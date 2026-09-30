"""Tests for `app/api/v1/routes/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.retrieval import hybrid_search
from app.api import deps
from app.api.v1.routes import search as search_route
from app.core.errors import install_exception_handlers
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.search import Citation, RetrievedPassage


def _user() -> CurrentUser:
    return CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(search_route.router, prefix="/search")
    app.dependency_overrides[deps.get_current_user] = _user
    # The trial rate limit is its own tested dependency; not the subject here.
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None
    install_exception_handlers(app)
    with TestClient(app) as test_client:
        yield test_client


def test_search_returns_ranked_passages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    passage = RetrievedPassage(
        id="c1",
        text="F0001 overcurrent.",
        score=0.9,
        citation=Citation(
            document_id="https://x/a.pdf", document_title="Faults", manufacturer="ABB"
        ),
    )
    monkeypatch.setattr(hybrid_search, "search", lambda *_a, **_k: [passage])

    response = client.post("/search", json={"query": "F0001 overcurrent"})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["passages"][0]["citation"]["document_id"] == "https://x/a.pdf"


def test_an_engineer_asking_for_staging_is_a_403(client: TestClient) -> None:
    response = client.post("/search", json={"query": "F0001", "corpus": "staging"})
    assert response.status_code == 403


def test_an_unknown_corpus_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post("/search", json={"query": "F0001", "corpus": "everything"})
    assert response.status_code == 422


def test_a_blank_query_is_a_422(client: TestClient) -> None:
    assert client.post("/search", json={"query": "  "}).status_code == 422


def test_a_retrieval_outage_is_a_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_a: object, **_k: object) -> list[RetrievedPassage]:
        raise ConnectionError("refused")

    monkeypatch.setattr(hybrid_search, "search", down)
    response = client.post("/search", json={"query": "F0001"})
    assert response.status_code == 503
    assert response.json()["error"] == "ServiceUnavailableError"


def test_an_unbounded_top_k_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post("/search", json={"query": "q", "top_k": 10_000})
    assert response.status_code == 422
