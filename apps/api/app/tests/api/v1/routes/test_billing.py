"""Tests for `app/api/v1/routes/billing.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import billing as billing_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.domain import billing as billing_domain
from app.domain.plans import Interval, PlanKey
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.billing import Entitlements, PlanRequest


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


class _Session:
    committed = False

    def commit(self) -> None:
        _Session.committed = True


def _shown(requested: PlanKey | None = None) -> Entitlements:
    return Entitlements(
        plan=PlanKey.FREE,
        interval=Interval.MONTHLY,
        status="free",
        seats=1,
        seats_used=1,
        model_calls_per_month=1000,
        model_calls_used=0,
        saved_projects=None,
        saved_projects_used=0,
        features=[],
        enforced=False,
        requested_plan=requested,
    )


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(billing_route.router, prefix="/billing")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = _Session
    monkeypatch.setattr(billing_domain, "entitlements", lambda **_kw: _shown())

    def _request(*, session: object, user: CurrentUser, request: PlanRequest) -> Entitlements:
        return _shown(request.plan)

    monkeypatch.setattr(billing_domain, "request_plan", _request)
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_the_plans_are_public(client: TestClient) -> None:
    response = client.get("/billing/plans")
    assert response.status_code == 200
    body = response.json()
    assert [p["key"] for p in body["plans"]][:2] == ["free", "engineer"]
    assert body["currency"] == "USD"


def test_entitlements_are_the_callers(client: TestClient) -> None:
    response = client.get("/billing/entitlements")
    assert response.status_code == 200
    assert response.json()["plan"] == "free"


def test_a_request_is_recorded_and_committed(client: TestClient) -> None:
    response = client.post("/billing/request", json={"plan": "team", "seats": 4})
    assert response.status_code == 200
    assert response.json()["requested_plan"] == "team"
    assert _Session.committed
