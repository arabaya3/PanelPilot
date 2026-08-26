"""Tests for `app/models/schemas/guardrail.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The decision type is the last line of defence: even if the gate logic were
wrong, the type refuses to represent a verdict nobody could explain to the
engineer who received it. How the gate produces decisions is tested in
`tests/ai/guardrails/test_cite_or_refuse.py`.
"""

from __future__ import annotations

import pytest

from app.models.schemas.guardrail import (
    ConfidenceDecision,
    DecisionOutcome,
    RefusalReason,
)
from app.models.schemas.search import Citation

_CITATION = Citation(
    document_id="doc-1",
    document_title="ACS880 firmware manual",
    manufacturer="ABB",
    page=88,
    section="3 Fault tracing",
)


def test_only_answer_permits_generation() -> None:
    """Every other outcome short-circuits without invoking the model."""
    answer = ConfidenceDecision(
        outcome=DecisionOutcome.ANSWER, score=0.9, threshold=0.6, citations=[_CITATION]
    )
    assert answer.may_generate

    for outcome in (DecisionOutcome.UNCERTAIN, DecisionOutcome.NO_VERIFIED_SOURCE):
        refused = ConfidenceDecision(
            outcome=outcome,
            score=0.2,
            threshold=0.6,
            reason=RefusalReason.BELOW_THRESHOLD,
            citations=[_CITATION] if outcome is DecisionOutcome.UNCERTAIN else [],
        )
        assert not refused.may_generate


def test_the_three_outcomes_are_the_whole_vocabulary() -> None:
    """Pinned: a fourth outcome would slip past every `is ANSWER` check."""
    assert {o.value for o in DecisionOutcome} == {
        "answer",
        "uncertain",
        "no_verified_source",
    }


def test_a_score_outside_the_unit_range_is_rejected() -> None:
    """The score is a probability-like measure; 1.4 would mean nothing."""
    with pytest.raises(ValueError, match="less than or equal to 1"):
        ConfidenceDecision(
            outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
            score=1.4,
            threshold=0.6,
            reason=RefusalReason.NO_EVIDENCE,
        )


def test_a_threshold_outside_the_unit_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        ConfidenceDecision(
            outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
            score=0.1,
            threshold=-0.2,
            reason=RefusalReason.NO_EVIDENCE,
        )


def test_the_refusal_vocabulary_covers_retrieval_and_calc_tools() -> None:
    """The same guardrail serves both paths, so every reason must exist.

    Pinned as an exact set: a new reason added without a template to render it
    surfaces to an engineer as a blank refusal.
    """
    assert {r.value for r in RefusalReason} == {
        "no_evidence",
        "below_threshold",
        "out_of_validated_range",
        # Retrieval returned a score that is not a real number in [0, 1].
        "unusable_score",
        # Evidence cleared the threshold but the generated response did not
        # match the schema. Distinct from below_threshold so escalation rows
        # do not read a generation fault as a retrieval fault.
        "unvalidatable_output",
    }


def test_an_uncertain_decision_may_name_its_closest_match() -> None:
    """Unlike NO_VERIFIED_SOURCE, which must name nothing."""
    decision = ConfidenceDecision(
        outcome=DecisionOutcome.UNCERTAIN,
        score=0.4,
        threshold=0.6,
        reason=RefusalReason.BELOW_THRESHOLD,
        citations=[_CITATION],
    )
    assert decision.citations == [_CITATION]
