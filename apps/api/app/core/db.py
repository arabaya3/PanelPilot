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
    url = settings.database_url.get_secret_value()
    return create_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        # How long a request waits for a free connection before failing. The
        # default is 30 s, which turned an exhausted pool into half a minute
        # of stalled requests — health checks included — before any error.
        pool_timeout=POOL_TIMEOUT_S,
        # Recycled before the idle cut-off common to managed Postgres and
        # proxies, so the pool does not hand out connections that were
        # severed while idle; `pool_pre_ping` would catch those, one failed
        # round trip at a time.
        pool_recycle=POOL_RECYCLE_S,
        connect_args=_connect_args(url),
        future=True,
    )


#: Seconds a request waits for a pooled connection before failing.
POOL_TIMEOUT_S = 10

#: Seconds after which a pooled connection is replaced.
POOL_RECYCLE_S = 1800

#: Seconds to establish a new connection.
CONNECT_TIMEOUT_S = 5

#: Longest any single statement may run, in milliseconds. Every query on the
#: request path is an indexed lookup; one running this long is a missing index
#: or a lock pile-up, and holding its connection indefinitely lets that spread
#: to every other request waiting on the pool.
STATEMENT_TIMEOUT_MS = 30_000


def _connect_args(url: str) -> dict[str, object]:
    """Return driver options bounding how long the database can hold us.

    Args:
        url: The database URL.

    Returns:
        psycopg options for Postgres; nothing for any other driver, which
        would reject them.
    """
    if not url.startswith("postgresql"):
        return {}
    return {
        "connect_timeout": CONNECT_TIMEOUT_S,
        "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
    }


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
