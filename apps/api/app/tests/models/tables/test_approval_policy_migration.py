"""Migration ``b4e8d2f6a9c1``: each legacy approval's policy and frozen approver name.

Runs the real migration, up and down, against a scratch database created
for this module beside the disposable test database (named after it, with
``_approval_mig``) and dropped afterwards, seeded with the legacy approvals
the backfill has to decide: approved by an account id, by an email only, by
no account, by an account with no usable name (blank, or its email), by an
account of another tenant, and a revision never approved.

Like the export-integrity gates, it refuses any local database not named
``panelpilot_phase1_*``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from app.tests import db_target

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
API_ROOT = Path(__file__).resolve().parents[4]
BEFORE = "a7d3e9f1c2b4"
HEAD = "b4e8d2f6a9c1"
COLUMNS = (
    "approved_profile_sha256",
    "approved_rules_version",
    "approved_catalogue_version",
    "approved_findings_policy",
    "approved_display_name",
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: the migration is run for real",
)


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return result


@pytest.fixture(scope="module", name="scratch_url")
def _scratch_url() -> Iterator[str]:
    try:
        scratch = db_target.scratch_url(DATABASE_URL, "approval_mig")
    except db_target.UnsafeDatabaseTargetError as exc:
        pytest.fail(str(exc))
    base = make_url(DATABASE_URL)
    name = scratch.database
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


class _Seed:
    """Legacy revisions by case, and the name each must end frozen with."""

    def __init__(self, engine: Engine) -> None:
        self.revisions: dict[str, uuid.UUID] = {}
        self.names: dict[str, str | None] = {}
        with engine.begin() as conn:

            def tenant(slug: str) -> uuid.UUID:
                tid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO tenants (id, slug, name, is_active)"
                        " VALUES (:id, :slug, :slug, true)"
                    ),
                    {"id": tid, "slug": slug},
                )
                return tid

            def account(tid: uuid.UUID, email: str, name: str | None) -> uuid.UUID:
                uid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, full_name, is_active)"
                        " VALUES (:id, :t, :e, :n, true)"
                    ),
                    {"id": uid, "t": tid, "e": email, "n": name},
                )
                return uid

            home = tenant("approval-mig-home")
            other = tenant("approval-mig-other")
            by_id = account(home, "by-id@mig.example", "  Rana Haddad ")
            account(home, "By-Email@mig.example", "Omar Khalil")
            blank = account(home, "blank@mig.example", "   ")
            emailish = account(home, "emailish@mig.example", "emailish@mig.example")
            foreign = account(other, "foreign@mig.example", "Other Tenant Name")
            project = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO design_projects (id, tenant_id, name, revision_count)"
                    " VALUES (:id, :t, 'P', 7)"
                ),
                {"id": project, "t": home},
            )
            cases: list[tuple[str, dict[str, object] | None, str | None]] = [
                ("by_id", {"user": by_id, "account": "by-id@mig.example"}, "Rana Haddad"),
                ("by_email", {"user": None, "account": "by-email@MIG.example"}, "Omar Khalil"),
                ("no_account", {"user": None, "account": None}, ""),
                ("blank_name", {"user": blank, "account": "blank@mig.example"}, ""),
                ("email_name", {"user": emailish, "account": "emailish@mig.example"}, ""),
                ("other_tenant", {"user": foreign, "account": "foreign@mig.example"}, ""),
                ("unapproved", None, None),
            ]
            for number, (case, approval, expected) in enumerate(cases, start=1):
                rid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO design_project_revisions (id, tenant_id, project_id, number,"
                        " note, author, request, snapshot_sha256, profile_sha256, rules_version,"
                        " catalogue_version, findings, approved_at, approved_user_id,"
                        " approved_account, approved_snapshot_sha256)"
                        " VALUES (:id, :t, :p, :n, '', '', CAST(:req AS JSONB), :snap, :prof,"
                        " :rules, :cat, CAST('[]' AS JSONB),"
                        " :at, :user, :account, :asnap)"
                    ),
                    {
                        "id": rid,
                        "t": home,
                        "p": project,
                        "n": number,
                        "req": json.dumps({"case": case}),
                        "snap": f"{number:064d}",
                        "prof": f"{number + 100:064d}",
                        "rules": f"rules-{case}",
                        "cat": f"cat-{case}",
                        "at": datetime.now(UTC) if approval is not None else None,
                        "asnap": f"{number:064d}" if approval is not None else None,
                        "user": (approval or {}).get("user"),
                        "account": (approval or {}).get("account"),
                    },
                )
                self.revisions[case] = rid
                self.names[case] = expected


def _rows(engine: Engine) -> dict[uuid.UUID, dict[str, object]]:
    with engine.connect() as conn:
        found = conn.execute(text("SELECT * FROM design_project_revisions")).mappings().all()
    return {row["id"]: dict(row) for row in found}


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'design_project_revisions'"
                )
            ).scalars()
        )


def _check(engine: Engine, seed: _Seed) -> None:
    rows = _rows(engine)
    for case, rid in seed.revisions.items():
        row = rows[rid]
        assert row["approved_display_name"] == seed.names[case], case
        assert row["approved_findings_policy"] is None, case
        if case == "unapproved":
            assert row["approved_profile_sha256"] is None
            assert row["approved_rules_version"] is None
            assert row["approved_catalogue_version"] is None
        else:
            assert row["approved_profile_sha256"] == row["profile_sha256"], case
            assert row["approved_rules_version"] == row["rules_version"] == f"rules-{case}"
            assert row["approved_catalogue_version"] == row["catalogue_version"], case


def test_the_backfill_binds_legacy_approvals_and_downgrades(scratch_url: str) -> None:
    _alembic(scratch_url, "upgrade", BEFORE)
    engine = create_engine(scratch_url)
    try:
        seed = _Seed(engine)
        upgraded = _alembic(scratch_url, "upgrade", HEAD)
        _check(engine, seed)
        report = upgraded.stderr
        assert "approval policy backfill: approved_rows=6" in report
        assert "approver name backfill: named=2 team_member=4" in report
        assert "@" not in report
        assert "Rana" not in report

        _alembic(scratch_url, "downgrade", BEFORE)
        assert not set(COLUMNS) & _columns(engine)
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM design_project_revisions"))
            assert count.scalar_one() == 7

        # Up again: the same, deterministic outcome.
        _alembic(scratch_url, "upgrade", HEAD)
        _check(engine, seed)
    finally:
        engine.dispose()
