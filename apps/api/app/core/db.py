"""Database engine and session lifecycle.

The engine is created once per process. Request-scoped sessions are handed out
through ``get_session`` as a FastAPI dependency; domain functions take a
``Session`` argument instead of reaching for a global, so they remain testable
without a running app.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlalchemy.orm import Session


def create_engine_from_settings() -> Engine:
    """Build the SQLAlchemy engine from application settings.

    Returns:
        An engine configured with the pool sizing from settings.
    """
    raise NotImplementedError


def get_session() -> Iterator[Session]:
    """Yield a request-scoped database session.

    Used as a FastAPI dependency. The session is committed on clean exit and
    rolled back if the handler raises.

    Yields:
        An open SQLAlchemy session.
    """
    raise NotImplementedError
