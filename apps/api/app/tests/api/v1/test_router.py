"""Tests for `app/api/v1/router.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

What the router owns is which limit applies where. Asserted by behaviour: a
spy store records which namespace each request was counted under, so the
test does not depend on how the framework represents attached dependencies.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.core.db import get_session
from app.domain.rate_limit import WindowDecision
from app.main import create_app


class _SpyStore:
    """Admits everything and remembers which namespace counted each request."""

    def __init__(self) -> None:
        self.namespaces: list[str] = []

    def record_if_allowed(
        self, key: str, *, now: float, window_seconds: int, limit: int  # noqa: ARG002
    ) -> WindowDecision:
        self.namespaces.append(key.split(":", 1)[0])
        return WindowDecision(allowed=True, count=1, oldest=now)


@pytest.fixture
def spy() -> _SpyStore:
    return _SpyStore()


@pytest.fixture
def client(settings: Settings, spy: _SpyStore) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: spy
    # Whatever the handler does after the limit is not under test; a session
    # that cannot do anything turns every such path into a quick error.
    app.dependency_overrides[get_session] = object
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


_LOGIN = {"email": "a@example.com", "password": "p"}
_SIGNUP = {"email": "a@example.com", "password": "a-long-password-1"}
_RESUME = {"session_id": str(uuid.uuid4()), "claim_secret": "s"}


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("POST", "/api/v1/auth/trial", None, ["auth-trial"]),
        ("POST", "/api/v1/auth/trial/resume", _RESUME, ["auth-trial"]),
        ("POST", "/api/v1/auth/login", _LOGIN, ["auth-login-ip", "auth-login-account"]),
        ("POST", "/api/v1/auth/signup", _SIGNUP, ["auth-signup"]),
        # Refresh needs a live single-use token already; quota is a read.
        ("POST", "/api/v1/auth/refresh", {"refresh_token": "t"}, []),
        ("GET", "/api/v1/auth/quota", None, []),
        ("POST", "/api/v1/diagnostics", {"symptom": "trips"}, ["trial"]),
        # Reading a conversation spends nothing.
        ("GET", f"/api/v1/diagnostics/{uuid.uuid4()}", None, []),
        ("POST", "/api/v1/plc/review", {"source": "x", "dialect": "st"}, ["trial"]),
        ("GET", "/api/v1/health/live", None, []),
    ],
)
def test_each_endpoint_is_counted_under_the_intended_limit(
    client: TestClient,
    spy: _SpyStore,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: list[str],
) -> None:
    client.request(method, path, json=body)
    assert spy.namespaces == expected


def test_image_upload_is_counted_under_the_trial_limit(client: TestClient, spy: _SpyStore) -> None:
    client.post("/api/v1/images", files={"file": ("a.jpg", b"\xff\xd8\xff")})
    assert spy.namespaces == ["trial"]
