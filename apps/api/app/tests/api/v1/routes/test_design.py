"""Tests for `app/api/v1/routes/design.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import design as design_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.models.schemas.auth import CurrentUser, Role

BOARD = {
    "info": {"name": "Pocket"},
    "board": {
        "name": "DBG-HALL",
        "loads": [
            {"description": "Sockets", "load": "socket", "power_kw": "1.5"},
            {"description": "Lights", "load": "lighting", "power_kw": "0.6"},
        ],
    },
}


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = object
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_a_board_is_designed_then_exported(client: TestClient) -> None:
    response = client.post("/design/distribution-board", json=BOARD)
    assert response.status_code == 200
    project = response.json()["project"]
    assert project["boards"][0]["circuits"][0]["description"] == "Sockets"

    exported = client.post("/design/export", json={"project": project, "format": "pdf"})
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/pdf"
    assert exported.headers["content-disposition"] == 'attachment; filename="Pocket.pdf"'
    assert exported.content.startswith(b"%PDF")


def test_a_bad_profile_is_a_client_error(client: TestClient) -> None:
    response = client.post(
        "/design/distribution-board", json={**BOARD, "profile": {"key": "x", "bogus": 1}}
    )
    assert response.status_code in (400, 422)


def test_an_unknown_format_is_refused(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    assert (
        client.post("/design/export", json={"project": project, "format": "dwg"}).status_code == 422
    )


def test_a_schedule_is_uploaded(client: TestClient) -> None:
    response = client.post(
        "/design/load-schedule/import",
        files={"file": ("schedule.csv", b"Description,kW\nPump,2.2\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["loads"][0]["load"] == "motor"
    assert body["warnings"] == [
        {
            "code": "import_kind_inferred",
            "params": {"row": "2", "load": "Pump", "kind": "motor"},
            "text": "Row 2 (Pump): taken as motor from its description.",
        }
    ]


def test_an_unreadable_schedule_is_a_client_error(client: TestClient) -> None:
    response = client.post(
        "/design/load-schedule/import",
        files={"file": ("x.csv", b"Name,Colour\n", "text/csv")},
    )
    assert response.status_code in (400, 422)


def test_a_suggestion_is_charged_to_the_month(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.domain import design as design_domain
    from app.domain import model_budget
    from app.models.schemas.design import DesignNote, LoadScheduleSuggestion

    charged: list[str] = []
    committed: list[bool] = []

    def charge(**kwargs: object) -> None:
        charged.append(str(kwargs["tenant_id"]))

    def suggest(**kwargs: object) -> LoadScheduleSuggestion:
        del kwargs
        return LoadScheduleSuggestion(
            loads=[], assumptions=[DesignNote(code="text", params={"text": "x"}, text="x")]
        )

    monkeypatch.setattr(model_budget, "charge_model_call", charge)
    monkeypatch.setattr(design_domain, "suggest_load_schedule", suggest)
    app = client.app
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None  # type: ignore[attr-defined]
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(  # type: ignore[attr-defined]
        commit=lambda: committed.append(True)
    )
    response = client.post("/design/load-schedule/suggest", json={"description": "a hall"})
    assert response.status_code == 200
    assert response.json() == {
        "loads": [],
        "assumptions": [{"code": "text", "params": {"text": "x"}, "text": "x"}],
    }
    assert charged == ["t"]
    assert committed == [True]


def test_a_board_is_priced(client: TestClient) -> None:
    project = client.post("/design/distribution-board", json=BOARD).json()["project"]
    response = client.post(
        "/design/quotation",
        json={
            "project": project,
            "pricing": {"price_list": [{"key": "circuit_breaker:1P:C16", "unit_price": "4"}]},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "JOD"
    assert body["complete"] is False


def test_a_price_list_is_uploaded(client: TestClient) -> None:
    response = client.post(
        "/design/price-list/import",
        files={"file": ("prices.csv", b"Key,Price\nX,2\n", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == [{"key": "X", "description": "", "unit_price": "2"}]


def test_a_plc_program_is_written(client: TestClient) -> None:
    board = {
        "info": {"name": "Pocket"},
        "board": {
            "name": "DBG-HALL",
            "loads": [
                {
                    "description": "Lights",
                    "load": "lighting",
                    "power_kw": "0.6",
                    "controlled": True,
                }
            ],
        },
    }
    project = client.post("/design/distribution-board", json=board).json()["project"]
    response = client.post("/design/plc", json={"project": project})
    assert response.status_code == 200
    body = response.json()
    assert body["validation"]["status"] == "valid"
    assert "END_PROGRAM" in body["source"]
    refused = client.post(
        "/design/plc",
        json={"project": client.post("/design/distribution-board", json=BOARD).json()["project"]},
    )
    assert refused.status_code == 422


def test_a_project_of_boards_is_designed(client: TestClient) -> None:
    loads = [{"description": "Lights", "load": "lighting", "power_kw": "1"}]
    response = client.post(
        "/design/project",
        json={
            "info": {"name": "Tower"},
            "boards": [
                {"name": "MDB", "loads": loads},
                {"name": "DB-1", "loads": loads, "fed_from": "MDB"},
            ],
        },
    )
    assert response.status_code == 200
    boards = response.json()["project"]["boards"]
    assert [b["name"] for b in boards] == ["MDB", "DB-1"]
    looped = client.post(
        "/design/project",
        json={
            "info": {"name": "Tower"},
            "boards": [{"name": "MDB", "loads": loads, "fed_from": "MDB"}],
        },
    )
    assert looped.status_code == 422
