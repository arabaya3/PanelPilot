"""Which worktree owns a disposable test database, recorded on the database.

The advisory lock in ``app.tests.db_lock`` stops two runs at once on one
database; it says nothing about a run that targets another worktree's
*idle* database (a copied env file, a typo in a lane name). That run would
get the lock and truncate the other worktree's data. So, after the lock and
before any test is collected, the run must also own the database:

* The run's **owner token** is ``PANELPILOT_TEST_DB_OWNER`` if set, else a
  stable digest of this worktree's path (so each checkout is its own owner
  and a CI job's checkout owns its fresh database).
* The database carries an **owner marker**: its comment,
  ``COMMENT ON DATABASE <name> IS 'panelpilot-test-owner:<token> path=<hint>'``.
  A comment survives every ``TRUNCATE`` and every migration, adds no table
  for ``alembic check`` to see, and can be set only by the database's owner
  role (the role that ran ``createdb``, as ``panelpilot`` does here and in CI).
* A matching marker: the run goes ahead. Another token: refused. No marker:
  the database is **claimed only if it holds no application data** (no
  account, no tenant but the migration's ``default`` one, no row in any table
  with a ``tenant_id``); otherwise refused. Every refusal happens before a
  test is collected, so nothing is truncated.

A person who legitimately reassigns a database runs, with
``TEST_DATABASE_URL`` set to it::

    python -m app.tests.db_owner status
    python -m app.tests.db_owner claim     # take it for this worktree's token
    python -m app.tests.db_owner release   # drop the marker

``claim`` overwrites another owner's marker and is meant for exactly that
reassignment; it refuses while a pytest run holds the database's lock.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.tests import db_target

#: Overrides the derived owner token.
OWNER_ENV = "PANELPILOT_TEST_DB_OWNER"

#: Prefix of the marker in the database comment.
PREFIX = "panelpilot-test-owner:"

#: What a token may contain (it is quoted into a SQL literal).
_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: The worktree this code lives in: apps/api/app/tests/db_owner.py → parents[4].
_WORKTREE = Path(__file__).resolve().parents[4]


class DatabaseNotOwnedError(RuntimeError):
    """The configured database belongs to another owner, or holds unowned data."""


def owner_token() -> str:
    """This run's owner token: ``PANELPILOT_TEST_DB_OWNER``, or a digest of the worktree path."""
    given = os.environ.get(OWNER_ENV, "").strip()
    if given:
        if not _TOKEN.fullmatch(given):
            raise DatabaseNotOwnedError(
                f"{OWNER_ENV} may hold only letters, digits, '.', '_' and '-' (at most 64)"
            )
        return given
    return "wt-" + hashlib.sha256(str(_WORKTREE).encode()).hexdigest()[:16]


def marker(token: str) -> str:
    """The comment that records ``token`` as the owner, with a readable path hint."""
    hint = re.sub(r"[^A-Za-z0-9_./-]", "_", str(_WORKTREE))[-80:]
    return f"{PREFIX}{token} path={hint}"


def parse(comment: str | None) -> str | None:
    """The owner token a database comment records, ``None`` if it records none.

    Raises:
        DatabaseNotOwnedError: The comment is something else (not ours to overwrite).
    """
    if not comment:
        return None
    if not comment.startswith(PREFIX):
        raise DatabaseNotOwnedError(
            "the database has a comment that is not a PanelPilot test-owner marker "
            f"({comment[:60]!r}); refusing to claim it"
        )
    return comment[len(PREFIX) :].split(" ", 1)[0] or None


def _comment(conn: Connection) -> str | None:
    found: Any = conn.execute(
        text(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
            "WHERE datname = current_database()"
        )
    ).scalar_one()
    return str(found) if found is not None else None


def _yes(conn: Connection, sql: str, params: dict[str, Any] | None = None) -> bool:
    value: Any = conn.execute(text(sql), params or {}).scalar_one()
    return bool(value)


def application_rows(conn: Connection) -> list[str]:
    """Where the database holds application data (empty when it is fresh)."""
    found: list[str] = []
    tables = [
        str(name)
        for (name,) in conn.execute(
            text(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t ON t.table_schema = c.table_schema "
                "AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE' "
                "WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' "
                "ORDER BY c.table_name"
            )
        ).all()
    ]
    present: set[str] = set(
        conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN ('users', 'tenants')"
            )
        )
        .scalars()
        .all()
    )
    if "users" in present and "users" not in tables:
        tables.append("users")
    for table in tables:
        if _yes(conn, f'SELECT EXISTS (SELECT 1 FROM "{table}")'):
            found.append(table)
    # The migration that creates ``tenants`` seeds the 'default' one.
    if "tenants" in present and _yes(
        conn, "SELECT EXISTS (SELECT 1 FROM tenants WHERE slug <> 'default')"
    ):
        found.append("tenants")
    return found


def _set_marker(conn: Connection, database: str, token: str) -> None:
    # Identifiers and the token are checked above; COMMENT takes no parameters.
    db_target.check_name(database)
    if not _TOKEN.fullmatch(token):
        raise DatabaseNotOwnedError(f"not a valid owner token: {token!r}")
    conn.execute(text(f"COMMENT ON DATABASE \"{database}\" IS '{marker(token)}'"))


def require_owned(conn: Connection, database: str, token: str | None = None) -> str:
    """Accept the run on ``database`` if ``token`` owns it, claiming it if fresh.

    Args:
        conn: An AUTOCOMMIT connection to that database (the lock holder's).
        database: Its name, already through the name guard.
        token: The run's owner token; :func:`owner_token` by default.

    Returns:
        ``"owned"`` or ``"claimed"``.

    Raises:
        DatabaseNotOwnedError: Another token owns it, it holds data and no
            owner, or its comment is not ours.
    """
    mine = token or owner_token()
    current = _comment(conn)
    holder = parse(current)
    if holder == mine:
        return "owned"
    if holder is not None:
        raise DatabaseNotOwnedError(
            f"the test database {database!r} belongs to another worktree or agent "
            f"(owner {current[len(PREFIX):] if current else holder!r}; this run is {mine!r}); "
            "nothing was truncated. Use your own panelpilot_test_* database, or, if it "
            "really is being handed to you, run `python -m app.tests.db_owner claim` "
            "(docs/testing/parallel-test-runs.md)."
        )
    data = application_rows(conn)
    if data:
        shown = ", ".join(data[:8]) + (" …" if len(data) > 8 else "")
        raise DatabaseNotOwnedError(
            f"the test database {database!r} has no owner marker but holds application "
            f"data ({shown}); nothing was truncated. If it is yours, run "
            "`python -m app.tests.db_owner claim` once (docs/testing/parallel-test-runs.md); "
            "otherwise create your own."
        )
    _set_marker(conn, database, mine)
    return "claimed"


def _connect(url: str) -> tuple[Any, Connection, str]:
    parsed = db_target.require_disposable(url)
    engine = create_engine(parsed, poolclass=NullPool, connect_args={"connect_timeout": 5})
    conn = engine.connect()
    conn.execution_options(isolation_level="AUTOCOMMIT")
    return engine, conn, str(parsed.database)


def main(argv: list[str]) -> int:
    """``status`` | ``claim`` | ``release`` for the database ``TEST_DATABASE_URL`` names."""
    from app.tests import db_lock

    if len(argv) != 1 or argv[0] not in {"status", "claim", "release"}:
        print("usage: python -m app.tests.db_owner status|claim|release", file=sys.stderr)
        return 2
    url = os.environ.get("TEST_DATABASE_URL", "")
    try:
        engine, conn, database = _connect(url)
    except (db_target.UnsafeDatabaseTargetError, OperationalError) as exc:
        print(f"db_owner: {exc}", file=sys.stderr)
        return 1
    try:
        current = _comment(conn)
        mine = owner_token()
        if argv[0] == "status":
            print(f"database: {database}\nmarker:   {current or '(none)'}\nthis run: {mine}")
            print(f"data in:  {', '.join(application_rows(conn)) or '(none)'}")
            return 0
        if not _yes(
            conn, "SELECT pg_try_advisory_lock(hashtext(:k))", {"k": db_lock.lock_key(database)}
        ):
            print(f"db_owner: a pytest run holds {database!r}; try again when it ends")
            return 1
        try:
            parse(current)
        except DatabaseNotOwnedError as exc:
            print(f"db_owner: {exc}", file=sys.stderr)
            return 1
        if argv[0] == "claim":
            _set_marker(conn, database, mine)
            print(f"db_owner: {database!r} now belongs to {mine!r} (was {current or 'none'})")
        else:
            conn.execute(text(f'COMMENT ON DATABASE "{database}" IS NULL'))
            print(f"db_owner: {database!r} has no owner marker (was {current or 'none'})")
        return 0
    finally:
        conn.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
