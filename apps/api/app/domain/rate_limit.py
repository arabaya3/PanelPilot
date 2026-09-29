"""Protecting the free trial without punishing legitimate users.

The trial is the self-serve differentiator, and it is trivially abusable if
nothing stops one source creating accounts in a loop. But the protection is
worth less than the thing it protects: a limit that blocks a real engineer on
their first call-out has cost more than the abuse it prevented.

**Two limits, deliberately different in kind.** The per-account limit is
BE-002's quota — a hard count, enforced under a row lock. This module adds a
per-IP sliding window to catch the case that quota cannot see: one source
creating many accounts, each with its own untouched free allowance.

**The IP limit is generous on purpose.** A factory or a service company sits
behind one NAT'd address, and a dozen engineers sharing it is ordinary rather
than suspicious. The threshold is set from that scenario, not from a
single-user assumption — because the failure it prevents (a slow attacker) is
recoverable, and the failure it would cause (a whole site locked out mid-fault)
is not.

**Trial paths only.** Authenticated paying usage is not subject to an IP
ceiling at all. A large customer's whole estate can share one egress address,
and rate-limiting them by IP would throttle the people paying for the service.

**A sliding window, not a fixed one.** A fixed window resets on a boundary, so
a burst spanning it gets double the allowance and a caller who learns the
boundary gets it reliably.

**The limiter fails open.** If the store cannot be reached the request is let
through and the outage is logged. Failing closed would turn a Redis blip into
every trial user on every site being refused at once, and the per-account
quota (BE-002) still bounds what any one account can spend meanwhile.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import redis
import structlog

from app.core.errors import ValidationError

logger = structlog.get_logger(__name__)

# Requests one source may make in the window. Sized for a shared site: a dozen
# engineers behind one NAT, each asking a handful of questions while working a
# fault, comfortably fits. An attacker is slowed to a crawl; a factory is not
# aware the limit exists.
TRIAL_REQUESTS_PER_WINDOW = 60
TRIAL_WINDOW_SECONDS = 300


class RateLimitStore(Protocol):
    """Somewhere request timestamps can be recorded and counted."""

    def record_and_count(self, key: str, *, now: float, window_seconds: int) -> int:
        """Record a request and return how many fall inside the window.

        Args:
            key: What is being limited.
            now: Current time, as a monotonic-ish epoch.
            window_seconds: How far back the window extends.

        Returns:
            The number of requests in the window, including this one.

        Raises:
            RateLimitStoreUnavailableError: If the store cannot be reached.
        """
        ...


class RateLimitStoreUnavailableError(RuntimeError):
    """Raised by a store that cannot record or count right now.

    Store-agnostic on purpose: the fail-open decision below is policy, and it
    should not need to know which client library an adapter happens to use.
    """


@dataclass
class InMemoryRateLimitStore:
    """A store backed by a dict, for a single process.

    Correct for one worker and useless across several — which is exactly why
    the port exists. ``RedisRateLimitStore`` implements the same method, and
    which one is used is a composition-root choice (``RATE_LIMIT_BACKEND``).

    Deliberately not the production default: a multi-worker deployment using
    this would give each worker its own allowance, so the effective limit is
    the configured one times the worker count.
    """

    _seen: dict[str, list[float]] = field(default_factory=dict)

    def record_and_count(self, key: str, *, now: float, window_seconds: int) -> int:
        """Record a request and count the window.

        Args:
            key: What is being limited.
            now: Current time.
            window_seconds: How far back the window extends.

        Returns:
            Requests in the window, including this one.
        """
        cutoff = now - window_seconds
        # Expired entries are dropped on access rather than by a sweep: a key
        # nobody touches costs nothing, and a key under load is cleaned every
        # time it is used.
        timestamps = [t for t in self._seen.get(key, []) if t > cutoff]
        timestamps.append(now)
        self._seen[key] = timestamps
        return len(timestamps)


class RedisRateLimitStore:
    """A sliding-window store shared by every worker, backed by Redis.

    Each key is a sorted set of request timestamps. Trimming, recording and
    counting run in one ``MULTI`` transaction, so two workers counting the same
    source at the same moment each see the other's request rather than both
    reading a stale count and both letting a request through.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Wrap a Redis client.

        Args:
            client: A connected client. Built by the composition root, with
                short socket timeouts, so an unreachable Redis costs a request
                a fraction of a second rather than hanging it.
        """
        self._client = client

    def record_and_count(self, key: str, *, now: float, window_seconds: int) -> int:
        """Record a request and count the window.

        Args:
            key: What is being limited.
            now: Current time, as a Unix epoch.
            window_seconds: How far back the window extends.

        Returns:
            Requests in the window, including this one.

        Raises:
            RateLimitStoreUnavailableError: If Redis cannot be reached or
                rejects the transaction.
        """
        # Unique per request, not the timestamp alone: two requests in the
        # same microsecond would otherwise collapse into one set member and
        # be counted once.
        member = f"{now}:{uuid.uuid4().hex}"
        pipeline = self._client.pipeline(transaction=True)
        # Inclusive of the cutoff, matching the in-memory store's `t > cutoff`
        # survivors, so the two stores enforce the same window exactly.
        pipeline.zremrangebyscore(key, "-inf", now - window_seconds)
        pipeline.zadd(key, {member: now})
        pipeline.zcard(key)
        # A key nobody touches for a whole window expires on its own, so a
        # source seen once does not occupy memory forever.
        pipeline.expire(key, window_seconds)
        try:
            results = pipeline.execute()
        except redis.RedisError as exc:
            raise RateLimitStoreUnavailableError(
                f"rate-limit store unreachable: {type(exc).__name__}"
            ) from exc
        return int(results[2])


class RateLimitExceededError(ValidationError):
    """Raised when a source has exhausted its window.

    A subclass of ``ValidationError`` so the existing handler renders it
    without a new mapping — the caller sent too many requests, which is a
    problem with the request rather than with the server.
    """


def check_trial_rate_limit(
    *,
    store: RateLimitStore,
    client_ip: str,
    now: float | None = None,
    limit: int = TRIAL_REQUESTS_PER_WINDOW,
    window_seconds: int = TRIAL_WINDOW_SECONDS,
) -> int:
    """Record a trial request and refuse it if the source is over its limit.

    Args:
        store: Where request history lives.
        client_ip: The source address.
        now: Current time; defaults to now. Injectable so a test can advance
            the clock rather than sleep through a five-minute window.
        limit: Requests permitted in the window.
        window_seconds: Window length.

    Returns:
        How many requests this source has made in the window, including this
        one. Returned rather than discarded so a caller can log how close a
        legitimate user came — a limit nobody can see approaching is one that
        surprises somebody.

        Zero when the request was allowed without being counted — no source
        address, or a store that could not be reached.

    Raises:
        RateLimitExceededError: If this request exceeds the limit. The message
            says how long to wait, because "too many requests" without a
            number invites immediate retrying, which is the behaviour the
            limit is trying to stop.
    """
    if not client_ip:
        # An unattributable request cannot be limited by source. Refusing
        # would break any deployment whose proxy does not forward the address;
        # counting it under one shared key would let one caller exhaust
        # everyone's allowance. So it is allowed through and the per-account
        # quota remains the backstop.
        return 0

    try:
        count = store.record_and_count(
            _trial_key(client_ip),
            now=now if now is not None else time.time(),
            window_seconds=window_seconds,
        )
    except RateLimitStoreUnavailableError as exc:
        # Fail open; see the module docstring. Logged as a warning because a
        # limiter that is silently off is one nobody notices is off. The
        # address is deliberately not logged: it is personal data, and the
        # outage is the same whichever source hit it.
        logger.warning("rate_limit.store_unavailable", error=str(exc))
        return 0
    if count > limit:
        raise RateLimitExceededError(
            f"too many requests from this network — please wait "
            f"{window_seconds // 60} minutes and try again"
        )
    return count


def _trial_key(client_ip: str) -> str:
    """Namespace a key so trial limiting cannot collide with another counter.

    Args:
        client_ip: The source address.

    Returns:
        The store key.
    """
    return f"trial:ip:{client_ip}"
