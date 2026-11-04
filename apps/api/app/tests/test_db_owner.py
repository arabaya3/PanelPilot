"""The test-database owner marker (``app.tests.db_owner``).

The parsing and token rules run without a database. The claim rules run
against a scratch database derived from ``TEST_DATABASE_URL`` (so only by the
run that already owns that database), created and dropped here; they skip
without Postgres.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.tests import db_owner, db_target


def test_the_token_is_stable_per_worktree_and_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(db_owner.OWNER_ENV, raising=False)
    derived = db_owner.owner_token()
    assert derived == db_owner.owner_token()
    assert derived.startswith("wt-")
    assert len(derived) == 19
    monkeypatch.setenv(db_owner.OWNER_ENV, "lane-7")
    assert db_owner.owner_token() == "lane-7"
    monkeypatch.setenv(db_owner.OWNER_ENV, "x'; DROP DATABASE y; --")
    with pytest.raises(db_owner.DatabaseNotOwnedError):
        db_owner.owner_token()


def test_the_marker_round_trips_and_foreign_comments_are_refused() -> None:
    assert db_owner.parse(db_owner.marker("lane-7")) == "lane-7"
    assert db_owner.parse(None) is None
    assert db_owner.parse("") is None
    with pytest.raises(db_owner.DatabaseNotOwnedError):
        db_owner.parse("the finance team's database")


@pytest.fixture(name="scratch")
def _scratch() -> Iterator[tuple[Connection, str]]:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("needs Postgres (TEST_DATABASE_URL)")
    target = db_target.scratch_url(url, "owner_chk")
    admin = create_engine(db_target.require_disposable(url), poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}"'))
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    except OperationalError:
        pytest.skip("needs Postgres (TEST_DATABASE_URL)")
    engine = create_engine(target, poolclass=NullPool)
    conn = engine.connect()
    conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        yield conn, str(target.database)
    finally:
        conn.close()
        engine.dispose()
        with admin.connect() as drop:
            drop.execution_options(isolation_level="AUTOCOMMIT")
            drop.execute(text(f'DROP DATABASE IF EXISTS "{target.database}"'))
        admin.dispose()


def test_claims_only_a_fresh_database_and_refuses_another_owner(
    scratch: tuple[Connection, str],
) -> None:
    conn, name = scratch
    conn.execute(text("CREATE TABLE tenants (id int, slug text)"))
    conn.execute(text("CREATE TABLE users (id int, tenant_id int)"))
    conn.execute(text("CREATE TABLE notes (tenant_id int)"))
    conn.execute(text("INSERT INTO tenants VALUES (1, 'default')"))
    # Fresh (only the migration's default tenant): claimed, then owned.
    assert db_owner.require_owned(conn, name, "lane-a") == "claimed"
    assert db_owner.require_owned(conn, name, "lane-a") == "owned"
    # Another token: refused, whatever the data.
    with pytest.raises(db_owner.DatabaseNotOwnedError, match="belongs to another"):
        db_owner.require_owned(conn, name, "lane-b")
    # No marker but data in a tenant table: refused, and the data stays.
    conn.execute(text(f'COMMENT ON DATABASE "{name}" IS NULL'))
    conn.execute(text("INSERT INTO notes VALUES (1)"))
    with pytest.raises(db_owner.DatabaseNotOwnedError, match="holds application data"):
        db_owner.require_owned(conn, name, "lane-b")
    assert conn.execute(text("SELECT count(*) FROM notes")).scalar_one() == 1
    assert db_owner.parse(db_owner._comment(conn)) is None
