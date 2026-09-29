"""Tests for `app/api/v1/routes/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The domain refuses every calculation until its sources are verified; what
belongs to this layer is that the refusal reaches the client as a 501 with
the reason, not as a 500 or a body shaped like a result.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import calculations as calculations_route
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
    from app.core.db import get_session
    from app.core.errors import install_exception_handlers

    app = FastAPI()
    app.include_router(calculations_route.router, prefix="/calculations")
    app.dependency_overrides[deps.get_current_user] = _engineer
    app.dependency_overrides[get_session] = lambda: None
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_cable_sizing_refuses_with_a_501_and_the_reason(client: TestClient) -> None:
    response = client.post(
        "/calculations/cable-sizing",
        json={
            "design_current_a": "100",
            "length_m": "50",
            "supply_voltage_v": "400",
            "installation_method": "E",
            "ambient_temp_c": "30",
        },
    )

    assert response.status_code == 501
    assert "AI-005" in response.text
    assert "conductor" not in response.json().get("result", {})


def test_vfd_selection_refuses_with_a_501(client: TestClient) -> None:
    response = client.post(
        "/calculations/vfd-selection",
        json={
            "motor_power_kw": "15",
            "supply_voltage_v": "400",
            "motor_efficiency": "0.92",
            "motor_power_factor": "0.85",
        },
    )

    assert response.status_code == 501
