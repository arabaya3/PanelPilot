"""Tests for `app/api/v1/routes/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import calculations as calculations_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.models.schemas.auth import CurrentUser, Role


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(calculations_route.router, prefix="/calculations")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = object
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_a_blocked_calculation_answers_501_with_its_reason(client: TestClient) -> None:
    """Not an anonymous 500: the caller learns this is known and why."""
    response = client.post(
        "/calculations/vfd-selection",
        json={
            "motor_power_kw": "7.5",
            "supply_voltage_v": "400",
            "motor_efficiency": "0.9",
            "motor_power_factor": "0.85",
        },
    )
    assert response.status_code == 501
    assert response.json()["error"] == "NotImplementedYetError"
    assert "engineering guides" in response.json()["detail"]


def test_a_cable_is_sized_with_its_sources(client: TestClient) -> None:
    response = client.post(
        "/calculations/cable-sizing",
        json={
            "design_current_a": "32",
            "length_m": "40",
            "supply_voltage_v": "400",
            "installation_method": "B1",
            "ambient_temp_c": "30",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # B1, XLPE, three loaded: 4 mm2 carries 37 A.
    assert body["result"]["cross_section_mm2"] == "4"
    assert {s["manufacturer"] for s in body["sources"]} == {"ABB", "Schneider Electric"}


def test_an_off_table_input_is_a_422_not_a_guess(client: TestClient) -> None:
    response = client.post(
        "/calculations/cable-sizing",
        json={
            "design_current_a": "32",
            "length_m": "40",
            "supply_voltage_v": "400",
            "installation_method": "D1",
            "ambient_temp_c": "30",
        },
    )
    assert response.status_code == 422
