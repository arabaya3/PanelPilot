"""Tests for `app/domain/team.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.tenancy import cross_tenant
from app.domain import auth, billing, roles, team
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: tenant isolation is the database's",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signup's tokens need the signing key; joining the team is what is tested."""
    monkeypatch.setattr(auth, "_issue_tokens", lambda **_kw: None)


@pytest.fixture(name="owner")
def _owner(engine: Engine) -> CurrentUser:
    """A fresh tenant with one account, its owner."""
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test setup"):
        tenant = TenantRow(slug=f"team-{uuid.uuid4().hex[:8]}", name="Team")
        session.add(tenant)
        session.flush()
        user = User(tenant_id=tenant.id, email=f"own-{uuid.uuid4().hex[:8]}@x.com", is_active=True)
        session.add(user)
        session.flush()
        tenant.owner_user_id = user.id
        session.commit()
        return CurrentUser(
            id=str(user.id), email=user.email, tenant_id=str(tenant.id), roles=frozenset()
        )


def _session(engine: Engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=engine)()


def test_the_owner_invites_and_the_colleague_joins(owner, engine) -> None:  # type: ignore[no-untyped-def]
    colleague = f"col-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email=colleague.upper())
        session.commit()
        assert created.email == colleague
        assert [i.email for i in team.invitations(session=session, user=owner)] == [colleague]

    with _session(engine) as session:
        auth.signup(
            session=session,
            email=colleague,
            password="a-long-password-1",
            invite_token=created.token,
        )
        session.commit()

    with _session(engine) as session:
        shown = team.team(session=session, user=owner)
        assert [m.email for m in shown.members] == [owner.email, colleague]
        assert shown.members[0].owner
        assert shown.owner
        assert team.invitations(session=session, user=owner) == []

    # Used once only.
    with _session(engine) as session, pytest.raises(ValidationError) as caught:
        auth.signup(
            session=session,
            email=f"other-{uuid.uuid4().hex[:8]}@x.com",
            password="a-long-password-1",
            invite_token=created.token,
        )
    assert caught.value.code == "team_invitation_invalid"


def test_an_invitation_is_for_its_email_only(owner, engine) -> None:  # type: ignore[no-untyped-def]
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email="right@x.com")
        session.commit()
    with _session(engine) as session, pytest.raises(ValidationError) as caught:
        auth.signup(
            session=session,
            email=f"wrong-{uuid.uuid4().hex[:8]}@x.com",
            password="a-long-password-1",
            invite_token=created.token,
        )
    assert caught.value.code == "team_invitation_email"


def test_an_expired_invitation_is_refused(owner, engine) -> None:  # type: ignore[no-untyped-def]
    past = datetime.now(UTC) - timedelta(days=30)
    address = f"late-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email=address, now=past)
        session.commit()
    with (
        _session(engine) as session,
        cross_tenant(session, reason="test"),
        pytest.raises(ValidationError),
    ):
        team.accept(session=session, token=created.token, email=address)


def test_only_the_owner_manages_the_team(owner, engine) -> None:  # type: ignore[no-untyped-def]
    with _session(engine) as session, cross_tenant(session, reason="test setup"):
        member = User(
            tenant_id=uuid.UUID(owner.tenant_id),
            email=f"mem-{uuid.uuid4().hex[:8]}@x.com",
            is_active=True,
        )
        session.add(member)
        session.commit()
        other = CurrentUser(
            id=str(member.id), email=member.email, tenant_id=owner.tenant_id, roles=frozenset()
        )
    with _session(engine) as session:
        with pytest.raises(AuthorizationError):
            team.invite(session=session, user=other, email="x@x.com")
        assert not team.team(session=session, user=other).owner
        with pytest.raises(ValidationError):
            team.remove(session=session, user=owner, member_id=owner.id)
        team.remove(session=session, user=owner, member_id=other.id)
        session.commit()
        assert [m.email for m in team.members(session=session, user=owner)] == [owner.email]
        with pytest.raises(NotFoundError):
            team.remove(session=session, user=owner, member_id=other.id)


def test_seats_are_held_once_billing_is_enforced(owner, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(billing, "enforced", lambda: True)
    with _session(engine) as session:
        # Free: one seat, the owner's.
        with pytest.raises(ValidationError) as caught:
            team.invite(session=session, user=owner, email="full@x.com")
        assert caught.value.code == "team_seats_full"


def test_an_invitation_is_withdrawn_and_an_existing_member_refused(owner, engine) -> None:  # type: ignore[no-untyped-def]
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email="gone@x.com")
        session.commit()
        team.revoke(session=session, user=owner, invitation_id=created.id)
        session.commit()
        assert team.invitations(session=session, user=owner) == []
        with pytest.raises(NotFoundError):
            team.revoke(session=session, user=owner, invitation_id=created.id)
        with pytest.raises(ValidationError) as caught:
            team.invite(session=session, user=owner, email=owner.email)
        assert caught.value.code == "team_member_exists"
        assert team.seats_taken(session=session) == 1
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_an_invitation_counts_only_its_own_tenants_seats(owner, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Signup accepts an invitation with the tenant filter lifted: the seat check must not be.

    With two tenants holding subscriptions, reading "the" subscription across
    tenants found several (a 500), and counting members across tenants filled
    a plan that has room.
    """
    from app.models.tables.billing import SubscriptionRow

    until = datetime.now(UTC) + timedelta(days=365)
    with _session(engine) as session, cross_tenant(session, reason="test setup"):
        other = TenantRow(slug=f"team-other-{uuid.uuid4().hex[:8]}", name="Other")
        session.add(other)
        session.flush()
        # Another tenant, crowded: many members on a one-seat subscription.
        for _ in range(3):
            session.add(
                User(tenant_id=other.id, email=f"o-{uuid.uuid4().hex[:8]}@x.com", is_active=True)
            )
        session.add(
            SubscriptionRow(tenant_id=other.id, plan="team", seats=1, current_period_end=until)
        )
        session.add(
            SubscriptionRow(
                tenant_id=uuid.UUID(owner.tenant_id), plan="team", seats=3, current_period_end=until
            )
        )
        session.commit()

    monkeypatch.setattr(billing, "enforced", lambda: True)
    colleague = f"col-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email=colleague)
        session.commit()
    with _session(engine) as session:
        auth.signup(
            session=session,
            email=colleague,
            password="a-long-password-1",
            invite_token=created.token,
        )
        session.commit()
    with _session(engine) as session:
        assert colleague in [m.email for m in team.members(session=session, user=owner)]


# --------------------------------------------------------------------------- #
# The recorded owner: signup, transfer, the operator's remedy
# --------------------------------------------------------------------------- #


def _member(engine: Engine, owner: CurrentUser, *, admin: bool = False) -> CurrentUser:
    """Another active account in the owner's tenant, roles as stored."""
    email = f"mem-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session, cross_tenant(session, reason="test setup"):
        row = User(tenant_id=uuid.UUID(owner.tenant_id), email=email, is_active=True)
        session.add(row)
        session.flush()
        if admin:
            roles.grant_role(session=session, email=email, role=Role.ADMIN)
        held = roles.roles_of(row)
        session.commit()
        return CurrentUser(id=str(row.id), email=email, tenant_id=owner.tenant_id, roles=held)


def _owner_of(engine: Engine, tenant_id: str) -> uuid.UUID | None:
    with engine.connect() as conn:
        found: object = conn.execute(
            text("SELECT owner_user_id FROM tenants WHERE id = :t"), {"t": uuid.UUID(tenant_id)}
        ).scalar_one()
    return None if found is None else uuid.UUID(str(found))


def test_signup_records_the_new_tenants_owner_and_an_invitee_is_not(engine) -> None:  # type: ignore[no-untyped-def]
    email = f"new-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session:
        auth.signup(session=session, email=email, password="a-long-password-1")
        session.commit()
    with _session(engine) as session, cross_tenant(session, reason="test"):
        row = session.scalars(select(User).where(User.email == email)).one()
        tenant = session.get(TenantRow, row.tenant_id)
        assert tenant is not None
        assert tenant.owner_user_id == row.id
        owner = CurrentUser(
            id=str(row.id), email=email, tenant_id=str(row.tenant_id), roles=frozenset()
        )
    with _session(engine) as session:
        created = team.invite(session=session, user=owner, email=f"inv-{email}")
        session.commit()
    with _session(engine) as session:
        auth.signup(
            session=session,
            email=f"inv-{email}",
            password="a-long-password-1",
            invite_token=created.token,
        )
        session.commit()
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)


def test_claiming_a_trial_records_its_owner(engine) -> None:  # type: ignore[no-untyped-def]
    with _session(engine) as session:
        trial = auth.start_trial(session=session, access_token_ttl_seconds=60)
        session.commit()
    email = f"claim-{uuid.uuid4().hex[:8]}@x.com"
    with _session(engine) as session:
        auth.signup(
            session=session,
            email=email,
            password="a-long-password-1",
            claim_session_id=trial.session_id,
            claim_secret=trial.claim_secret,
        )
        session.commit()
    with _session(engine) as session, cross_tenant(session, reason="test"):
        row = session.scalars(select(User).where(User.email == email)).one()
        tenant = session.get(TenantRow, row.tenant_id)
        assert tenant is not None
        assert tenant.owner_user_id == row.id


def test_the_owner_transfers_ownership_and_loses_it(owner, engine) -> None:  # type: ignore[no-untyped-def]
    member = _member(engine, owner)
    with _session(engine) as session:
        team.transfer_ownership(session=session, user=owner, member_id=member.id)
        session.commit()
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(member.id)
    with _session(engine) as session:
        shown = team.team(session=session, user=member)
        assert shown.owns
        assert shown.owner
        assert [m.id for m in shown.members if m.owner] == [member.id]
        assert shown.members[0].id == member.id
        before = team.team(session=session, user=owner)
        assert not before.owns
        assert not before.owner
        # The former owner is a plain member now: it manages nothing.
        with pytest.raises(AuthorizationError) as refused:
            team.transfer_ownership(session=session, user=owner, member_id=member.id)
        assert refused.value.code == "team_transfer_owner_only"
        session.rollback()
        with pytest.raises(AuthorizationError):
            team.invite(session=session, user=owner, email="x@x.com")


def test_an_admin_cannot_transfer_nor_remove_the_owner(owner, engine) -> None:  # type: ignore[no-untyped-def]
    admin = _member(engine, owner, admin=True)
    member = _member(engine, owner)
    with _session(engine) as session:
        with pytest.raises(AuthorizationError) as refused:
            team.transfer_ownership(session=session, user=admin, member_id=admin.id)
        assert refused.value.code == "team_transfer_owner_only"
        session.rollback()
        with pytest.raises(ValidationError) as kept:
            team.remove(session=session, user=admin, member_id=owner.id)
        assert kept.value.code == "team_owner_not_removable"
        session.rollback()
        # Still an admin: it removes a plain member.
        team.remove(session=session, user=admin, member_id=member.id)
        session.commit()
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)


@pytest.mark.parametrize("target", ["self", "inactive", "other_tenant", "missing", "malformed"])
def test_a_transfer_needs_another_active_member_of_the_tenant(owner, engine, target) -> None:  # type: ignore[no-untyped-def]
    if target == "self":
        member_id = owner.id
    elif target == "inactive":
        gone = _member(engine, owner)
        with _session(engine) as session:
            team.remove(session=session, user=owner, member_id=gone.id)
            session.commit()
        member_id = gone.id
    elif target == "other_tenant":
        with _session(engine) as session, cross_tenant(session, reason="test setup"):
            other = TenantRow(slug=f"team-x-{uuid.uuid4().hex[:8]}", name="X")
            session.add(other)
            session.flush()
            stranger = User(tenant_id=other.id, email=f"s-{uuid.uuid4().hex[:8]}@x.com")
            session.add(stranger)
            session.commit()
            member_id = str(stranger.id)
    elif target == "missing":
        member_id = str(uuid.uuid4())
    else:
        member_id = "not-a-uuid"
    with (
        _session(engine) as session,
        pytest.raises((ValidationError, NotFoundError)) as refused,
    ):
        team.transfer_ownership(session=session, user=owner, member_id=member_id)
    expected = "team_transfer_self" if target == "self" else "team_member_not_found"
    assert refused.value.code == expected
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)


def test_the_operator_assigns_an_owner_only_where_none_is_active(owner, engine) -> None:  # type: ignore[no-untyped-def]
    member = _member(engine, owner)
    with _session(engine) as session, cross_tenant(session, reason="test"):
        with pytest.raises(ValidationError) as present:
            team.assign_owner(session=session, email=member.email)
        assert present.value.code == "team_owner_present"
        session.rollback()
        # The owner deactivated outside the API: an operator, in the database.
        session.execute(
            text("UPDATE users SET is_active = false WHERE id = :u"), {"u": uuid.UUID(owner.id)}
        )
        session.commit()
    # Ownership did not pass silently to anyone.
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)
    with _session(engine) as session:
        assert not team.team(session=session, user=member).owner
    with _session(engine) as session, cross_tenant(session, reason="test"):
        issues = {i.tenant_id: i for i in team.ownership_report(session=session)}
        assert issues[owner.tenant_id].issue == "owner_inactive"
        assert issues[owner.tenant_id].active_admins == 0
        with pytest.raises(NotFoundError):
            team.assign_owner(session=session, email=owner.email)
        session.rollback()
        assigned = team.assign_owner(session=session, email=member.email.upper())
        assert assigned == uuid.UUID(owner.tenant_id)
        session.commit()
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(member.id)
    with _session(engine) as session, cross_tenant(session, reason="test"):
        assert owner.tenant_id not in {i.tenant_id for i in team.ownership_report(session=session)}


def test_the_report_lists_a_tenant_without_owner_and_its_admins(owner, engine) -> None:  # type: ignore[no-untyped-def]
    admin = _member(engine, owner, admin=True)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tenants SET owner_user_id = NULL WHERE id = :t"),
            {"t": uuid.UUID(owner.tenant_id)},
        )
    with _session(engine) as session, cross_tenant(session, reason="test"):
        issues = {i.tenant_id: i for i in team.ownership_report(session=session)}
    assert issues[owner.tenant_id].issue == "no_owner"
    assert issues[owner.tenant_id].active_admins == 1
    # Without an owner, the oldest account is a plain member; the admin manages.
    with _session(engine) as session:
        assert not team.team(session=session, user=owner).owner
        shown = team.team(session=session, user=admin)
        assert shown.owner
        assert not shown.owns


@pytest.mark.parametrize("spelled", [str.upper, lambda value: "{" + value + "}"])
def test_a_self_id_spelled_otherwise_is_still_the_caller(owner, engine, spelled) -> None:  # type: ignore[no-untyped-def]
    """The self checks compare the account looked up, not the string sent."""
    admin = _member(engine, owner, admin=True)
    with _session(engine) as session, pytest.raises(ValidationError) as removed:
        team.remove(session=session, user=admin, member_id=spelled(admin.id))
    assert removed.value.code == "team_remove_self"
    with _session(engine) as session, pytest.raises(ValidationError) as transferred:
        team.transfer_ownership(session=session, user=owner, member_id=spelled(owner.id))
    assert transferred.value.code == "team_transfer_self"
    with _session(engine) as session, cross_tenant(session, reason="test"):
        assert session.scalar(select(User.is_active).where(User.id == uuid.UUID(admin.id)))
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)


def test_the_operator_assigns_no_owner_in_a_deactivated_tenant(owner, engine) -> None:  # type: ignore[no-untyped-def]
    with _session(engine) as session, cross_tenant(session, reason="test"):
        session.execute(
            text("UPDATE users SET is_active = false WHERE id = :u"), {"u": uuid.UUID(owner.id)}
        )
        session.execute(
            text("UPDATE tenants SET is_active = false WHERE id = :t"),
            {"t": uuid.UUID(owner.tenant_id)},
        )
        session.commit()
    member = _member(engine, owner)
    with _session(engine) as session, cross_tenant(session, reason="test"):
        with pytest.raises(ValidationError) as refused:
            team.assign_owner(session=session, email=member.email)
        assert refused.value.code == "tenant_inactive"
    assert _owner_of(engine, owner.tenant_id) == uuid.UUID(owner.id)
