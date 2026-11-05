"""M6f review: the test-database owner marker, end to end in a real pytest subprocess.

Only databases this module creates (``panelpilot_test_m6f_rev_<hex>``) are
touched; each is dropped by its exact name. Needs ``TEST_DATABASE_URL`` (its
server and credentials are reused).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.pool import NullPool

from app.tests import db_owner, db_target

API = Path(__file__).resolve().parents[2]
#: A test that needs no database fixture, so a run that gets past the guard passes.
TINY = "app/tests/domain/test_m6f_review_confirmation_db.py::test_the_unknown_power_rule_asks_about_motor_like_and_1kw_loads"


def _base() -> URL:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return db_target.require_disposable(url)


@pytest.fixture
def scratch() -> Iterator[tuple[Connection, URL]]:
    base = _base()
    name = f"panelpilot_test_m6f_rev_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, poolclass=NullPool)
    with admin.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT")
        c.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target, poolclass=NullPool)
    conn = engine.connect()
    conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        yield conn, target
    finally:
        conn.close()
        engine.dispose()
        with admin.connect() as c:
            c.execution_options(isolation_level="AUTOCOMMIT")
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _env(target: URL) -> dict[str, str]:
    url = target.render_as_string(hide_password=False)
    env = {**os.environ, "TEST_DATABASE_URL": url, "DATABASE_URL": url}
    env.pop(db_owner.OWNER_ENV, None)
    return env


def _run(target: URL, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=API,
        env=_env(target),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _snapshot(conn: Connection) -> tuple[object, ...]:
    tables = conn.execute(
        text(
            "SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_tables "
            "WHERE schemaname = 'public'"
        )
    ).scalar()
    rows = conn.execute(text("SELECT string_agg(v, ',' ORDER BY v) FROM sentinel")).scalar()
    comment = conn.execute(
        text(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
            "WHERE datname = current_database()"
        )
    ).scalar()
    return tables, rows, comment


@pytest.mark.parametrize(
    "comment",
    [db_owner.marker("someone-else-lane"), "a database comment written by someone else"],
    ids=["another_token", "foreign_comment"],
)
def test_an_idle_database_of_another_owner_is_refused_before_collection(
    scratch: tuple[Connection, URL], comment: str
) -> None:
    conn, target = scratch
    conn.execute(text("CREATE TABLE sentinel (v text)"))
    conn.execute(text("INSERT INTO sentinel VALUES ('keep-1'), ('keep-2')"))
    conn.execute(text(f"COMMENT ON DATABASE \"{target.database}\" IS '{comment}'"))
    before = _snapshot(conn)
    ran = _run(target, "-m", "pytest", "-q", "-p", "no:cacheprovider", TINY)
    assert ran.returncode == 1, ran.stdout[-600:] + ran.stderr[-600:]
    out = ran.stdout + ran.stderr
    assert str(target.database) in out or "not a PanelPilot test-owner marker" in out
    assert "collected" not in out, out[-600:]
    assert "passed" not in out, out[-600:]
    assert _snapshot(conn) == before
    # claim without --force is refused and changes nothing.
    claimed = _run(target, "-m", "app.tests.db_owner", "claim")
    assert claimed.returncode == 1, claimed.stdout + claimed.stderr
    assert _snapshot(conn) == before


def test_a_fresh_ci_like_database_is_claimed_and_the_run_proceeds(
    scratch: tuple[Connection, URL],
) -> None:
    conn, target = scratch
    migrated = _run(target, "-m", "alembic", "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr[-800:]
    ran = _run(target, "-m", "pytest", "-q", "-p", "no:cacheprovider", TINY)
    assert ran.returncode == 0, ran.stdout[-800:] + ran.stderr[-800:]
    comment = conn.execute(
        text(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
            "WHERE datname = current_database()"
        )
    ).scalar()
    assert db_owner.parse(comment) == _token(target)


def _token(target: URL) -> str:
    probe = subprocess.run(
        [sys.executable, "-c", "from app.tests import db_owner; print(db_owner.owner_token())"],
        cwd=API,
        env=_env(target),
        capture_output=True,
        text=True,
        check=True,
    )
    return probe.stdout.strip()
