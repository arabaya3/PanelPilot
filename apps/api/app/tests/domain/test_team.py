"""Tests for `app/domain/team.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.tenancy import cross_tenant
from app.domain import auth, billing, team
from app.models.schemas.auth import CurrentUser
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
