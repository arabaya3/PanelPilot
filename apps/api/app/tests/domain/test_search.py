"""Tests for `app/domain/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.retrieval import hybrid_search
from app.core.errors import AuthorizationError, ServiceUnavailableError, ValidationError
from app.domain.search import calibrate_relevance, search_documents
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.evaluation import EvalCategory, EvalEntry
from app.models.schemas.retrieval_config import RetrievalConfig
from app.models.schemas.search import (
    Citation,
    RetrievedPassage,
    SearchFilters,
    SearchRequest,
)

ENGINEER = CurrentUser(
    id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
)
REVIEWER = CurrentUser(
    id="r", email="r@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER, Role.REVIEWER})
)
PASSAGE = RetrievedPassage(
    id="c1",
    text="F0001 overcurrent: check the motor cable.",
    score=0.9,
    citation=Citation(document_id="https://x/a.pdf", document_title="Faults", manufacturer="ABB"),
)


def _recorder(name: str, calls: list[tuple[str, dict[str, Any]]]) -> Any:
    def run(query: str, **kwargs: Any) -> list[RetrievedPassage]:
        calls.append((name, {"query": query, **kwargs}))
        return [PASSAGE]

    return run


def test_an_engineer_searches_production_with_their_filters() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    request = SearchRequest(
        query="  F0001 overcurrent  ",
        filters=SearchFilters(manufacturers=["ABB"]),
        top_k=5,
    )

    response = search_documents(
        user=ENGINEER,
        request=request,
        search=_recorder("production", calls),
        search_staging=_recorder("staging", calls),
    )

    assert (response.total, response.passages) == (1, [PASSAGE])
    assert calls == [
        ("production", {"query": "F0001 overcurrent", "filters": request.filters, "top_k": 5})
    ]


def test_only_a_reviewer_reaches_staging() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    staging = SearchRequest(query="F0001", corpus="staging")
    production, staged = _recorder("production", calls), _recorder("staging", calls)

    with pytest.raises(AuthorizationError):
        search_documents(user=ENGINEER, request=staging, search=production, search_staging=staged)
    assert calls == []

    search_documents(user=REVIEWER, request=staging, search=production, search_staging=staged)
    assert [name for name, _ in calls] == ["staging"]


def test_a_reviewer_searches_production_unless_they_ask_for_staging() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    search_documents(
        user=REVIEWER,
        request=SearchRequest(query="F0001"),
        search=_recorder("production", calls),
        search_staging=_recorder("staging", calls),
    )
    assert [name for name, _ in calls] == ["production"]


def test_a_blank_query_is_refused_before_anything_is_embedded() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    with pytest.raises(ValidationError, match="needs a query"):
        search_documents(
            user=ENGINEER,
            request=SearchRequest(query="   "),
            search=_recorder("production", calls),
        )
    assert calls == []


def test_a_retrieval_failure_is_a_503_that_names_no_host() -> None:
    def down(_query: str, **_kwargs: Any) -> list[RetrievedPassage]:
        raise ConnectionError("opensearch.internal:9200 refused the connection")

    with pytest.raises(ServiceUnavailableError) as raised:
        search_documents(user=ENGINEER, request=SearchRequest(query="F0001"), search=down)
    assert "opensearch.internal" not in str(raised.value)


def test_by_default_each_corpus_uses_its_own_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production search for production, and the staging one only for staging."""
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(hybrid_search, "search", _recorder("production", calls))
    monkeypatch.setattr(hybrid_search, "search_staging", _recorder("staging", calls))

    search_documents(user=REVIEWER, request=SearchRequest(query="a"))
    search_documents(user=REVIEWER, request=SearchRequest(query="b", corpus="staging"))

    assert [(name, kw["query"]) for name, kw in calls] == [("production", "a"), ("staging", "b")]


def test_calibration_measures_production_with_no_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured floor would hide the passages whose similarity it measures."""
    calls: list[dict[str, Any]] = []

    def fake_search(query: str, brand: Any, model: Any, *, config: Any) -> list[RetrievedPassage]:
        calls.append({"query": query, "brand": brand, "model": model, "config": config})
        return [
            RetrievedPassage(
                id="p",
                text="t",
                score=1.0,
                similarity=0.3,
                citation=Citation(document_id="d", document_title="D", manufacturer="ABB"),
            )
        ]

    monkeypatch.setattr(hybrid_search, "search", fake_search)
    monkeypatch.setattr(
        hybrid_search,
        "retrieval_config_from_settings",
        lambda: RetrievalConfig(top_k=7, min_similarity=0.8),
    )
    entry = EvalEntry(
        id="oos",
        query="torque spec for a Corolla head bolt",
        category=EvalCategory.OUT_OF_SCOPE,
        expected_answer_summary="Not in the corpus.",
        brand="ABB",
        model="ACS880",
    )

    result = calibrate_relevance([entry])

    assert result.out_of_scope == 1
    assert len(calls) == 1
    assert (calls[0]["query"], calls[0]["brand"], calls[0]["model"]) == (
        entry.query,
        "ABB",
        "ACS880",
    )
    assert calls[0]["config"].min_similarity is None
    assert calls[0]["config"].top_k == 7  # everything else as configured
