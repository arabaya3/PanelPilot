"""Tests for `app/ai/guardrails/refusal_text.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.ai.guardrails.refusal_text import _TEMPLATES, render_refusal
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
)


def _refusal(
    reason: RefusalReason,
    *,
    outcome: DecisionOutcome = DecisionOutcome.NO_VERIFIED_SOURCE,
    detail: str | None = None,
) -> ConfidenceDecision:
    citations = [] if outcome is DecisionOutcome.NO_VERIFIED_SOURCE else [_CITATION]
    return ConfidenceDecision(
        outcome=outcome,
        score=0.2,
        threshold=0.6,
        reason=reason,
        citations=citations,
        detail=detail,
    )


def test_every_refusal_reason_has_text() -> None:
    """Pinned as exhaustive.

    A reason added without a template would raise ``KeyError`` inside the
    response path, or — worse, if it were defaulted — surface to an engineer
    as a blank card.
    """
    assert set(_TEMPLATES) == set(RefusalReason)


@pytest.mark.parametrize("reason", list(RefusalReason))
def test_every_reason_renders_something_an_engineer_can_act_on(
    reason: RefusalReason,
) -> None:
    text = render_refusal(_refusal(reason))
    assert text.strip()
    assert len(text) > 40, "a refusal that says nothing actionable is a blank card"


def test_the_texts_are_distinct() -> None:
    """Otherwise the reason code carries information the engineer never sees."""
    assert len(set(_TEMPLATES.values())) == len(_TEMPLATES)


def test_the_validated_range_refusal_names_the_specifics() -> None:
    """Name the quantity and its bounds, not just that it was out of range.

    "Out of range" alone tells an engineer nothing they can change.
    """
    text = render_refusal(
        _refusal(
            RefusalReason.OUT_OF_VALIDATED_RANGE,
            detail="Cable length 900 m exceeds the validated maximum of 500 m.",
        )
    )
    assert "900 m" in text


def test_a_validation_failure_never_leaks_the_model_output() -> None:
    """``detail`` on that path quotes the unvalidated response being refused.

    Showing it would put exactly the content the guardrail rejected in front
    of the engineer, with the appearance of having been vetted.
    """
    leaked = "steps.0.instruction: Bypass the interlock and energise the panel"
    text = render_refusal(
        _refusal(
            RefusalReason.UNVALIDATABLE_OUTPUT,
            outcome=DecisionOutcome.UNCERTAIN,
            detail=f"structured output could not be validated: {leaked}",
        )
    )
    assert "Bypass the interlock" not in text
    assert "instruction" not in text


def test_rendering_a_permitting_decision_is_refused() -> None:
    """It would show a refusal to an engineer who was entitled to an answer."""
    answering = ConfidenceDecision(
        outcome=DecisionOutcome.ANSWER,
        score=0.9,
        threshold=0.6,
        citations=[_CITATION],
    )
    with pytest.raises(ValueError, match="permits generation"):
        render_refusal(answering)


def test_the_below_threshold_text_points_at_the_closest_match() -> None:
    """The uncertain path cites its nearest passage, so the text must say so."""
    text = render_refusal(
        _refusal(RefusalReason.BELOW_THRESHOLD, outcome=DecisionOutcome.UNCERTAIN)
    )
    assert "cited" in text or "nearest" in text


def test_no_template_speculates_or_offers_a_partial_answer() -> None:
    """The refusal must not become a hedged answer.

    A refusal that says "it is probably the contactor" is an unsourced answer
    wearing a disclaimer, which is precisely what cite-or-refuse forbids.
    """
    hedges = ("probably", "likely", "might be", "usually the", "try replacing")
    for reason, text in _TEMPLATES.items():
        lowered = text.lower()
        for hedge in hedges:
            assert hedge not in lowered, f"{reason.value} speculates: {hedge!r}"
