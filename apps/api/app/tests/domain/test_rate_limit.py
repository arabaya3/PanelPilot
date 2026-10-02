"""Tests for `app/domain/rate_limit.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The acceptance criterion has two halves and the second is the harder one:
automated bursts are throttled, **and a normal user's trial flow is
unaffected**. A limit that catches the attacker by also catching a factory
has failed, so the shared-site case is tested as carefully as the abuse case.
"""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fakeredis
import pytest
import redis

from app.core.errors import TooManyRequestsError, ValidationError
from app.domain import rate_limit as rate_limit_domain
from app.domain.rate_limit import (
    DESIGN_POLICY,
    LOGIN_ACCOUNT_POLICY,
    LOGIN_IP_POLICY,
    SIGNUP_POLICY,
    TRIAL_REQUESTS_PER_WINDOW,
    TRIAL_START_POLICY,
    TRIAL_WINDOW_SECONDS,
    InMemoryRateLimitStore,
    RateLimitExceededError,
    RateLimitPolicy,
    RateLimitStore,
    RateLimitStoreUnavailableError,
    RedisRateLimitStore,
    WindowDecision,
    check_design_rate_limit,
    check_login_rate_limit,
    check_signup_rate_limit,
    check_trial_rate_limit,
    check_trial_start_rate_limit,
)

_IP = "203.0.113.10"
_OTHER_IP = "203.0.113.99"


def _redis_store() -> RedisRateLimitStore:
    return RedisRateLimitStore(fakeredis.FakeRedis())


@pytest.fixture(params=["memory", "redis"])
def store(request: pytest.FixtureRequest) -> RateLimitStore:
    """Every behavioural test runs against both stores.

    The two must enforce the same window exactly — a deployment switching
    backend should not change who gets throttled.
    """
    if request.param == "redis":
        return _redis_store()
    return InMemoryRateLimitStore()


def _request(store: RateLimitStore, ip: str = _IP, *, at: float = 1000.0) -> int:
    return check_trial_rate_limit(store=store, client_ip=ip, now=at)


def _count(store: RateLimitStore, key: str, *, now: float, limit: int = 1000) -> int:
    """Record one request under a generous limit and return the window count."""
    return store.record_if_allowed(key, now=now, window_seconds=60, limit=limit).count


# --- the burst is throttled --------------------------------------------------


def test_a_burst_is_throttled(store: RateLimitStore) -> None:
    """The acceptance criterion's first half."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n * 0.01)

    with pytest.raises(RateLimitExceededError):
        _request(store, at=1000.0 + TRIAL_REQUESTS_PER_WINDOW * 0.01)


def test_the_request_at_the_limit_is_allowed(store: RateLimitStore) -> None:
    """The limit is a ceiling, not a fence one short of it."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW - 1):
        _request(store, at=1000.0 + n * 0.01)
    assert _request(store, at=1050.0) == TRIAL_REQUESTS_PER_WINDOW


def test_the_message_says_how_long_to_wait(store: RateLimitStore) -> None:
    """Say how long to wait, not just that they waited too little.

    "Too many requests" without a number invites immediate retrying, which is
    the behaviour the limit exists to stop.
    """
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n * 0.01)

    with pytest.raises(RateLimitExceededError, match="minutes"):
        _request(store, at=1001.0)


# --- a normal flow is unaffected ---------------------------------------------


def test_a_single_engineer_never_notices(store: RateLimitStore) -> None:
    """The acceptance criterion's second half, and the harder one.

    Someone working one fault: a dozen questions over half an hour. A limit
    that fires here has cost more than the abuse it prevents.
    """
    for minute in range(30):
        count = _request(store, at=1000.0 + minute * 60)
        assert count <= TRIAL_REQUESTS_PER_WINDOW


def test_a_shared_site_is_not_locked_out(store: RateLimitStore) -> None:
    """A dozen engineers behind one NAT'd address is ordinary, not suspicious.

    The threshold is set from this scenario rather than a single-user one,
    because a whole site locked out mid-fault is not a recoverable failure.
    """
    engineers = 12
    questions_each = 4
    for engineer in range(engineers):
        for question in range(questions_each):
            _request(store, at=1000.0 + engineer * 5 + question)

    # Still working after 48 requests from one address.
    assert _request(store, at=1100.0) <= TRIAL_REQUESTS_PER_WINDOW


def test_the_threshold_is_set_for_shared_addresses() -> None:
    """Pinned so a future tightening is a deliberate decision.

    Dropping this to a single-user number would lock out every customer
    behind a NAT, and the failure would look like the product being broken.
    """
    assert TRIAL_REQUESTS_PER_WINDOW >= 40


# --- the window slides -------------------------------------------------------


def test_the_window_slides_rather_than_resetting(store: RateLimitStore) -> None:
    """A fixed window gives a burst spanning its boundary double the allowance.

    A caller who learns the boundary gets that reliably, which makes the
    limit worth roughly half what it claims.
    """
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n)

    # One second later, still inside the window: refused.
    with pytest.raises(RateLimitExceededError):
        _request(store, at=1000.0 + TRIAL_REQUESTS_PER_WINDOW)

    # Once the earliest requests age out, allowed again.
    assert _request(store, at=1000.0 + TRIAL_WINDOW_SECONDS + 1)


def test_old_requests_stop_counting(store: RateLimitStore) -> None:
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n)
    later = 1000.0 + TRIAL_WINDOW_SECONDS + TRIAL_REQUESTS_PER_WINDOW + 1
    assert _request(store, at=later) == 1


# --- sources are counted separately ------------------------------------------


def test_one_source_cannot_exhaust_anothers_allowance(
    store: RateLimitStore,
) -> None:
    """Otherwise a single attacker locks out every other customer."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW + 5):
        with contextlib.suppress(RateLimitExceededError):
            _request(store, _IP, at=1000.0 + n * 0.01)

    assert _request(store, _OTHER_IP, at=1000.0) == 1


# --- an unattributable request -----------------------------------------------


def test_a_request_with_no_address_is_allowed(store: RateLimitStore) -> None:
    """Refusing would break any deployment whose proxy does not forward it.

    Counting them under one shared key would be worse: one caller could then
    exhaust everybody's allowance. The per-account quota remains the backstop.
    """
    for _ in range(TRIAL_REQUESTS_PER_WINDOW * 2):
        assert check_trial_rate_limit(store=store, client_ip="", now=1000.0) == 0


# --- the store ---------------------------------------------------------------


def test_the_store_counts_within_a_window(store: RateLimitStore) -> None:
    assert _count(store, "k", now=100.0) == 1
    assert _count(store, "k", now=110.0) == 2


def test_the_store_forgets_expired_entries(store: RateLimitStore) -> None:
    """Expired entries are dropped on access.

    A key under load is cleaned every time it is used, so nothing grows
    without bound and no sweep is needed.
    """
    _count(store, "k", now=100.0)
    assert _count(store, "k", now=200.0) == 1


def test_a_request_exactly_one_window_old_no_longer_counts(store: RateLimitStore) -> None:
    """The boundary is exclusive in both stores, so they agree to the second."""
    _count(store, "k", now=100.0)
    assert _count(store, "k", now=160.0) == 1


def test_keys_are_independent(store: RateLimitStore) -> None:
    _count(store, "a", now=100.0)
    assert _count(store, "b", now=100.0) == 1


# --- the Redis store ----------------------------------------------------------


def test_the_redis_store_shares_one_window_between_workers() -> None:
    """The reason it exists: two workers, one count."""
    server = fakeredis.FakeServer()
    worker_a = RedisRateLimitStore(fakeredis.FakeRedis(server=server))
    worker_b = RedisRateLimitStore(fakeredis.FakeRedis(server=server))

    _count(worker_a, "k", now=1000.0)
    assert _count(worker_b, "k", now=1001.0) == 2


def test_simultaneous_requests_are_counted_separately() -> None:
    """Two requests at the same instant are two requests, not one set member."""
    store = _redis_store()
    _count(store, "k", now=1000.0)
    assert _count(store, "k", now=1000.0) == 2


def test_an_idle_key_expires_on_its_own() -> None:
    client = fakeredis.FakeRedis()
    _count(RedisRateLimitStore(client), "k", now=1000.0)

    ttl = client.ttl("k")
    assert isinstance(ttl, int)
    assert 0 < ttl <= 60


def test_an_unreachable_redis_is_reported_as_unavailable() -> None:
    """Wrapped, so the policy above it needs no knowledge of the client library."""
    server = fakeredis.FakeServer()
    server.connected = False  # fakeredis: every command now raises ConnectionError
    client = fakeredis.FakeRedis(server=server)

    with pytest.raises(RateLimitStoreUnavailableError) as raised:
        _count(RedisRateLimitStore(client), "k", now=1000.0)
    assert isinstance(raised.value.__cause__, redis.RedisError)


# --- the limiter fails open ----------------------------------------------------


class _DownStore:
    def record_if_allowed(
        self, key: str, *, now: float, window_seconds: int, limit: int  # noqa: ARG002
    ) -> WindowDecision:
        raise RateLimitStoreUnavailableError("down")


def test_an_unavailable_store_lets_the_request_through() -> None:
    """A Redis blip must not refuse every trial user on every site at once."""
    assert _request(_DownStore()) == 0


def test_an_unavailable_store_is_logged_without_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, dict[str, Any]]] = []

    class _Logger:
        def warning(self, event: str, **fields: Any) -> None:
            logged.append((event, fields))

    monkeypatch.setattr(rate_limit_domain, "logger", _Logger())

    _request(_DownStore())

    assert [event for event, _ in logged] == ["rate_limit.store_unavailable"]
    assert _IP not in repr(logged)


# --- a refusal is not recorded -------------------------------------------------


def test_refused_requests_are_not_recorded_in_redis() -> None:
    """A flooder must not keep its own window full forever.

    Recording refusals meant a source hammering at the limit never aged out —
    and behind a shared address, neither did anyone else — while the sorted
    set grew with the attempt rate rather than with the limit.
    """
    client = fakeredis.FakeRedis()
    store = RedisRateLimitStore(client)
    for n in range(200):
        with contextlib.suppress(RateLimitExceededError):
            check_trial_rate_limit(store=store, client_ip=_IP, now=1000.0 + n * 0.01)

    assert client.zcard(f"trial:ip:{_IP}") == TRIAL_REQUESTS_PER_WINDOW


def test_refused_requests_are_not_recorded_in_memory() -> None:
    store = InMemoryRateLimitStore()
    for n in range(200):
        with contextlib.suppress(RateLimitExceededError):
            _request(store, at=1000.0 + n * 0.01)

    assert len(store._seen[f"trial:ip:{_IP}"]) == TRIAL_REQUESTS_PER_WINDOW


def test_a_flooder_regains_access_once_its_admitted_requests_age_out(
    store: RateLimitStore,
) -> None:
    """Only the admitted requests hold the window, so it drains on schedule."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW + 100):
        with contextlib.suppress(RateLimitExceededError):
            _request(store, at=1000.0 + n * 0.5)

    # The first admitted request was at 1000.0; just past a window later the
    # source is let back in, however hard it kept knocking in between.
    assert _request(store, at=1000.0 + TRIAL_WINDOW_SECONDS + 0.1) >= 1


# --- Retry-After -----------------------------------------------------------------


def test_a_refusal_says_when_the_oldest_request_leaves_the_window(
    store: RateLimitStore,
) -> None:
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n)

    with pytest.raises(RateLimitExceededError) as refused:
        _request(store, at=1100.0)

    # The oldest request (t=1000) leaves the 300 s window at t=1300.
    assert refused.value.retry_after_seconds == 200


def test_a_refusal_is_a_too_many_requests_error_not_a_validation_error() -> None:
    """422 told a client its payload was wrong; 429 tells it to slow down."""
    assert issubclass(RateLimitExceededError, TooManyRequestsError)
    assert not issubclass(RateLimitExceededError, ValidationError)


# --- the in-memory store's housekeeping ------------------------------------------


def test_an_emptied_key_is_dropped() -> None:
    """A key whose window drained is not kept around as an empty list."""
    store = InMemoryRateLimitStore()
    store.record_if_allowed("k", now=100.0, window_seconds=60, limit=0)
    assert "k" not in store._seen


def test_idle_keys_are_swept() -> None:
    """Sources never seen again must not accumulate for the process lifetime."""
    store = InMemoryRateLimitStore()
    for n in range(500):
        _count(store, f"source-{n}", now=100.0)
    # Far past every window: the periodic sweep forgets them all.
    for _ in range(1024):
        _count(store, "busy", now=10_000.0)
    assert set(store._seen) == {"busy"}


def test_the_in_memory_store_admits_exactly_the_limit_under_threads() -> None:
    """Concurrent threads admit exactly the limit.

    FastAPI runs sync dependencies on a thread pool; the lock is what makes the
    check-and-record one step there.
    """
    store = InMemoryRateLimitStore()
    with ThreadPoolExecutor(max_workers=16) as pool:
        decisions = list(
            pool.map(
                lambda _: store.record_if_allowed("k", now=100.0, window_seconds=60, limit=25),
                range(200),
            )
        )
    assert sum(d.allowed for d in decisions) == 25


# --- the Redis store under contention --------------------------------------------


def test_a_concurrent_write_makes_the_redis_store_decide_again() -> None:
    """Optimistic concurrency: a write between our count and ours is retried.

    Simulated by having another worker take the last slot the first time our
    transaction is about to commit.
    """
    server = fakeredis.FakeServer()
    ours = fakeredis.FakeRedis(server=server)
    theirs = RedisRateLimitStore(fakeredis.FakeRedis(server=server))
    for n in range(2):
        theirs.record_if_allowed("k", now=100.0 + n, window_seconds=60, limit=3)

    original = ours.pipeline
    interfered = False

    def _pipeline(*args: Any, **kwargs: Any) -> Any:
        pipe = original(*args, **kwargs)
        real_multi = pipe.multi

        def _multi() -> None:
            nonlocal interfered
            if not interfered:
                interfered = True
                theirs.record_if_allowed("k", now=103.0, window_seconds=60, limit=3)
            real_multi()

        pipe.multi = _multi  # type: ignore[method-assign]
        return pipe

    ours.pipeline = _pipeline  # type: ignore[method-assign]

    decision = RedisRateLimitStore(ours).record_if_allowed(
        "k", now=104.0, window_seconds=60, limit=3
    )

    # The other worker took the third slot; the retry sees it and refuses.
    assert interfered
    assert not decision.allowed
    assert ours.zcard("k") == 3


# --- the auth limits -----------------------------------------------------------


def test_each_auth_limit_counts_in_its_own_namespace() -> None:
    """A burst of logins must not spend the budget for starting a trial."""
    store = InMemoryRateLimitStore()
    for n in range(LOGIN_IP_POLICY.limit):
        check_login_rate_limit(store=store, client_ip=_IP, email=f"u{n}@x.test", now=1000.0)

    assert check_trial_start_rate_limit(store=store, client_ip=_IP, now=1000.0) == 1
    assert check_signup_rate_limit(store=store, client_ip=_IP, now=1000.0) == 1
    assert _request(store, at=1000.0) == 1


@pytest.mark.parametrize(
    ("check", "policy"),
    [
        (check_trial_start_rate_limit, TRIAL_START_POLICY),
        (check_signup_rate_limit, SIGNUP_POLICY),
        (check_design_rate_limit, DESIGN_POLICY),
    ],
)
def test_trial_start_and_signup_are_limited_per_address(
    check: Any, policy: RateLimitPolicy
) -> None:
    store = InMemoryRateLimitStore()
    for _ in range(policy.limit):
        check(store=store, client_ip=_IP, now=1000.0)

    with pytest.raises(RateLimitExceededError):
        check(store=store, client_ip=_IP, now=1000.0)
    # Another address is unaffected.
    assert check(store=store, client_ip=_OTHER_IP, now=1000.0) == 1


def test_login_is_limited_per_account_across_addresses() -> None:
    """Guessing one account's password from many addresses is still caught."""
    store = InMemoryRateLimitStore()
    for n in range(LOGIN_ACCOUNT_POLICY.limit):
        check_login_rate_limit(
            store=store, client_ip=f"198.51.100.{n}", email="victim@x.test", now=1000.0
        )

    with pytest.raises(RateLimitExceededError, match="this account"):
        check_login_rate_limit(
            store=store, client_ip="198.51.100.200", email=" Victim@X.test ", now=1000.0
        )


def test_login_is_limited_per_address_across_accounts() -> None:
    store = InMemoryRateLimitStore()
    for n in range(LOGIN_IP_POLICY.limit):
        check_login_rate_limit(store=store, client_ip=_IP, email=f"u{n}@x.test", now=1000.0)

    with pytest.raises(RateLimitExceededError, match="this network"):
        check_login_rate_limit(store=store, client_ip=_IP, email="fresh@x.test", now=1000.0)


def test_the_account_key_does_not_store_the_address() -> None:
    """The store holds a hash, so it never becomes a list of who tried to log in."""
    client = fakeredis.FakeRedis()
    check_login_rate_limit(
        store=RedisRateLimitStore(client), client_ip=_IP, email="someone@x.test", now=1000.0
    )
    keys = [str(key) for key in client.keys("*")]
    assert keys
    assert not any("someone" in key for key in keys)
