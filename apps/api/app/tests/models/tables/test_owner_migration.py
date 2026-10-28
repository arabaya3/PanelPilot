"""Migration ``a7d3e9f1c2b4``: each legacy tenant's recorded owner.

Runs the real migration, up and down, against a scratch database created
for this module beside the disposable test database (named after it, with
``_owner_mig``) and dropped afterwards, seeded with the legacy shapes the
backfill has to decide: a clean first account, a deactivated first account
with and without an admin, a ``created_at`` tie, several admins, and no
accounts at all.

Like the export-integrity gates, it refuses any local database not named
``panelpilot_phase1_*``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.tenancy import cross_tenant
from app.domain import team

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
API_ROOT = Path(__file__).resolve().parents[4]
BEFORE = "f8c4a2e6d9b3"
HEAD = "a7d3e9f1c2b4"

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
    base = make_url(DATABASE_URL)
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    if not in_ci and not (base.database or "").startswith("panelpilot_phase1_"):
        pytest.fail(f"refusing to run against a database other than the disposable one: {base}")
    name = f"{base.database}_owner_mig"
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


T0 = datetime(2025, 1, 1, tzinfo=UTC)


class _Seed:
    """The legacy tenants, by case, and the accounts each must end owned by."""

    def __init__(self, engine: Engine) -> None:
        self.tenants: dict[str, uuid.UUID] = {}
        self.expected: dict[str, uuid.UUID | None] = {}
        with engine.begin() as conn:
            admin_role = uuid.uuid4()
            conn.execute(
                text("INSERT INTO roles (id, name) VALUES (:id, 'admin')"), {"id": admin_role}
            )

            def tenant(case: str) -> uuid.UUID:
                tid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO tenants (id, slug, name, is_active)"
                        " VALUES (:id, :slug, :slug, true)"
                    ),
                    {"id": tid, "slug": f"mig-{case}"},
                )
                self.tenants[case] = tid
                return tid

            def account(
                tid: uuid.UUID, days: int, *, active: bool = True, admin: bool = False
            ) -> uuid.UUID:
                uid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, is_active, created_at)"
                        " VALUES (:id, :t, :e, :a, :c)"
                    ),
                    {
                        "id": uid,
                        "t": tid,
                        "e": f"{uid.hex}@mig.example",
                        "a": active,
                        "c": T0 + timedelta(days=days),
                    },
                )
                if admin:
                    conn.execute(
                        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
                        {"u": uid, "r": admin_role},
                    )
                return uid

            t = tenant("clean")
            self.expected["clean"] = account(t, 0)
            account(t, 1)
            t = tenant("single")
            self.expected["single"] = account(t, 3)
            t = tenant("first_inactive")
            account(t, 0, active=False)
            account(t, 1)
            self.expected["first_inactive"] = None
            t = tenant("first_inactive_admin")
            account(t, 0, active=False)
            account(t, 1, admin=True)
            self.expected["first_inactive_admin"] = None
            t = tenant("tie")
            account(t, 0)
            account(t, 0)
            account(t, 1)
            self.expected["tie"] = None
            t = tenant("tie_later")  # a tie that is not the oldest is no ambiguity
            self.expected["tie_later"] = account(t, 0)
            account(t, 1)
            account(t, 1)
            t = tenant("admins")
            self.expected["admins"] = account(t, 0)
            account(t, 1, admin=True)
            account(t, 2, admin=True)
            tenant("empty")
            self.expected["empty"] = None


def _owners(engine: Engine) -> dict[uuid.UUID, uuid.UUID | None]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, owner_user_id FROM tenants")).all()
    return {row[0]: row[1] for row in rows}


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            ).scalars()
        )


def test_the_backfill_records_only_unambiguous_owners_and_downgrades(scratch_url: str) -> None:
    _alembic(scratch_url, "upgrade", BEFORE)
    engine = create_engine(scratch_url)
    try:
        with engine.connect() as conn:
            # Tenants an earlier migration created, with no accounts.
            preexisting: int = conn.execute(text("SELECT count(*) FROM tenants")).scalar_one()
        seed = _Seed(engine)
        upgraded = _alembic(scratch_url, "upgrade", HEAD)
        owners = _owners(engine)
        for case, tid in seed.tenants.items():
            assert owners[tid] == seed.expected[case], case

        # Counts only, no ids, no addresses; the stranded tenants are named.
        report = upgraded.stderr
        assert "first_account_inactive tenants=2 without_active_admin=1" in report
        assert "created_at_tie tenants=1 without_active_admin=1" in report
        assert f"no_accounts tenants={1 + preexisting}" in report
        assert "owner_set tenants=4" in report
        assert "2 tenants have no owner and no active admin" in report
        assert "@" not in report
        assert not any(str(tid) in report for tid in seed.tenants.values())

        with Session(engine) as session, cross_tenant(session, reason="test"):
            issues = {i.tenant_id: i for i in team.ownership_report(session=session)}
        assert {uuid.UUID(t): (i.issue, i.active_admins) for t, i in issues.items()} == {
            seed.tenants["first_inactive"]: ("no_owner", 0),
            seed.tenants["first_inactive_admin"]: ("no_owner", 1),
            seed.tenants["tie"]: ("no_owner", 0),
        }

        # An owner must be the tenant's own account.
        with (
            pytest.raises(IntegrityError, match="fk_tenants_owner_user_id_users"),
            engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE tenants SET owner_user_id = :u WHERE id = :t"),
                {"u": seed.expected["clean"], "t": seed.tenants["tie"]},
            )

        _alembic(scratch_url, "downgrade", BEFORE)
        assert "owner_user_id" not in _columns(engine, "tenants")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_users_id_tenant_id'"
                    )
                ).scalar_one()
                == 0
            )
            assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == 16

        # Up again: the same, deterministic outcome.
        _alembic(scratch_url, "upgrade", HEAD)
        again = _owners(engine)
        for case, tid in seed.tenants.items():
            assert again[tid] == seed.expected[case], case
    finally:
        engine.dispose()
