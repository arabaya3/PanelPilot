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


def _passage(doc_id: str = "doc-1", score: float = 0.82) -> RetrievedPassage:
    return RetrievedPassage(
        id=f"{doc_id}#3",
        text="Ambient correction factors.",
        score=score,
        citation=Citation(
            document_id=doc_id,
            document_title="Electrical Installation Guide 2024",
            manufacturer="Schneider Electric",
        ),
    )


# --- the score never comes from the model's own opinion --------------------


def test_confidence_is_derived_from_retrieval_not_the_model() -> None:
    """A model's self-reported confidence is not calibrated.

    It is highest exactly when the model is confidently wrong, so every signal
    here is measured from the evidence rather than asked for.
    """
    strong = confidence.score_confidence(_answer(), evidence=[_passage(score=0.95)])
    weak = confidence.score_confidence(_answer(), evidence=[_passage(score=0.2)])
    assert strong.overall > weak.overall
    assert strong.retrieval_score == 0.95


def test_an_answer_resting_on_more_of_the_evidence_scores_higher() -> None:
    """Passage agreement: how much of what was retrieved the answer used."""
    answer = _answer()
    all_cited = confidence.score_confidence(answer, evidence=[_passage()])
    one_of_five = confidence.score_confidence(
        answer,
        evidence=[_passage(), *(_passage(f"other-{n}") for n in range(4))],
    )
    assert all_cited.passage_agreement == 1.0
    assert one_of_five.passage_agreement == pytest.approx(0.2)
    assert all_cited.overall > one_of_five.overall


def test_more_citations_raise_density_up_to_a_ceiling() -> None:
    """Four agreeing sources is well-supported; forty is not ten times better.

    Rewarding an unbounded count would reward padding the citation list.
    """

    def answer_with(n: int) -> VerifiedAnswer:
        return VerifiedAnswer(
            text="An answer.",
            citations=[
                Citation(
                    document_id=f"doc-{i}",
                    document_title="Guide",
                    manufacturer="Schneider Electric",
                )
                for i in range(n)
            ],
        )

    evidence = [_passage(f"doc-{i}") for i in range(40)]
    one = confidence.score_confidence(answer_with(1), evidence=evidence)
    four = confidence.score_confidence(answer_with(4), evidence=evidence)
    forty = confidence.score_confidence(answer_with(40), evidence=evidence)
    assert one.citation_density < four.citation_density
    assert four.citation_density == 1.0
    assert forty.citation_density == 1.0


def test_the_overall_score_stays_in_the_unit_interval() -> None:
    """It is rendered as a percentage and compared against a threshold."""
    breakdown = confidence.score_confidence(_answer(), evidence=[_passage(score=1.0)])
    assert 0.0 <= breakdown.overall <= 1.0


def test_no_evidence_scores_zero_rather_than_raising() -> None:
    """Reachable only if a caller skipped the guardrail, which is forbidden.

    Scoring zero keeps that bug from becoming a 500, and zero refuses
    downstream — the safe direction.
    """
    breakdown = confidence.score_confidence(_answer(), evidence=[])
    assert breakdown.overall == 0.0


def test_every_component_is_reported() -> None:
    """A low score has to be explainable to the engineer who received it."""
    breakdown = confidence.score_confidence(_answer(), evidence=[_passage()])
    assert breakdown.retrieval_score > 0
    assert breakdown.passage_agreement > 0
    assert breakdown.citation_density > 0


# --- publishability --------------------------------------------------------


def test_a_score_at_the_threshold_is_publishable() -> None:
    assert confidence.is_publishable(_breakdown(), threshold=0.71)


def test_a_score_below_the_threshold_is_not() -> None:
    assert not confidence.is_publishable(_breakdown(), threshold=0.72)


def test_publishability_uses_the_hardened_resolver() -> None:
    """Not `get_settings()` directly.

    Two readers of one setting could disagree about the live threshold, and
    the resolver is the one that fails closed on a nonsensical value.
    """
    import inspect

    source = inspect.getsource(confidence.is_publishable)
    assert "_resolve_threshold" in source
    assert "get_settings" not in source


def test_confidence_breakdown_keeps_its_components() -> None:
    """A real assertion: the score must stay explainable to the engineer.

    A breakdown that collapsed to a single number would make a low-confidence
    answer impossible to argue with, which is the whole point of reporting it.
    """
    breakdown = _breakdown()
    assert breakdown.retrieval_score > 0
    assert breakdown.passage_agreement > 0
    assert breakdown.citation_density > 0
