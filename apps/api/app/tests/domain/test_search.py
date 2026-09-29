"""Tests for `app/domain/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.retrieval import hybrid_search
from app.core.errors import NotImplementedYetError
from app.domain.search import calibrate_relevance, search_documents
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.evaluation import EvalCategory, EvalEntry
from app.models.schemas.retrieval_config import RetrievalConfig
from app.models.schemas.search import Citation, RetrievedPassage, SearchRequest


def test_search_says_it_is_not_available_yet() -> None:
    """A 501 with a reason, not the anonymous 500 of a bare NotImplementedError."""
    user = CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )
    with pytest.raises(NotImplementedYetError, match="not available yet"):
        search_documents(user=user, request=SearchRequest(query="F0001"))


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
