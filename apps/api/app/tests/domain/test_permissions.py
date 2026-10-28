"""Tests for `app/domain/permissions.py`.

Mirrors the module 1:1. Ownership is read from the tenant's accounts, so the
policy is tested against the disposable Postgres; a trial, which has no
account, is refused before the database is asked.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, ValidationError
from app.core.tenancy import bind_tenant
from app.domain import permissions, roles, team
from app.models.schemas.auth import CurrentUser, Permission, Role
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

needs_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: ownership is read from the tenant's accounts",
)

EVERYTHING = sorted(Permission)


def test_a_trial_holds_nothing_and_the_database_is_not_asked() -> None:
    session = MagicMock(spec=Session)
    trial = CurrentUser(
        id=str(uuid.uuid4()), email="", tenant_id=str(uuid.uuid4()), roles=frozenset()
    )
    assert permissions.allowed(session=session, user=trial) == []
    for permission in Permission:
        assert not permissions.can(session=session, user=trial, permission=permission)
    session.scalar.assert_not_called()


def test_a_caller_without_an_account_id_holds_nothing() -> None:
    session = MagicMock(spec=Session)
    caller = CurrentUser(
        id="trial", email="x@example.com", tenant_id=str(uuid.uuid4()), roles=frozenset()
    )
    assert permissions.allowed(session=session, user=caller) == []


@pytest.mark.parametrize(
    ("permission", "code"),
    [
        (Permission.APPROVE_REVISION, "approval_not_permitted"),
        (Permission.DELETE_PROJECT, "project_delete_not_permitted"),
        (Permission.EDIT_COMPANY_SETTINGS, "settings_not_permitted"),
    ],
)
def test_each_refusal_has_its_own_code(permission: Permission, code: str) -> None:
    trial = CurrentUser(
        id=str(uuid.uuid4()), email="", tenant_id=str(uuid.uuid4()), roles=frozenset()
    )
    with pytest.raises(AuthorizationError) as raised:
        permissions.require(session=MagicMock(spec=Session), user=trial, permission=permission)
    assert raised.value.code == code


# --------------------------------------------------------------------------- #
# Against the database
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("needs Postgres: ownership is read from the tenant's accounts")
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenant")
def _tenant(engine: Engine) -> uuid.UUID:
    """A tenant with no accounts yet."""
    with sessionmaker(bind=engine)() as session:
        found = session.execute(
            text("SELECT id FROM tenants WHERE slug = 'permissions-a'")
        ).scalar_one_or_none()
        if found is None:
            row = TenantRow(slug="permissions-a", name="permissions-a")
            session.add(row)
            session.flush()
            found = row.id
        session.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": found})
        session.commit()
        return found


def _account(
    engine: Engine, tenant: uuid.UUID, email: str, *, created: datetime, active: bool = True
) -> CurrentUser:
    """An account row, created when given; the caller it resolves to, roles from the DB."""
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, tenant)
        row = User(
            tenant_id=tenant, email=email, is_active=active, created_at=created, updated_at=created
        )
        session.add(row)
        session.commit()
        return CurrentUser(
            id=str(row.id), email=email, tenant_id=str(tenant), roles=roles.roles_of(row)
        )


def _grant(engine: Engine, tenant: uuid.UUID, email: str, role: Role) -> None:
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, tenant)
        roles.grant_role(session=session, email=email, role=role)
        session.commit()


def _reload(engine: Engine, caller: CurrentUser) -> CurrentUser:
    """The caller's roles as the next request would read them."""
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, uuid.UUID(caller.tenant_id))
        row = session.get(User, uuid.UUID(caller.id))
        assert row is not None
        return caller.model_copy(update={"roles": roles.roles_of(row)})


def _allowed(engine: Engine, caller: CurrentUser) -> list[Permission]:
    with sessionmaker(bind=engine)() as session:
        return permissions.allowed(session=session, user=caller)


@needs_postgres
def test_the_matrix(engine: Engine, tenant: uuid.UUID) -> None:
    start = datetime.now(UTC) - timedelta(days=30)
    owner = _account(engine, tenant, "dom-perm-owner@example.com", created=start)
    admin = _account(
        engine, tenant, "dom-perm-admin@example.com", created=start + timedelta(days=1)
    )
    reviewer = _account(
        engine, tenant, "dom-perm-reviewer@example.com", created=start + timedelta(days=2)
    )
    member = _account(
        engine, tenant, "dom-perm-member@example.com", created=start + timedelta(days=3)
    )
    _grant(engine, tenant, admin.email, Role.ADMIN)
    _grant(engine, tenant, reviewer.email, Role.REVIEWER)
    admin, reviewer = _reload(engine, admin), _reload(engine, reviewer)

    assert _allowed(engine, owner) == EVERYTHING
    assert _allowed(engine, admin) == EVERYTHING
    assert _allowed(engine, reviewer) == [Permission.APPROVE_REVISION]
    assert _allowed(engine, member) == []


@needs_postgres
def test_a_legacy_tenant_keeps_its_first_account_as_owner(
    engine: Engine, tenant: uuid.UUID
) -> None:
    """Accounts from before the checks, no role rows: the first keeps everything."""
    start = datetime.now(UTC) - timedelta(days=400)
    first = _account(engine, tenant, "dom-legacy-first@example.com", created=start)
    later = [
        _account(engine, tenant, f"dom-legacy-{i}@example.com", created=start + timedelta(days=i))
        for i in range(1, 4)
    ]
    assert _allowed(engine, first) == EVERYTHING
    for member in later:
        assert _allowed(engine, member) == []
        with sessionmaker(bind=engine)() as session, pytest.raises(AuthorizationError) as raised:
            permissions.require(
                session=session, user=member, permission=Permission.APPROVE_REVISION
            )
        assert raised.value.code == "approval_not_permitted"

    # The operator's remedy: grant reviewer, which allows approval only.
    _grant(engine, tenant, later[0].email, Role.REVIEWER)
    assert _allowed(engine, _reload(engine, later[0])) == [Permission.APPROVE_REVISION]


@needs_postgres
def test_a_deactivated_first_account_passes_ownership_on(engine: Engine, tenant: uuid.UUID) -> None:
    start = datetime.now(UTC) - timedelta(days=10)
    _account(engine, tenant, "dom-gone@example.com", created=start, active=False)
    next_one = _account(engine, tenant, "dom-next@example.com", created=start + timedelta(days=1))
    assert _allowed(engine, next_one) == EVERYTHING


@needs_postgres
def test_ownership_is_per_tenant(engine: Engine, tenant: uuid.UUID) -> None:
    """The owner of one tenant holds nothing as a caller of another."""
    owner = _account(engine, tenant, "dom-perm-owner-x@example.com", created=datetime.now(UTC))
    elsewhere = owner.model_copy(update={"tenant_id": str(uuid.uuid4())})
    assert _allowed(engine, elsewhere) == []


@needs_postgres
def test_an_admin_cannot_remove_the_first_account(engine: Engine, tenant: uuid.UUID) -> None:
    """Removing the owner would pass ownership silently to the next-oldest member."""
    start = datetime.now(UTC) - timedelta(days=10)
    first = _account(engine, tenant, "dom-succ-first@example.com", created=start)
    admin = _account(
        engine, tenant, "dom-succ-admin@example.com", created=start + timedelta(days=1)
    )
    member = _account(
        engine, tenant, "dom-succ-member@example.com", created=start + timedelta(days=2)
    )
    _grant(engine, tenant, admin.email, Role.ADMIN)
    admin = _reload(engine, admin)
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(ValidationError) as raised:
            team.remove(session=session, user=admin, member_id=first.id)
        assert raised.value.code == "team_owner_not_removable"
        session.rollback()
    assert _allowed(engine, first) == EVERYTHING
    assert _allowed(engine, member) == []
    # A plain member is still removable by the admin.
    with sessionmaker(bind=engine)() as session:
        team.remove(session=session, user=admin, member_id=member.id)
        session.commit()


@needs_postgres
def test_operator_deactivation_hands_ownership_to_the_next_oldest(
    engine: Engine, tenant: uuid.UUID
) -> None:
    """Documented succession: deactivated outside the API, the next-oldest active account owns."""
    start = datetime.now(UTC) - timedelta(days=10)
    first = _account(engine, tenant, "dom-op-first@example.com", created=start)
    second = _account(
        engine, tenant, "dom-op-second@example.com", created=start + timedelta(days=1)
    )
    third = _account(engine, tenant, "dom-op-third@example.com", created=start + timedelta(days=2))
    assert _allowed(engine, second) == []
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, tenant)
        row = session.get(User, uuid.UUID(first.id))
        assert row is not None
        row.is_active = False
        session.commit()
    assert _allowed(engine, second) == EVERYTHING
    assert _allowed(engine, third) == []
    with sessionmaker(bind=engine)() as session:
        assert team.is_owner(session=session, user=second)
        owners = [m.email for m in team.members(session=session, user=second) if m.owner]
    assert owners == [second.email]
    # The operator's way to choose otherwise: grant admin explicitly.
    _grant(engine, tenant, third.email, Role.ADMIN)
    assert _allowed(engine, _reload(engine, third)) == EVERYTHING
