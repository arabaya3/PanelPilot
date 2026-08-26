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
from app.core.errors import install_exception_handlers
from app.domain.rate_limit import TRIAL_REQUESTS_PER_WINDOW, InMemoryRateLimitStore


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

    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_a_normal_request_passes(client: TestClient) -> None:
    assert client.get("/limited").status_code == 200


def test_a_burst_is_throttled(client: TestClient) -> None:
    for _ in range(TRIAL_REQUESTS_PER_WINDOW):
        client.get("/limited")
    assert client.get("/limited").status_code == 422


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
    assert blocked.status_code == 422


def test_the_rate_limit_store_is_shared_between_requests() -> None:
    """The store outlives a single request.

    A new one per request would count to one every time and enforce nothing.
    """
    assert deps.get_rate_limit_store() is deps.get_rate_limit_store()


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
