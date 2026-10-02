"""FastAPI dependencies shared by all route modules.

Dependencies resolve *inputs* (session, caller, clients). They must not contain
business logic — that belongs in ``app.domain``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

import redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import RateLimitBackend, get_settings
from app.core.db import get_session
from app.core.security import decode_access_token
from app.domain import auth as auth_domain
from app.domain.rate_limit import (
    InMemoryRateLimitStore,
    RateLimitStore,
    RedisRateLimitStore,
    check_design_rate_limit,
    check_login_rate_limit,
    check_signup_rate_limit,
    check_trial_rate_limit,
    check_trial_resume_rate_limit,
    check_trial_start_rate_limit,
)
from app.domain.storage import FilesystemObjectStore, ObjectStore
from app.models.schemas.auth import CurrentUser
from app.models.schemas.auth_flows import LoginRequest

SessionDep = Annotated[Session, Depends(get_session)]


def get_object_store() -> ObjectStore:
    """Return the store uploaded images are written to.

    Returns:
        The configured store. A dependency rather than a module-level
        singleton so a test can substitute one without touching the disk.
    """
    return FilesystemObjectStore(Path(get_settings().image_storage_root))


ObjectStoreDep = Annotated[ObjectStore, Depends(get_object_store)]


# Seconds. An unreachable Redis should cost a trial request a fraction of a
# second before the limiter fails open, not hang it on the OS default.
_REDIS_SOCKET_TIMEOUT_SECONDS = 0.5


@lru_cache(maxsize=1)
def get_rate_limit_store() -> RateLimitStore:
    """Return the store trial rate limiting counts against.

    Returns:
        One store per process, chosen by ``RATE_LIMIT_BACKEND``. Cached so
        counts survive between requests — a new in-memory store per request
        would count to one every time and enforce nothing — and so the Redis
        connection pool is built once rather than per request.

        ``redis`` shares one window across every worker. ``memory`` is
        per-worker: a deployment with N workers then has an effective limit N
        times the configured one. The per-account quota (BE-002) is the hard
        limit either way.
    """
    settings = get_settings()
    if settings.rate_limit_backend is RateLimitBackend.MEMORY:
        return InMemoryRateLimitStore()
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
    )
    return RedisRateLimitStore(client)


def enforce_trial_rate_limit(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Apply the per-source limit to a trial-path request.

    Attached to specific routes rather than installed globally: authenticated
    paying usage is not subject to an IP ceiling, because a large customer's
    whole estate can share one egress address and throttling it would throttle
    the people paying for the service.

    Every tenant is on the free trial today — there is no paid tier in the
    schema yet — so in practice this currently applies wherever it is
    attached. It is written as a per-route dependency so introducing a paid
    tier is a matter of not attaching it, rather than unpicking a global.

    Args:
        request: The incoming request, for its source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source is over its limit.
    """
    check_trial_rate_limit(store=store, client_ip=_client_ip(request))


# Reads a caller cannot use to spend anything: no model call, no stored file.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def enforce_trial_rate_limit_on_writes(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Apply the trial limit to everything on a router except reads.

    For a router that mixes the costly path (asking a question) with cheap
    reads of the caller's own data. Counting a page reload of a conversation
    against the allowance for asking questions would lock a site out of
    reading its own history mid-fault, for no saving at all.

    Args:
        request: The incoming request, for its method and source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source is over its limit.
    """
    if request.method in _READ_METHODS:
        return
    check_trial_rate_limit(store=store, client_ip=_client_ip(request))


def enforce_trial_start_rate_limit(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Throttle starting a trial, per source address.

    Separate from the trial-path limit: each start mints a tenant with a
    fresh free allowance, which is the abuse this stops, and its budget is
    sized for that rather than for asking questions.

    Args:
        request: The incoming request, for its source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source has started too many trials.
    """
    check_trial_start_rate_limit(store=store, client_ip=_client_ip(request))


def enforce_trial_resume_rate_limit(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Throttle resuming a trial, per source address, on its own budget.

    Args:
        request: The incoming request, for its source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source has resumed too often.
    """
    check_trial_resume_rate_limit(store=store, client_ip=_client_ip(request))


def enforce_design_rate_limit(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Throttle the panel design routes, per source address, on their own budget.

    Args:
        request: The incoming request, for its source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source has made too many design requests.
    """
    check_design_rate_limit(store=store, client_ip=_client_ip(request))


def enforce_signup_rate_limit(
    request: Request,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Throttle account creation, per source address.

    Args:
        request: The incoming request, for its source address.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If this source has signed up too often.
    """
    check_signup_rate_limit(store=store, client_ip=_client_ip(request))


def enforce_login_rate_limit(
    request: Request,
    payload: LoginRequest,
    store: Annotated[RateLimitStore, Depends(get_rate_limit_store)],
) -> None:
    """Throttle login attempts, per source address and per account.

    Takes the login body so the account can be limited as well as the
    address. FastAPI parses the body once and hands the same model to this
    dependency and to the route, because both name it ``payload``.

    Args:
        request: The incoming request, for its source address.
        payload: The submitted credentials; only the email is read.
        store: Where request history lives.

    Raises:
        RateLimitExceededError: If either limit is exhausted.
    """
    check_login_rate_limit(store=store, client_ip=_client_ip(request), email=payload.email)


def _client_ip(request: Request) -> str:
    """Return the address to limit by.

    Args:
        request: The incoming request.

    Returns:
        The client address, or an empty string when it cannot be determined.

        Deliberately reads only the socket address. ``X-Forwarded-For`` is
        caller-controlled unless a trusted proxy overwrites it, and trusting it
        here would let anyone bypass the limit by sending a different value
        each request — the exact abuse this is meant to stop. A deployment
        behind a proxy should have that proxy set the socket address, or this
        needs an explicit trusted-proxy configuration rather than a header we
        hope is honest. Uvicorn provides exactly that: with
        ``FORWARDED_ALLOW_IPS`` set to the proxy's address, it rewrites the
        socket address from the proxy's header before this ever runs (see
        the Dockerfile).
    """
    return request.client.host if request.client else ""


# auto_error=False so a missing header raises our AuthenticationError, which
# the installed handler renders consistently, rather than Starlette's own 403.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Resolve the authenticated caller from the request credentials.

    Two steps, both necessary. Decoding proves the token is ours and unexpired;
    ``resolve_caller`` then confirms the account still exists, is still active,
    and still belongs to the tenant the token claims. Skipping the second means
    a deactivated user keeps working until their token happens to expire.

    Args:
        session: Request-scoped database session.
        credentials: Bearer credentials from the ``Authorization`` header.

    Returns:
        The authenticated caller, carrying their tenant.

    Raises:
        AuthenticationError: If credentials are absent, invalid, expired, or no
            longer match a live account.
    """
    caller = decode_access_token(credentials.credentials if credentials else "")
    # Raises if the account is gone, inactive, or has moved tenant; returns the
    # caller with roles read from the database rather than taken from the token.
    return auth_domain.authenticate(session=session, caller=caller)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = ["CurrentUserDep", "SessionDep", "get_current_user"]
