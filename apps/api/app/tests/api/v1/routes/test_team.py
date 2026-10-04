"""Tests for `app/api/v1/routes/team.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import team as team_route
from app.core.db import get_session
from app.core.errors import AuthorizationError, install_exception_handlers
from app.domain import team as team_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.team import InvitationCreated, Member, Team


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


class _Session:
    def commit(self) -> None:
        pass


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(team_route.router, prefix="/team")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = _Session
    monkeypatch.setattr(
        team_domain,
        "team",
        lambda **_kw: Team(
            members=[Member(id="u", email="e@example.com", owner=True)],
            owner=True,
            seats=None,
            seats_taken=1,
        ),
    )
    monkeypatch.setattr(
        team_domain,
        "invite",
        lambda **kw: InvitationCreated(
            id="i", email=kw["email"], token="tok", expires_at="2026-10-18T00:00:00+00:00"
        ),
    )
    monkeypatch.setattr(team_domain, "invitations", lambda **_kw: [])

    def _refuse(**_kw: object) -> None:
        raise AuthorizationError("only the owner", code="team_owner_only")

    monkeypatch.setattr(team_domain, "revoke", lambda **_kw: None)
    monkeypatch.setattr(team_domain, "remove", _refuse)
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_the_team_and_an_invitation(client: TestClient) -> None:
    assert client.get("/team").json()["members"][0]["owner"] is True
    created = client.post("/team/invitations", json={"email": "c@example.com"})
    assert created.status_code == 201
    assert created.json()["token"] == "tok"
    assert client.get("/team/invitations").json() == []
    assert client.delete("/team/invitations/i").status_code == 204


def test_a_refused_removal_is_a_403(client: TestClient) -> None:
    response = client.delete("/team/members/m")
    assert response.status_code == 403
    assert response.json()["code"] == "team_owner_only"
