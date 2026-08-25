"""FastAPI dependencies shared by all route modules.

Dependencies resolve *inputs* (session, caller, clients). They must not contain
business logic — that belongs in ``app.domain``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.security import decode_access_token
from app.domain import auth as auth_domain
from app.models.schemas.auth import CurrentUser

SessionDep = Annotated[Session, Depends(get_session)]

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
