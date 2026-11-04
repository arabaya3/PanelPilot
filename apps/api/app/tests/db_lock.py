"""One pytest session per disposable database, enforced by Postgres.

On 2026-11 an env file in a shared scratch directory was overwritten, so one
agent's test run pointed at another agent's disposable database and its
fixtures truncated that run's tables mid-suite. The name guard in
``app.tests.db_target`` cannot catch that: both runs used legitimate
``panelpilot_test_*`` names, just the same one.

So a run claims its database before any test runs: a session-level advisory
lock, keyed on the database name, taken on a connection held open for the
whole run. A second run against the same database finds the lock held and
stops before collecting tests, naming the database and the holder (its
``application_name`` carries the holder's working directory and pid). The lock
dies with the holding connection, so a crashed or killed run never leaves a
stale claim behind.

Scratch databases the migration tests derive from the configured name
(``<name>_<suffix>``) are covered too: only the holder of ``<name>`` makes them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.tests import db_target

#: Prefix of the lock key and of the holder's ``application_name``.
_TAG = "panelpilot-pytest"

#: Kept open for the whole run; closing it releases the lock.
_holder: Any = None


class DatabaseInUseError(RuntimeError):
    """Another pytest session already holds the configured database."""


def _application_name() -> str:
    # Postgres keeps 63 bytes; the pid and the tail of the path matter most.
    return f"{_TAG} pid={os.getpid()} cwd={str(Path.cwd())[-30:]}"[:63]


def claim(url: str) -> bool:
    """Take the run's lock on the database ``url`` names, or raise.

    Args:
        url: ``TEST_DATABASE_URL``; it must already pass ``require_disposable``.

    Returns:
        True when the lock is held, False when the server could not be reached
        (then nothing can be truncated either, and the database tests fail or
        skip on their own).

    Raises:
        DatabaseInUseError: Another session holds the lock on this database.
    """
    global _holder
    parsed = db_target.require_disposable(url)
    if _holder is not None:
        return True
    engine = create_engine(
        parsed,
        poolclass=NullPool,
        connect_args={"application_name": _application_name(), "connect_timeout": 5},
    )
    try:
        conn = engine.connect()
    except OperationalError:
        engine.dispose()
        return False
    conn.execution_options(isolation_level="AUTOCOMMIT")
    got: bool = conn.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:k))"),
        {"k": f"{_TAG}:{parsed.database}"},
    ).scalar_one()
    if not got:
        holders = conn.execute(
            text(
                "SELECT a.pid, a.application_name FROM pg_locks l "
                "JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.granted "
                "AND l.database = (SELECT oid FROM pg_database "
                "WHERE datname = current_database()) "
                "AND a.application_name LIKE :tag"
            ),
            {"tag": f"{_TAG}%"},
        ).all()
        conn.close()
        engine.dispose()
        who = "; ".join(f"pid {p} ({n})" for p, n in holders) or "another session"
        raise DatabaseInUseError(
            f"the test database {parsed.database!r} is in use by another pytest run "
            f"[{who}]; nothing was truncated. Give this worktree its own "
            "panelpilot_test_* database (see docs/testing/parallel-test-runs.md)."
        )
    _holder = conn
    return True


def release() -> None:
    """Drop the lock (process exit does the same)."""
    global _holder
    if _holder is not None:
        _holder.close()
        _holder = None
