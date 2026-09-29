"""Tests for `app/api/v1/routes/schematics.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
The domain is exercised in `app/tests/domain/test_schematics.py`; what belongs
here is the HTTP contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import schematics as schematics_route
from app.models.schemas.auth import CurrentUser, Role


def _engineer() -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        email="e@example.com",
        tenant_id="00000000-0000-0000-0000-000000000007",
        roles=frozenset({Role.ENGINEER}),
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.api import deps
    from app.core.errors import install_exception_handlers

    app = FastAPI()
    app.include_router(schematics_route.router, prefix="/schematics")
    app.dependency_overrides[deps.get_current_user] = _engineer
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


_SCHEDULE: dict[str, Any] = {
    "title": "MCC-1",
    "supply": "400 V 3~ 50 Hz",
    "lines": [
        {"designator": "Q0", "kind": "isolator", "mounting": "door"},
        {"designator": "Q1", "kind": "circuit-breaker", "feeds_from": "Q0"},
    ],
}


def test_a_schedule_returns_its_specification(client: TestClient) -> None:
    response = client.post("/schematics", json=_SCHEDULE)

    assert response.status_code == 200
    body = response.json()
    assert body["incomer"] == "Q0"
    assert body["connections"][0]["conductor"]["status"] == "not_calculated"
    assert body["trunking"]["blocked_by"] == "PD-004"


def test_an_ambiguous_topology_is_a_422_naming_the_problem(client: TestClient) -> None:
    schedule = {**_SCHEDULE, "lines": [{**line, "feeds_from": None} for line in _SCHEDULE["lines"]]}

    response = client.post("/schematics", json=schedule)

    assert response.status_code == 422
    assert "ambiguous incomer" in response.text


def test_a_malformed_schedule_is_rejected_by_the_schema(client: TestClient) -> None:
    assert client.post("/schematics", json={"title": "t"}).status_code == 422
