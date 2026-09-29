"""Tests for `app/api/v1/routes/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import search as search_route
from app.core.errors import install_exception_handlers
from app.models.schemas.auth import CurrentUser, Role


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(search_route.router, prefix="/search")
    app.dependency_overrides[deps.get_current_user] = _user
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_search_answers_501_rather_than_500(client: TestClient) -> None:
    response = client.post("/search", json={"query": "F0001 overcurrent"})
    assert response.status_code == 501
    assert response.json()["error"] == "NotImplementedYetError"


def test_an_unbounded_top_k_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post("/search", json={"query": "q", "top_k": 10_000})
    assert response.status_code == 422
