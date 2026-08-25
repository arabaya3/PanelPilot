"""Tests for `app/ai/guardrails/confidence.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Signature-pinning only while the functions are stubs; see the note in
test_cite_or_refuse.py.
"""

from __future__ import annotations

import pytest

from app.ai.guardrails import confidence
from app.models.schemas.diagnostics import ConfidenceBreakdown, VerifiedAnswer
from app.models.schemas.search import Citation, RetrievedPassage


def _breakdown() -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        overall=0.71,
        retrieval_score=0.82,
        passage_agreement=0.65,
        citation_density=0.66,
    )


def _answer() -> VerifiedAnswer:
    return VerifiedAnswer(
        text="Apply a 0.87 ambient correction factor.",
        citations=[
            Citation(
                document_id="doc-1",
                document_title="Electrical Installation Guide 2024",
                manufacturer="Schneider Electric",
            )
        ],
    )


def test_score_confidence_is_not_implemented() -> None:
    passage = RetrievedPassage(
        id="doc-1#3",
        text="Ambient correction factors.",
        score=0.82,
        citation=_answer().citations[0],
    )
    with pytest.raises(NotImplementedError):
        confidence.score_confidence(_answer(), evidence=[passage])


def test_is_publishable_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        confidence.is_publishable(_breakdown(), threshold=0.6)


def test_confidence_breakdown_keeps_its_components() -> None:
    """A real assertion: the score must stay explainable to the engineer.

    A breakdown that collapsed to a single number would make a low-confidence
    answer impossible to argue with, which is the whole point of reporting it.
    """
    breakdown = _breakdown()
    assert breakdown.retrieval_score > 0
    assert breakdown.passage_agreement > 0
    assert breakdown.citation_density > 0
