"""FastAPI dependencies shared by all route modules.

Dependencies resolve *inputs* (session, caller, clients). They must not contain
business logic — that belongs in ``app.domain``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import decode_access_token
from app.domain import auth as auth_domain
from app.domain.rate_limit import (
    InMemoryRateLimitStore,
    RateLimitStore,
    check_trial_rate_limit,
)
from app.domain.storage import FilesystemObjectStore, ObjectStore
from app.models.schemas.auth import CurrentUser

SessionDep = Annotated[Session, Depends(get_session)]


def get_object_store() -> ObjectStore:
    """Return the store uploaded images are written to.

    Returns:
        The configured store. A dependency rather than a module-level
        singleton so a test can substitute one without touching the disk.
    """
    return FilesystemObjectStore(Path(get_settings().image_storage_root))


ObjectStoreDep = Annotated[ObjectStore, Depends(get_object_store)]


def get_rate_limit_store() -> RateLimitStore:
    """Return the store trial rate limiting counts against.

    Returns:
        A process-local store.

        **This is single-worker only.** Each worker keeps its own counts, so a
        deployment with N workers has an effective limit N times the
        configured one. ``RATE_LIMIT_BACKEND`` documents the intended Redis
        adapter; until it exists this is honest about what it enforces rather
        than pretending to a guarantee it cannot make. The per-account quota
        (BE-002) is unaffected and remains the hard limit.
    """
    return _rate_limit_store


# One instance per process, so counts survive between requests. A new store per
# request would count to one every time and enforce nothing.
_rate_limit_store: RateLimitStore = InMemoryRateLimitStore()


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
        hope is honest.
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
    # Raises if the account is gone, inactive, or has moved tenant.
    auth_domain.resolve_caller(session=session, caller=caller)
    return caller


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = ["CurrentUserDep", "SessionDep", "get_current_user"]
