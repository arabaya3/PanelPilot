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

**Auth endpoints get their own, separate limits.** Starting a trial mints a
tenant with a fresh free allowance, and login and signup each cost a bcrypt
hash — so all three are throttled, but each in its own namespace with a limit
sized for what it protects. Sharing the trial-path counter would let a burst
of logins lock a site out of asking questions, and the reverse.

**A sliding window, not a fixed one.** A fixed window resets on a boundary, so
a burst spanning it gets double the allowance and a caller who learns the
boundary gets it reliably.

**A refused request is not recorded.** Only admitted requests enter the
window. Recording refusals too let a flooder keep its own window full forever —
and, behind a shared address, everyone else's with it — while the stored
history grew with the attempt rate rather than with the limit.

**The limiter fails open.** If the store cannot be reached the request is let
through and the outage is logged. Failing closed would turn a Redis blip into
every trial user on every site being refused at once, and the per-account
quota (BE-002) still bounds what any one account can spend meanwhile.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import redis
import structlog

from app.core.errors import TooManyRequestsError

logger = structlog.get_logger(__name__)

# Requests one source may make in the window. Sized for a shared site: a dozen
# engineers behind one NAT, each asking a handful of questions while working a
# fault, comfortably fits. An attacker is slowed to a crawl; a factory is not
# aware the limit exists.
TRIAL_REQUESTS_PER_WINDOW = 60
TRIAL_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class RateLimitPolicy:
    """One named limit: how many requests, over how long.

    Attributes:
        namespace: Prefix of every store key this policy counts under. Each
            policy has its own, so one endpoint's traffic can never spend
            another's allowance.
        limit: Requests permitted in the window.
        window_seconds: Window length.
    """

    namespace: str
    limit: int
    window_seconds: int


# The trial path: diagnosis, image upload, PLC. The namespace is the key prefix
# this limiter has always used, so a deploy does not reset live windows.
TRIAL_POLICY = RateLimitPolicy("trial", TRIAL_REQUESTS_PER_WINDOW, TRIAL_WINDOW_SECONDS)

# Starting or resuming a trial. Each start mints a tenant with its own free
# allowance, so an unthrottled start is an unthrottled free quota. Ten an hour
# covers several visitors from one site; a script is stopped at the eleventh.
TRIAL_START_POLICY = RateLimitPolicy("auth-trial", 10, 3600)

# Login, per source address. More generous than the per-account limit below:
# a whole shift can log in from one NAT'd address at the start of a day, and
# locking a site out of its own accounts is the failure this module refuses to
# cause.
LOGIN_IP_POLICY = RateLimitPolicy("auth-login-ip", 30, 900)

# Login, per account. Guessing one account's password from many addresses is
# invisible to a per-IP limit. Keyed by a hash of the address, so the store
# never holds a list of who tried to log in.
LOGIN_ACCOUNT_POLICY = RateLimitPolicy("auth-login-account", 10, 900)

# Signup, per source address. Each one is a bcrypt hash and a new tenant.
SIGNUP_POLICY = RateLimitPolicy("auth-signup", 10, 3600)

# Optimistic-transaction attempts before a contended key is treated as full.
# Contention on one key means other requests from that source are being
# admitted at that very moment, so refusing here errs in the safe direction.
_MAX_OPTIMISTIC_ATTEMPTS = 8

# Calls between sweeps of idle keys in the in-memory store.
_SWEEP_EVERY = 1024


@dataclass(frozen=True)
class WindowDecision:
    """What a store decided about one request.

    Attributes:
        allowed: Whether the request fits in the window. Only an admitted
            request is recorded.
        count: Requests in the window after the decision — including this
            one when it was admitted.
        oldest: Timestamp of the oldest request still in the window, or
            ``None`` when unknown. Tells a refused caller when a slot frees.
    """

    allowed: bool
    count: int
    oldest: float | None


class RateLimitStore(Protocol):
    """Somewhere request timestamps can be recorded and counted."""

    def record_if_allowed(
        self, key: str, *, now: float, window_seconds: int, limit: int
    ) -> WindowDecision:
        """Record a request only if the window has room for it.

        The check and the record are one atomic step. Split, concurrent
        callers could all read "room for one more" and all be admitted.

        Args:
            key: What is being limited.
            now: Current time, as a monotonic-ish epoch.
            window_seconds: How far back the window extends.
            limit: Requests permitted in the window.

        Returns:
            The decision, and the window as it stands afterwards.

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

    Locked, because FastAPI runs sync dependencies on a thread pool: unlocked,
    two threads could both read "room for one more" and both append.
    """

    _seen: dict[str, list[float]] = field(default_factory=dict)
    # When each key's newest entry leaves its window, so an idle key can be
    # swept without knowing which policy wrote it.
    _expires: dict[str, float] = field(default_factory=dict)
    _calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record_if_allowed(
        self, key: str, *, now: float, window_seconds: int, limit: int
    ) -> WindowDecision:
        """Record a request if the window has room, and report the window.

        Args:
            key: What is being limited.
            now: Current time.
            window_seconds: How far back the window extends.
            limit: Requests permitted in the window.

        Returns:
            The decision.
        """
        cutoff = now - window_seconds
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0:
                self._sweep(now)
            # Expired entries are dropped on access: a key under load is
            # cleaned every time it is used, and the periodic sweep catches
            # the keys nobody touches again.
            timestamps = [t for t in self._seen.get(key, []) if t > cutoff]
            allowed = len(timestamps) < limit
            if allowed:
                timestamps.append(now)
            if timestamps:
                self._seen[key] = timestamps
                self._expires[key] = max(timestamps) + window_seconds
            else:
                # An empty list kept for every source ever seen is unbounded
                # growth for no information.
                self._seen.pop(key, None)
                self._expires.pop(key, None)
            return WindowDecision(
                allowed=allowed,
                count=len(timestamps),
                oldest=min(timestamps) if timestamps else None,
            )

    def _sweep(self, now: float) -> None:
        """Forget every key whose whole window has passed.

        Args:
            now: Current time. The caller holds the lock.
        """
        for key in [k for k, expires in self._expires.items() if expires <= now]:
            self._seen.pop(key, None)
            self._expires.pop(key, None)


class RedisRateLimitStore:
    """A sliding-window store shared by every worker, backed by Redis.

    Each key is a sorted set of request timestamps. The count and the record
    are one optimistic transaction (``WATCH``/``MULTI``): if another worker
    writes the same key between our count and our write, the write is
    discarded and the decision is taken again, so two workers can never both
    admit the last slot. A Lua script would do it in one round trip, but the
    Redis double the tests run against cannot execute Lua, and an atomicity
    guarantee that cannot be tested is one nobody knows still holds.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Wrap a Redis client.

        Args:
            client: A connected client. Built by the composition root, with
                short socket timeouts, so an unreachable Redis costs a request
                a fraction of a second rather than hanging it.
        """
        self._client = client

    def record_if_allowed(
        self, key: str, *, now: float, window_seconds: int, limit: int
    ) -> WindowDecision:
        """Record a request if the window has room, and report the window.

        Args:
            key: What is being limited.
            now: Current time, as a Unix epoch.
            window_seconds: How far back the window extends.
            limit: Requests permitted in the window.

        Returns:
            The decision.

        Raises:
            RateLimitStoreUnavailableError: If Redis cannot be reached or
                rejects the transaction.
        """
        cutoff = now - window_seconds
        # Exclusive of the cutoff, matching the in-memory store's `t > cutoff`
        # survivors, so the two stores enforce the same window exactly.
        live_from = f"({cutoff}"
        # Unique per request, not the timestamp alone: two requests in the
        # same microsecond would otherwise collapse into one set member and
        # be counted once.
        member = f"{now}:{uuid.uuid4().hex}"
        try:
            with self._client.pipeline(transaction=True) as pipe:
                for _ in range(_MAX_OPTIMISTIC_ATTEMPTS):
                    try:
                        pipe.watch(key)  # type: ignore[no-untyped-call]
                        count = int(pipe.zcount(key, live_from, "+inf"))
                        first = pipe.zrangebyscore(
                            key, live_from, "+inf", start=0, num=1, withscores=True
                        )
                        oldest = float(first[0][1]) if first else None
                        if count >= limit:
                            # Refused, so nothing is written; see the module
                            # docstring on why a refusal must not be recorded.
                            pipe.unwatch()
                            return WindowDecision(allowed=False, count=count, oldest=oldest)
                        pipe.multi()
                        pipe.zremrangebyscore(key, "-inf", cutoff)
                        pipe.zadd(key, {member: now})
                        # A key nobody touches for a whole window expires on
                        # its own, so a source seen once does not occupy
                        # memory forever.
                        pipe.pexpire(key, window_seconds * 1000)
                        pipe.execute()
                        return WindowDecision(
                            allowed=True,
                            count=count + 1,
                            oldest=oldest if oldest is not None else now,
                        )
                    except redis.WatchError:
                        # Another worker wrote this key between our count and
                        # our write. Decide again against what it wrote.
                        continue
        except redis.RedisError as exc:
            raise RateLimitStoreUnavailableError(
                f"rate-limit store unreachable: {type(exc).__name__}"
            ) from exc
        return WindowDecision(allowed=False, count=limit, oldest=None)


class RateLimitExceededError(TooManyRequestsError):
    """Raised when a source has exhausted its window.

    A ``TooManyRequestsError``, so the central handler answers 429 with a
    ``Retry-After`` header. It was once a ``ValidationError``, which answered
    422 — telling a well-behaved client its request was malformed, and giving
    it no number to wait for.
    """


def check_rate_limit(
    *,
    store: RateLimitStore,
    policy: RateLimitPolicy,
    subject: str,
    message: str,
    now: float | None = None,
) -> int:
    """Admit one request under a policy, or refuse it.

    Args:
        store: Where request history lives.
        policy: The limit to apply.
        subject: What is limited within the policy's namespace, e.g.
            ``ip:203.0.113.10``. An empty subject is let through uncounted;
            see ``check_trial_rate_limit`` for why.
        message: What a refused caller is told, before the wait is appended.
        now: Current time; defaults to now. Injectable so a test can advance
            the clock rather than sleep through a window.

    Returns:
        How many requests this subject has made in the window, including this
        one. Zero when the request was allowed without being counted — no
        subject, or a store that could not be reached.

    Raises:
        RateLimitExceededError: If this request exceeds the limit. It carries
            the seconds until a slot frees, and the message says so too,
            because "too many requests" without a number invites immediate
            retrying, which is the behaviour the limit is trying to stop.
    """
    if not subject:
        return 0

    moment = now if now is not None else time.time()
    try:
        decision = store.record_if_allowed(
            f"{policy.namespace}:{subject}",
            now=moment,
            window_seconds=policy.window_seconds,
            limit=policy.limit,
        )
    except RateLimitStoreUnavailableError as exc:
        # Fail open; see the module docstring. Logged as a warning because a
        # limiter that is silently off is one nobody notices is off. The
        # subject is deliberately not logged: it is personal data, and the
        # outage is the same whichever source hit it.
        logger.warning("rate_limit.store_unavailable", error=str(exc))
        return 0
    if not decision.allowed:
        retry_after = _retry_after_seconds(decision, policy=policy, now=moment)
        minutes = max(1, math.ceil(retry_after / 60))
        unit = "minute" if minutes == 1 else "minutes"
        raise RateLimitExceededError(
            f"{message} — please wait {minutes} {unit} and try again",
            retry_after_seconds=retry_after,
        )
    return decision.count


def _retry_after_seconds(decision: WindowDecision, *, policy: RateLimitPolicy, now: float) -> int:
    """Return how long until the oldest request in the window leaves it.

    Args:
        decision: The refusal.
        policy: The limit that refused it.
        now: Current time.

    Returns:
        Whole seconds, at least one. Rounded up, so a client that waits
        exactly this long is not refused again for a fraction of a second.
    """
    if decision.oldest is None:
        # Refused on contention rather than on a full window: a slot is being
        # taken right now, so a short wait is the honest answer.
        return 1
    return max(1, math.ceil(decision.oldest + policy.window_seconds - now))


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
        RateLimitExceededError: If this request exceeds the limit.
    """
    # An unattributable request cannot be limited by source. Refusing would
    # break any deployment whose proxy does not forward the address; counting
    # it under one shared key would let one caller exhaust everyone's
    # allowance. So `_ip_subject` yields no subject, the request is let
    # through, and the per-account quota remains the backstop.
    return check_rate_limit(
        store=store,
        policy=RateLimitPolicy(TRIAL_POLICY.namespace, limit, window_seconds),
        subject=_ip_subject(client_ip),
        message="too many requests from this network",
        now=now,
    )


def check_trial_start_rate_limit(
    *, store: RateLimitStore, client_ip: str, now: float | None = None
) -> int:
    """Throttle starting or resuming a trial, per source address.

    Args:
        store: Where request history lives.
        client_ip: The source address.
        now: Current time; defaults to now.

    Returns:
        Requests from this source in the window, including this one.

    Raises:
        RateLimitExceededError: If this source has started too many trials.
    """
    return check_rate_limit(
        store=store,
        policy=TRIAL_START_POLICY,
        subject=_ip_subject(client_ip),
        message="too many trials started from this network",
        now=now,
    )


def check_signup_rate_limit(
    *, store: RateLimitStore, client_ip: str, now: float | None = None
) -> int:
    """Throttle account creation, per source address.

    Args:
        store: Where request history lives.
        client_ip: The source address.
        now: Current time; defaults to now.

    Returns:
        Requests from this source in the window, including this one.

    Raises:
        RateLimitExceededError: If this source has signed up too often.
    """
    return check_rate_limit(
        store=store,
        policy=SIGNUP_POLICY,
        subject=_ip_subject(client_ip),
        message="too many sign-ups from this network",
        now=now,
    )


def check_login_rate_limit(
    *, store: RateLimitStore, client_ip: str, email: str, now: float | None = None
) -> None:
    """Throttle login attempts, per source address and per account.

    Two limits because there are two attacks. Many passwords from one address
    is caught by the first; one account's password guessed from many
    addresses is caught only by the second.

    Args:
        store: Where request history lives.
        client_ip: The source address.
        email: The address being logged into, as submitted.
        now: Current time; defaults to now.

    Raises:
        RateLimitExceededError: If either limit is exhausted.
    """
    check_rate_limit(
        store=store,
        policy=LOGIN_IP_POLICY,
        subject=_ip_subject(client_ip),
        message="too many sign-in attempts from this network",
        now=now,
    )
    check_rate_limit(
        store=store,
        policy=LOGIN_ACCOUNT_POLICY,
        subject=_account_subject(email),
        message="too many sign-in attempts for this account",
        now=now,
    )


def _ip_subject(client_ip: str) -> str:
    """Return the subject for a source address, or empty when there is none.

    Args:
        client_ip: The source address.

    Returns:
        The subject, e.g. ``ip:203.0.113.10``.
    """
    return f"ip:{client_ip}" if client_ip else ""


def _account_subject(email: str) -> str:
    """Return the subject for an account without storing the address itself.

    Args:
        email: The address as submitted. Normalised the way login normalises
            it, so ``Bob@x`` and ``bob@x `` share one allowance.

    Returns:
        ``email:<sha256 hex>``, or empty when no address was given.
    """
    normalised = email.strip().lower()
    if not normalised:
        return ""
    return f"email:{hashlib.sha256(normalised.encode('utf-8')).hexdigest()}"
