"""Tests for `app/api/v1/routes/ingestion.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import ingestion as ingestion_route
from app.core.errors import install_exception_handlers
from app.domain import ingestion as ingestion_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.ingestion import CrawlJobResponse, CrawlJobStatus

_BODY = {"source_id": "abb", "seed_urls": ["https://library.abb.com/x"]}


class _Session:
    """Records whether the route committed before answering."""

    committed = False

    def commit(self) -> None:
        _Session.committed = True


def _ingester() -> CurrentUser:
    return CurrentUser(
        id="u", email="i@example.com", tenant_id="t", roles=frozenset({Role.INGESTION})
    )


@pytest.fixture(name="client")
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.api import deps
    from app.core.db import get_session

    _Session.committed = False
    app = FastAPI()
    app.include_router(ingestion_route.router, prefix="/ingestion")
    app.dependency_overrides[deps.get_current_user] = _ingester
    app.dependency_overrides[get_session] = _Session
    install_exception_handlers(app)
    with TestClient(app) as client:
        yield client


def test_a_crawl_request_is_accepted_not_performed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """202: queued for the worker. The request thread does no crawling."""

    def fake_queue(**_kw: Any) -> CrawlJobResponse:
        return CrawlJobResponse(id="job-1", status=CrawlJobStatus.QUEUED)

    monkeypatch.setattr(ingestion_domain, "create_crawl_job", fake_queue)

    response = client.post("/ingestion/crawl-jobs", json=_BODY)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    # Committed before the answer: a worker polling the queue must find it.
    assert _Session.committed


def test_a_job_can_be_polled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(*, session: Any, user: Any, job_id: str) -> CrawlJobResponse:
        seen["job_id"] = job_id
        return CrawlJobResponse(id=job_id, status=CrawlJobStatus.FAILED, error="boom")

    monkeypatch.setattr(ingestion_domain, "get_crawl_job", fake_get)

    response = client.get("/ingestion/crawl-jobs/job-9")

    assert response.status_code == 200
    assert response.json() == {"id": "job-9", "status": "failed", "error": "boom"}
    assert seen["job_id"] == "job-9"
