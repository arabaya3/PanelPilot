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
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import ValidationError

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
        """
        ...


@dataclass
class InMemoryRateLimitStore:
    """A store backed by a dict, for a single process.

    Correct for one worker and useless across several — which is exactly why
    the port exists. A Redis adapter implementing the same method is a
    composition-root change; the domain does not learn about it.

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

    count = store.record_and_count(
        _trial_key(client_ip),
        now=now if now is not None else time.time(),
        window_seconds=window_seconds,
    )
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
