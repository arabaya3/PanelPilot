"""Tests for `app/domain/billing.py`.

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
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.tenancy import cross_tenant
from app.domain import billing
from app.domain.plans import Feature, Interval, PlanKey
from app.models.schemas.auth import CurrentUser
from app.models.schemas.billing import PlanRequest
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

_NOW = datetime(2026, 10, 4, tzinfo=UTC)


def test_the_catalogue_lists_every_plan_with_annual_prices() -> None:
    shown = billing.catalogue()
    keys = [p.key for p in shown.plans]
    assert keys == [
        PlanKey.FREE,
        PlanKey.ENGINEER,
        PlanKey.TEAM,
        PlanKey.COMPANY,
        PlanKey.ENTERPRISE,
    ]
    team = next(p for p in shown.plans if p.key is PlanKey.TEAM)
    assert team.annual_usd == 790
    assert next(p for p in shown.plans if p.key is PlanKey.ENTERPRISE).annual_usd is None
    assert shown.annual_months_charged == 10


class _Settings:
    def __init__(self, enforced: bool) -> None:
        self.billing_enforced = enforced
        self.model_calls_per_month = 1000


def _enforce(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(billing, "get_settings", lambda: _Settings(on))
    monkeypatch.setattr(billing, "enforced", lambda: on)


def test_enforced_reads_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(billing, "get_settings", lambda: _Settings(True))
    assert billing.enforced()


def test_nothing_is_refused_until_billing_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    _enforce(monkeypatch, False)
    user = CurrentUser(id="u", email="e@x.com", tenant_id=str(uuid.uuid4()), roles=frozenset())
    # No session is touched: an unenforced check returns at once.
    billing.require_feature(session=None, user=user, feature=Feature.ECAD_EXPORT)  # type: ignore[arg-type]
    billing.require_project_room(session=None, user=user)  # type: ignore[arg-type]
    assert billing.model_call_ceiling(session=None, tenant_id=uuid.uuid4()) == 1000  # type: ignore[arg-type]


needs_db = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: tenant isolation is the database's",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenant")
def _tenant(engine: Engine) -> uuid.UUID:
    with sessionmaker(bind=engine)() as session:
        session.execute(text("TRUNCATE subscriptions"))
        found = session.execute(
            text("SELECT id FROM tenants WHERE slug = 'billing-a'")
        ).scalar_one_or_none()
        if found is None:
            row = TenantRow(slug="billing-a", name="Billing A")
            session.add(row)
            session.flush()
            found = row.id
        session.commit()
        return found


def _user(tenant: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=str(uuid.uuid4()), email="eng@example.com", tenant_id=str(tenant), roles=frozenset()
    )


def _activate(session: Session, plan: str, until: datetime | None, seats: int = 1) -> None:
    with cross_tenant(session, reason="test activates a plan"):
        billing.activate(
            session=session,
            tenant_slug="billing-a",
            plan_key=plan,
            interval="annual",
            seats=seats,
            until=until,
        )
        session.commit()


@needs_db
def test_a_tenant_without_a_subscription_is_on_free(tenant, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _enforce(monkeypatch, True)
    with sessionmaker(bind=engine)() as session:
        shown = billing.entitlements(session=session, user=_user(tenant), now=_NOW)
        assert shown.plan is PlanKey.FREE
        assert shown.status == "free"
        assert shown.model_calls_per_month == 30
        assert Feature.ECAD_EXPORT not in shown.features
        with pytest.raises(AuthorizationError) as caught:
            billing.require_feature(
                session=session, user=_user(tenant), feature=Feature.ECAD_EXPORT
            )
        assert caught.value.code == "plan_feature"


@needs_db
def test_a_request_changes_nothing_until_activated(tenant, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _enforce(monkeypatch, True)
    with sessionmaker(bind=engine)() as session:
        asked = billing.request_plan(
            session=session,
            user=_user(tenant),
            request=PlanRequest(plan=PlanKey.TEAM, interval=Interval.ANNUAL, seats=4),
            now=_NOW,
        )
        session.commit()
        assert asked.plan is PlanKey.FREE
        assert asked.requested_plan is PlanKey.TEAM
        assert asked.requested_seats == 4

        _activate(session, "team", _NOW + timedelta(days=365), seats=4)
        shown = billing.entitlements(session=session, user=_user(tenant), now=_NOW)
        assert shown.plan is PlanKey.TEAM
        assert shown.status == "active"
        assert shown.seats == 4
        assert shown.requested_plan is None
        assert shown.model_calls_per_month == 2000
        billing.require_feature(session=session, user=_user(tenant), feature=Feature.ECAD_EXPORT)


@needs_db
def test_an_ended_period_falls_back_to_free(tenant, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _enforce(monkeypatch, True)
    with sessionmaker(bind=engine)() as session:
        _activate(session, "engineer", _NOW - timedelta(days=1))
        shown = billing.entitlements(session=session, user=_user(tenant), now=_NOW)
        assert shown.plan is PlanKey.FREE
        assert shown.status == "expired"


@needs_db
def test_requests_and_activation_refuse_what_no_plan_holds(tenant, engine) -> None:  # type: ignore[no-untyped-def]
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(ValidationError):
            billing.request_plan(
                session=session, user=_user(tenant), request=PlanRequest(plan=PlanKey.FREE)
            )
        with pytest.raises(ValidationError):
            billing.request_plan(
                session=session,
                user=_user(tenant),
                request=PlanRequest(plan=PlanKey.ENGINEER, seats=3),
            )
        with cross_tenant(session, reason="test"), pytest.raises(NotFoundError):
            billing.activate(
                session=session,
                tenant_slug="no-such-tenant",
                plan_key="team",
                interval="monthly",
                seats=3,
                until=None,
            )


@needs_db
def test_the_free_plan_keeps_three_projects(tenant, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _enforce(monkeypatch, True)
    with sessionmaker(bind=engine)() as session:
        session.execute(
            text("DELETE FROM design_project_revisions WHERE tenant_id = :t"), {"t": tenant}
        )
        session.execute(text("DELETE FROM design_projects WHERE tenant_id = :t"), {"t": tenant})
        for i in range(3):
            session.execute(
                text(
                    "INSERT INTO design_projects (id, tenant_id, name, revision_count, created_at,"
                    " updated_at) VALUES (gen_random_uuid(), :t, :n, 0, now(), now())"
                ),
                {"t": tenant, "n": f"P{i}"},
            )
        session.commit()
        with pytest.raises(AuthorizationError) as caught:
            billing.require_project_room(session=session, user=_user(tenant))
        assert caught.value.code == "plan_projects"
