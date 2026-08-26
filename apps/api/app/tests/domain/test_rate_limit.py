"""Tests for `app/domain/rate_limit.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The acceptance criterion has two halves and the second is the harder one:
automated bursts are throttled, **and a normal user's trial flow is
unaffected**. A limit that catches the attacker by also catching a factory
has failed, so the shared-site case is tested as carefully as the abuse case.
"""

from __future__ import annotations

import contextlib

import pytest

from app.domain.rate_limit import (
    TRIAL_REQUESTS_PER_WINDOW,
    TRIAL_WINDOW_SECONDS,
    InMemoryRateLimitStore,
    RateLimitExceededError,
    check_trial_rate_limit,
)

_IP = "203.0.113.10"
_OTHER_IP = "203.0.113.99"


@pytest.fixture
def store() -> InMemoryRateLimitStore:
    return InMemoryRateLimitStore()


def _request(store: InMemoryRateLimitStore, ip: str = _IP, *, at: float = 1000.0) -> int:
    return check_trial_rate_limit(store=store, client_ip=ip, now=at)


# --- the burst is throttled --------------------------------------------------


def test_a_burst_is_throttled(store: InMemoryRateLimitStore) -> None:
    """The acceptance criterion's first half."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n * 0.01)

    with pytest.raises(RateLimitExceededError):
        _request(store, at=1000.0 + TRIAL_REQUESTS_PER_WINDOW * 0.01)


def test_the_request_at_the_limit_is_allowed(store: InMemoryRateLimitStore) -> None:
    """The limit is a ceiling, not a fence one short of it."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW - 1):
        _request(store, at=1000.0 + n * 0.01)
    assert _request(store, at=1050.0) == TRIAL_REQUESTS_PER_WINDOW


def test_the_message_says_how_long_to_wait(store: InMemoryRateLimitStore) -> None:
    """Say how long to wait, not just that they waited too little.

    "Too many requests" without a number invites immediate retrying, which is
    the behaviour the limit exists to stop.
    """
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n * 0.01)

    with pytest.raises(RateLimitExceededError, match="minutes"):
        _request(store, at=1001.0)


# --- a normal flow is unaffected ---------------------------------------------


def test_a_single_engineer_never_notices(store: InMemoryRateLimitStore) -> None:
    """The acceptance criterion's second half, and the harder one.

    Someone working one fault: a dozen questions over half an hour. A limit
    that fires here has cost more than the abuse it prevents.
    """
    for minute in range(30):
        count = _request(store, at=1000.0 + minute * 60)
        assert count <= TRIAL_REQUESTS_PER_WINDOW


def test_a_shared_site_is_not_locked_out(store: InMemoryRateLimitStore) -> None:
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


def test_the_window_slides_rather_than_resetting(store: InMemoryRateLimitStore) -> None:
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


def test_old_requests_stop_counting(store: InMemoryRateLimitStore) -> None:
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        _request(store, at=1000.0 + n)
    later = 1000.0 + TRIAL_WINDOW_SECONDS + TRIAL_REQUESTS_PER_WINDOW + 1
    assert _request(store, at=later) == 1


# --- sources are counted separately ------------------------------------------


def test_one_source_cannot_exhaust_anothers_allowance(
    store: InMemoryRateLimitStore,
) -> None:
    """Otherwise a single attacker locks out every other customer."""
    for n in range(TRIAL_REQUESTS_PER_WINDOW + 5):
        with contextlib.suppress(RateLimitExceededError):
            _request(store, _IP, at=1000.0 + n * 0.01)

    assert _request(store, _OTHER_IP, at=1000.0) == 1


# --- an unattributable request -----------------------------------------------


def test_a_request_with_no_address_is_allowed(store: InMemoryRateLimitStore) -> None:
    """Refusing would break any deployment whose proxy does not forward it.

    Counting them under one shared key would be worse: one caller could then
    exhaust everybody's allowance. The per-account quota remains the backstop.
    """
    for _ in range(TRIAL_REQUESTS_PER_WINDOW * 2):
        assert check_trial_rate_limit(store=store, client_ip="", now=1000.0) == 0


# --- the store ---------------------------------------------------------------


def test_the_store_counts_within_a_window() -> None:
    store = InMemoryRateLimitStore()
    assert store.record_and_count("k", now=100.0, window_seconds=60) == 1
    assert store.record_and_count("k", now=110.0, window_seconds=60) == 2


def test_the_store_forgets_expired_entries() -> None:
    """Expired entries are dropped on access.

    A key under load is cleaned every time it is used, so nothing grows
    without bound and no sweep is needed.
    """
    store = InMemoryRateLimitStore()
    store.record_and_count("k", now=100.0, window_seconds=60)
    assert store.record_and_count("k", now=200.0, window_seconds=60) == 1


def test_keys_are_independent() -> None:
    store = InMemoryRateLimitStore()
    store.record_and_count("a", now=100.0, window_seconds=60)
    assert store.record_and_count("b", now=100.0, window_seconds=60) == 1
