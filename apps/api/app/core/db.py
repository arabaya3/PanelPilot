"""Database engine and session lifecycle.

The engine is created once per process. Request-scoped sessions are handed out
through ``get_session`` as a FastAPI dependency; domain functions take a
``Session`` argument instead of reaching for a global, so they remain testable
without a running app.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def create_engine_from_settings() -> Engine:
    """Build the SQLAlchemy engine from application settings.

    Cached so the pool is shared process-wide. ``pool_pre_ping`` is on because
    both runtimes sit behind connections that a container restart or an idle
    timeout can sever without warning.

    Returns:
        An engine configured with the pool sizing from settings.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(bind=create_engine_from_settings(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a request-scoped database session.

    Used as a FastAPI dependency. The session is committed on clean exit and
    rolled back if the handler raises.

    Yields:
        An open SQLAlchemy session.
    """
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
