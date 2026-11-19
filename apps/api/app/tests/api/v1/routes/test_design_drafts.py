"""The server-backed draft routes: their own rate budget, refusals and body handling.

The database behaviour (privacy, concurrency, retries, purge) is tested in
``tests/domain/test_design_drafts.py``; here the routes run with a stub
session, so only what is decided before the database is reached is checked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import design_drafts as drafts_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.domain.rate_limit import DRAFT_POLICY, InMemoryRateLimitStore
from app.models.schemas.auth import CurrentUser, Role


def _trial() -> CurrentUser:
    return CurrentUser(id="trial", email="", tenant_id=str(uuid.uuid4()), roles=frozenset())


def _member() -> CurrentUser:
    return CurrentUser(
        id=str(uuid.uuid4()),
        email="e@example.com",
        tenant_id=str(uuid.uuid4()),
        roles=frozenset({Role.ENGINEER}),
    )


def _client(user: object) -> TestClient:
    app = FastAPI()
    app.include_router(drafts_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = user  # type: ignore[assignment]
    app.dependency_overrides[get_session] = object
    store = InMemoryRateLimitStore()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store
    install_exception_handlers(app)
    return TestClient(app)


@pytest.fixture(name="trial_client")
def _trial_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DESIGN_SERVER_DRAFTS", "true")
    with _client(_trial) as client:
        yield client


def _write(body: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "request_id": str(uuid.uuid4()),
        "issued_at": datetime.now(UTC).isoformat(),
        "if_match": None,
        "base_revision": 1,
        "body": body
        or {
            "clean": "",
            "info": {
                "name": "Secret name",
                "number": "",
                "customer": "",
                "consultant": "",
                "contractor": "",
            },
            "boards": [{"name": "DB1"}],
            "profileText": "",
            "market": "",
        },
    }


def test_a_trial_is_refused_before_the_database(trial_client: TestClient) -> None:
    response = trial_client.put(f"/design/projects/{uuid.uuid4()}/draft", json=_write())
    assert response.status_code == 403
    assert response.json()["code"] == "server_drafts_need_account"


def test_writes_are_refused_while_the_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_SERVER_DRAFTS", "false")
    with _client(_member) as client:
        response = client.put(f"/design/projects/{uuid.uuid4()}/draft", json=_write())
    assert response.status_code == 403
    assert response.json()["code"] == "server_drafts_disabled"


def test_a_malformed_body_is_refused_without_echoing_it(trial_client: TestClient) -> None:
    write = _write()
    write["body"] = {"info": {"name": "Secret name"}, "unknown": "Secret field"}
    response = trial_client.put(f"/design/projects/{uuid.uuid4()}/draft", json=write)
    assert response.status_code == 422
    assert "Secret" not in response.text


def test_drafts_have_their_own_rate_budget() -> None:
    assert DRAFT_POLICY.namespace == "design-drafts"
    dependencies = [d.dependency for d in drafts_route.router.dependencies]
    assert deps.enforce_draft_rate_limit in dependencies
    assert deps.enforce_design_rate_limit not in dependencies
