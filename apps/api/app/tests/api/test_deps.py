"""Tests for `app/api/deps.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The dependency wiring is thin, so what is worth testing is the part that is
security-relevant: which address the rate limiter counts against.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import RateLimitBackend
from app.core.errors import install_exception_handlers
from app.domain.rate_limit import (
    LOGIN_ACCOUNT_POLICY,
    SIGNUP_POLICY,
    TRIAL_REQUESTS_PER_WINDOW,
    TRIAL_START_POLICY,
    InMemoryRateLimitStore,
    RateLimitPolicy,
    RedisRateLimitStore,
)
from app.models.schemas.auth_flows import LoginRequest


@pytest.fixture
def store() -> InMemoryRateLimitStore:
    return InMemoryRateLimitStore()


@pytest.fixture
def client(store: InMemoryRateLimitStore) -> Iterator[TestClient]:
    app = FastAPI()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store

    @app.get("/limited", dependencies=[Depends(deps.enforce_trial_rate_limit)])
    def limited() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/mixed", dependencies=[Depends(deps.enforce_trial_rate_limit_on_writes)])
    def mixed_read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mixed", dependencies=[Depends(deps.enforce_trial_rate_limit_on_writes)])
    def mixed_write() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/trial", dependencies=[Depends(deps.enforce_trial_start_rate_limit)])
    def trial() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/signup", dependencies=[Depends(deps.enforce_signup_rate_limit)])
    def signup() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/login", dependencies=[Depends(deps.enforce_login_rate_limit)])
    def login(payload: LoginRequest) -> dict[str, str]:
        return {"email": payload.email}

    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_a_normal_request_passes(client: TestClient) -> None:
    assert client.get("/limited").status_code == 200


def test_a_burst_is_throttled(client: TestClient) -> None:
    for _ in range(TRIAL_REQUESTS_PER_WINDOW):
        client.get("/limited")
    response = client.get("/limited")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_a_forwarded_for_header_does_not_change_the_count(client: TestClient) -> None:
    """The whole limit is worthless if a header can reset it.

    ``X-Forwarded-For`` is caller-controlled unless a trusted proxy overwrites
    it. Counting by it would let an attacker send a different value on every
    request and never be limited at all — which is precisely the abuse this
    exists to stop.
    """
    for n in range(TRIAL_REQUESTS_PER_WINDOW):
        client.get("/limited", headers={"X-Forwarded-For": f"10.0.0.{n}"})

    # A fresh forged address must not buy a fresh allowance.
    blocked = client.get("/limited", headers={"X-Forwarded-For": "10.0.0.254"})
    assert blocked.status_code == 429


def test_reads_are_not_throttled_on_a_mixed_router(client: TestClient) -> None:
    """Reloading a conversation must not spend the allowance for asking."""
    for _ in range(TRIAL_REQUESTS_PER_WINDOW):
        assert client.post("/mixed").status_code == 200
    assert client.post("/mixed").status_code == 429
    assert client.get("/mixed").status_code == 200


def test_reads_do_not_count_towards_the_write_limit(client: TestClient) -> None:
    for _ in range(TRIAL_REQUESTS_PER_WINDOW * 2):
        client.get("/mixed")
    assert client.post("/mixed").status_code == 200


@pytest.mark.parametrize(
    ("path", "policy"), [("/trial", TRIAL_START_POLICY), ("/signup", SIGNUP_POLICY)]
)
def test_auth_endpoints_have_their_own_limits(
    client: TestClient, path: str, policy: RateLimitPolicy
) -> None:
    for _ in range(policy.limit):
        assert client.post(path).status_code == 200
    refused = client.post(path)
    assert refused.status_code == 429
    assert refused.json()["error"] == "RateLimitExceededError"
    # Its own namespace: the trial path is untouched.
    assert client.get("/limited").status_code == 200


def test_login_is_limited_per_account(client: TestClient) -> None:
    body = {"email": "victim@example.com", "password": "guess"}
    for _ in range(LOGIN_ACCOUNT_POLICY.limit):
        assert client.post("/login", json=body).status_code == 200
    assert client.post("/login", json=body).status_code == 429
    # A different account from the same address is still let through.
    other = {"email": "someone@example.com", "password": "guess"}
    assert client.post("/login", json=other).status_code == 200


def test_the_login_limit_reads_the_same_body_the_route_does(client: TestClient) -> None:
    """One body, parsed once, handed to both the dependency and the route."""
    response = client.post("/login", json={"email": "a@example.com", "password": "p"})
    assert response.json() == {"email": "a@example.com"}


def _settings_with(monkeypatch: pytest.MonkeyPatch, backend: RateLimitBackend) -> None:
    class _Settings:
        rate_limit_backend = backend
        redis_url = "redis://localhost:6379/0"

    monkeypatch.setattr(deps, "get_settings", _Settings)
    deps.get_rate_limit_store.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_rate_limit_store() -> Iterator[None]:
    """Never let one test's configured store leak into another's."""
    deps.get_rate_limit_store.cache_clear()
    yield
    deps.get_rate_limit_store.cache_clear()


@pytest.mark.parametrize("backend", list(RateLimitBackend))
def test_the_rate_limit_store_is_shared_between_requests(
    monkeypatch: pytest.MonkeyPatch, backend: RateLimitBackend
) -> None:
    """The store outlives a single request.

    A new one per request would count to one every time and enforce nothing.
    """
    _settings_with(monkeypatch, backend)
    assert deps.get_rate_limit_store() is deps.get_rate_limit_store()


def test_the_redis_backend_builds_a_redis_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built without connecting: an unreachable Redis must not fail startup."""
    _settings_with(monkeypatch, RateLimitBackend.REDIS)
    assert isinstance(deps.get_rate_limit_store(), RedisRateLimitStore)


def test_the_memory_backend_builds_an_in_memory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings_with(monkeypatch, RateLimitBackend.MEMORY)
    assert isinstance(deps.get_rate_limit_store(), InMemoryRateLimitStore)


def test_the_object_store_is_built_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built from configuration, as a dependency.

    A module-level singleton would be built at import time and could not be
    substituted by a test without touching the disk.
    """
    from pathlib import Path

    class _Settings:
        image_storage_root = "./var/test-images"

    monkeypatch.setattr(deps, "get_settings", _Settings)
    store = deps.get_object_store()
    assert store is not None
    # Clean up the directory the constructor creates.
    root = Path("./var/test-images")
    if root.is_dir():
        root.rmdir()
