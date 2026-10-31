"""Tests for ``app/models/tables/engineering_reviews.py`` and migration ``d9e4b7c2a5f1``.

* The rows are append-only: the ORM refuses an update or delete, and so does
  the database trigger, for raw SQL as well.
* The checks refuse an unknown kind, decision or a malformed digest.
* The migration runs up and down for real on a scratch database beside the
  disposable one (``_review_mig``), dropped afterwards; ``create_all`` on
  another scratch database installs the same trigger.

Synthetic rows only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.tables.base import Base
from app.models.tables.engineering_reviews import (
    AppendOnlyError,
    GlobalEngineeringReviewDecisionRow,
)
from app.tests import db_target

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
API_ROOT = Path(__file__).resolve().parents[4]
BEFORE = "c5d1e8a3f7b2"
HEAD = "d9e4b7c2a5f1"
TABLES = (
    "engineering_reviewer_grants",
    "engineering_review_decisions",
    "global_engineering_review_decisions",
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: append-only is enforced by the database",
)


def _row(**overrides: object) -> GlobalEngineeringReviewDecisionRow:
    fields: dict[str, object] = {
        "subject_kind": "rule",
        "subject_ref": f"synthetic/m3-table-{uuid.uuid4().hex[:8]}/v1",
        "subject_digest": "d" * 64,
        "decision": "approved",
        "reviewer_label": "Synthetic reviewer",
        "qualification_statement": "SYNTHETIC TEST STATEMENT",
        "standards": [],
        "reason": "Synthetic",
        "recorded_by": "synthetic-operator",
    }
    fields.update(overrides)
    return GlobalEngineeringReviewDecisionRow(**fields)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    db_target.require_disposable(DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_the_orm_refuses_an_update_or_delete(engine: Engine) -> None:
    with sessionmaker(bind=engine)() as session:
        row = _row()
        session.add(row)
        session.commit()
        row.decision = "rejected"
        with pytest.raises(AppendOnlyError):
            session.commit()
        session.rollback()
        session.delete(row)
        with pytest.raises(AppendOnlyError):
            session.commit()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE global_engineering_review_decisions SET decision = 'rejected' WHERE id = :i",
        "DELETE FROM global_engineering_review_decisions WHERE id = :i",
    ],
)
def test_the_database_refuses_an_update_or_delete(engine: Engine, statement: str) -> None:
    with sessionmaker(bind=engine)() as session:
        row = _row()
        session.add(row)
        session.commit()
        identity = row.id
    with engine.connect() as conn, pytest.raises(DBAPIError) as refused:
        conn.execute(text(statement), {"i": identity})
    assert "append-only" in str(refused.value)


@pytest.mark.parametrize(
    "overrides",
    [{"subject_kind": "design"}, {"decision": "approve"}, {"subject_digest": "D" * 64}],
)
def test_the_checks_refuse_bad_values(engine: Engine, overrides: dict[str, object]) -> None:
    with sessionmaker(bind=engine)() as session:
        session.add(_row(**overrides))
        with pytest.raises(IntegrityError):
            session.commit()


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]


def _scratch(suffix: str) -> Iterator[str]:
    scratch = db_target.scratch_url(DATABASE_URL, suffix)
    base = make_url(DATABASE_URL)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch.database}"'))
        conn.execute(text(f'CREATE DATABASE "{scratch.database}"'))
    try:
        yield scratch.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(name="migration_url")
def _migration_url() -> Iterator[str]:
    yield from _scratch("review_mig")


@pytest.fixture(name="metadata_url")
def _metadata_url() -> Iterator[str]:
    yield from _scratch("review_meta")


def _triggers(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE tgname LIKE '%_append_only'")
            ).scalars()
        )


def test_the_migration_runs_up_and_down(migration_url: str) -> None:
    _alembic(migration_url, "upgrade", BEFORE)
    engine = create_engine(migration_url)
    try:
        assert not set(TABLES) & set(inspect(engine).get_table_names())
        _alembic(migration_url, "upgrade", HEAD)
        assert set(TABLES) <= set(inspect(engine).get_table_names())
        assert _triggers(engine) == {f"{t}_append_only" for t in TABLES}
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO global_engineering_review_decisions (id, subject_kind, "
                    "subject_ref, subject_digest, decision, reviewer_label, "
                    "qualification_statement, standards, reason, recorded_by) VALUES "
                    "(:i, 'rule', 'synthetic/m3-mig/v1', :d, 'approved', 'Synthetic', "
                    "'SYNTHETIC', '[]', 'Synthetic', 'synthetic-operator')"
                ),
                {"i": uuid.uuid4(), "d": "e" * 64},
            )
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text("DELETE FROM global_engineering_review_decisions"))
        _alembic(migration_url, "check")
        _alembic(migration_url, "downgrade", BEFORE)
        with engine.connect() as conn:
            assert not set(TABLES) & set(
                conn.execute(text("SELECT tablename FROM pg_tables")).scalars()
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_proc WHERE proname = 'pp_refuse_append_only_change'"
                    )
                ).scalar_one()
                == 0
            )
        _alembic(migration_url, "upgrade", HEAD)
    finally:
        engine.dispose()


def test_create_all_installs_the_trigger_too(metadata_url: str) -> None:
    engine = create_engine(metadata_url)
    try:
        Base.metadata.create_all(engine)
        assert _triggers(engine) == {f"{t}_append_only" for t in TABLES}
        with engine.connect() as conn:
            checks: set[str] = set(
                conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conname LIKE '%subject_digest'")
                ).scalars()
            )
        assert checks == {
            "ck_engineering_review_decisions_subject_digest",
            "ck_global_engineering_review_decisions_subject_digest",
        }
    finally:
        engine.dispose()
