"""Tests for `app/domain/rate_limit.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The acceptance criterion has two halves and the second is the harder one:
automated bursts are throttled, **and a normal user's trial flow is
unaffected**. A limit that catches the attacker by also catching a factory
has failed, so the shared-site case is tested as carefully as the abuse case.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any

import fakeredis
import pytest
import redis

from app.domain import rate_limit as rate_limit_domain
from app.domain.rate_limit import (
    TRIAL_REQUESTS_PER_WINDOW,
    TRIAL_WINDOW_SECONDS,
    InMemoryRateLimitStore,
    RateLimitExceededError,
    RateLimitStore,
    RateLimitStoreUnavailableError,
    RedisRateLimitStore,
    check_trial_rate_limit,
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
    assert store.record_and_count("k", now=100.0, window_seconds=60) == 1
    assert store.record_and_count("k", now=110.0, window_seconds=60) == 2


def test_the_store_forgets_expired_entries(store: RateLimitStore) -> None:
    """Expired entries are dropped on access.

    A key under load is cleaned every time it is used, so nothing grows
    without bound and no sweep is needed.
    """
    store.record_and_count("k", now=100.0, window_seconds=60)
    assert store.record_and_count("k", now=200.0, window_seconds=60) == 1


def test_a_request_exactly_one_window_old_no_longer_counts(store: RateLimitStore) -> None:
    """The boundary is exclusive in both stores, so they agree to the second."""
    store.record_and_count("k", now=100.0, window_seconds=60)
    assert store.record_and_count("k", now=160.0, window_seconds=60) == 1


def test_keys_are_independent(store: RateLimitStore) -> None:
    store.record_and_count("a", now=100.0, window_seconds=60)
    assert store.record_and_count("b", now=100.0, window_seconds=60) == 1


# --- the Redis store ----------------------------------------------------------


def test_the_redis_store_shares_one_window_between_workers() -> None:
    """The reason it exists: two workers, one count."""
    server = fakeredis.FakeServer()
    worker_a = RedisRateLimitStore(fakeredis.FakeRedis(server=server))
    worker_b = RedisRateLimitStore(fakeredis.FakeRedis(server=server))

    worker_a.record_and_count("k", now=1000.0, window_seconds=60)
    assert worker_b.record_and_count("k", now=1001.0, window_seconds=60) == 2


def test_simultaneous_requests_are_counted_separately() -> None:
    """Two requests at the same instant are two requests, not one set member."""
    store = _redis_store()
    store.record_and_count("k", now=1000.0, window_seconds=60)
    assert store.record_and_count("k", now=1000.0, window_seconds=60) == 2


def test_an_idle_key_expires_on_its_own() -> None:
    client = fakeredis.FakeRedis()
    RedisRateLimitStore(client).record_and_count("k", now=1000.0, window_seconds=60)

    ttl = client.ttl("k")
    assert isinstance(ttl, int)
    assert 0 < ttl <= 60


def test_an_unreachable_redis_is_reported_as_unavailable() -> None:
    """Wrapped, so the policy above it needs no knowledge of the client library."""
    server = fakeredis.FakeServer()
    server.connected = False  # fakeredis: every command now raises ConnectionError
    client = fakeredis.FakeRedis(server=server)

    with pytest.raises(RateLimitStoreUnavailableError) as raised:
        RedisRateLimitStore(client).record_and_count("k", now=1000.0, window_seconds=60)
    assert isinstance(raised.value.__cause__, redis.RedisError)


# --- the limiter fails open ----------------------------------------------------


class _DownStore:
    def record_and_count(self, key: str, *, now: float, window_seconds: int) -> int:  # noqa: ARG002
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


# --- against a real Redis -------------------------------------------------------


def _real_redis() -> redis.Redis | None:
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
    except redis.RedisError:
        return None
    return client


requires_redis = pytest.mark.skipif(
    _real_redis() is None, reason="needs a reachable Redis; CI provides one as a service container"
)


@requires_redis
def test_two_workers_share_one_window_on_a_real_redis() -> None:
    """The property fakeredis can only simulate: MULTI on a real server."""
    client = _real_redis()
    assert client is not None
    key = f"trial:ip:test-{uuid.uuid4().hex}"
    worker_a = RedisRateLimitStore(redis.Redis(connection_pool=client.connection_pool))
    worker_b = RedisRateLimitStore(redis.Redis(connection_pool=client.connection_pool))
    try:
        for i in range(3):
            worker_a.record_and_count(key, now=1000.0 + i, window_seconds=60)
        assert worker_b.record_and_count(key, now=1003.0, window_seconds=60) == 4
        # Everything older than the window is trimmed.
        assert worker_b.record_and_count(key, now=1062.5, window_seconds=60) == 2
        ttl = client.ttl(key)
        assert isinstance(ttl, int)
        assert 0 < ttl <= 60
    finally:
        client.delete(key)


@requires_redis
def test_a_burst_is_throttled_on_a_real_redis() -> None:
    client = _real_redis()
    assert client is not None
    store = RedisRateLimitStore(client)
    ip = f"198.51.100.{uuid.uuid4().int % 250}"
    try:
        for _ in range(TRIAL_REQUESTS_PER_WINDOW):
            check_trial_rate_limit(store=store, client_ip=ip, now=5000.0)
        with pytest.raises(RateLimitExceededError):
            check_trial_rate_limit(store=store, client_ip=ip, now=5000.0)
    finally:
        client.delete(f"trial:ip:{ip}")
