"""FastAPI dependencies shared by all route modules.

Dependencies resolve *inputs* (session, caller, clients). They must not contain
business logic — that belongs in ``app.domain``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models.schemas.auth import CurrentUser

SessionDep = Annotated[Session, Depends(get_session)]


def get_current_user() -> CurrentUser:
    """Resolve the authenticated caller from the request credentials.

    Returns:
        The authenticated caller.

    Raises:
        AuthenticationError: If credentials are absent or invalid.
    """
    raise NotImplementedError


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = ["CurrentUserDep", "SessionDep", "get_current_user"]
