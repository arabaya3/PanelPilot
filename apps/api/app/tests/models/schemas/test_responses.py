"""Tests for `app/models/schemas/responses.py`.

Mirrors the module 1:1. These cover the models as a contract in their own
right — `test_structured_output.py` covers how the parse boundary uses them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.responses import (
    DiagnosisEnvelope,
    DiagnosisStep,
    Severity,
    StructuredDiagnosis,
)


def _step(**overrides: object) -> DiagnosisStep:
    """Build a valid step, with named fields replaced.

    Takes a mapping rather than keyword arguments so a test can vary a field
    chosen at parametrize time without the call losing its type.
    """
    payload: dict[str, object] = {
        "order": 1,
        "instruction": "Isolate the drive.",
        "rationale": "A live DC link is fatal.",
        "citation_ids": ["p1"],
    }
    payload.update(overrides)
    return DiagnosisStep.model_validate(payload)


# --- DiagnosisStep ----------------------------------------------------------


def test_a_step_defaults_to_the_least_alarming_severity() -> None:
    """An unspecified severity must not silently read as critical."""
    assert _step().severity is Severity.INFO


def test_a_step_cannot_be_uncited() -> None:
    """The whole point of cite-or-refuse, expressed in the type."""
    with pytest.raises(ValidationError):
        _step(citation_ids=[])


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n  "])
def test_a_step_cannot_carry_a_blank_citation_id(blank: str) -> None:
    """`min_length=1` on the list catches an empty list, not an empty string."""
    with pytest.raises(ValidationError, match="blank citation id"):
        _step(citation_ids=[blank])


def test_a_step_rejects_a_blank_id_alongside_a_real_one() -> None:
    """The check is over every id, not just the first."""
    with pytest.raises(ValidationError, match="blank citation id"):
        _step(citation_ids=["p1", "  "])


def test_step_order_is_one_indexed() -> None:
    with pytest.raises(ValidationError):
        _step(order=0)


@pytest.mark.parametrize("field", ["instruction", "rationale"])
def test_a_step_cannot_be_empty_text(field: str) -> None:
    with pytest.raises(ValidationError):
        _step(**{field: ""})


# --- StructuredDiagnosis ----------------------------------------------------


def _diagnosis(**overrides: object) -> StructuredDiagnosis:
    payload: dict[str, object] = {
        "summary": "Overcurrent on acceleration.",
        "summary_citation_ids": ["p1"],
        "steps": [_step(), _step(order=2)],
    }
    payload.update(overrides)
    return StructuredDiagnosis.model_validate(payload)


def test_a_diagnosis_needs_at_least_one_step() -> None:
    """A diagnosis with no action is not a diagnosis — refuse instead."""
    with pytest.raises(ValidationError):
        _diagnosis(steps=[])


def test_a_diagnosis_summary_must_be_cited() -> None:
    with pytest.raises(ValidationError):
        _diagnosis(summary_citation_ids=[])


@pytest.mark.parametrize(
    "orders",
    [
        [1, 3],  # gap — reads as a missing step
        [1, 1],  # repeat
        [2, 1],  # out of order
        [0, 1],  # zero-indexed
        [2, 3],  # does not start at 1
    ],
)
def test_step_numbering_must_be_a_clean_sequence(orders: list[int]) -> None:
    """A procedure jumping 2 -> 4 reads as though a step was dropped.

    For an isolation procedure that is a safety problem, not a cosmetic one.
    """
    steps = [
        {"order": o, "instruction": "x", "rationale": "y", "citation_ids": ["p1"]} for o in orders
    ]
    with pytest.raises(ValidationError):
        _diagnosis(steps=steps)


def test_a_single_step_diagnosis_is_valid() -> None:
    """The sequence rule must not accidentally require two steps."""
    assert len(_diagnosis(steps=[_step()]).steps) == 1


def test_equipment_model_is_the_one_optional_field() -> None:
    """Optional because a general question legitimately names no unit."""
    assert _diagnosis().equipment_model is None


# --- DiagnosisEnvelope ------------------------------------------------------


def test_a_valid_answered_envelope_round_trips() -> None:
    envelope = DiagnosisEnvelope(answered=True, diagnosis=_diagnosis(), confidence=0.9)
    assert DiagnosisEnvelope.model_validate(envelope.model_dump()).answered


def test_a_valid_refusal_envelope_round_trips() -> None:
    envelope = DiagnosisEnvelope(
        answered=False, refusal_message="No verified source.", confidence=0.1
    )
    assert not DiagnosisEnvelope.model_validate(envelope.model_dump()).answered


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_refusal_message_cannot_be_blank(blank: str) -> None:
    """A blank message renders as an empty card — the failure mode to remove."""
    with pytest.raises(ValidationError, match="must explain itself"):
        DiagnosisEnvelope(answered=False, refusal_message=blank, confidence=0.1)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_stays_in_the_unit_interval(confidence: float) -> None:
    """The frontend renders it as a percentage."""
    with pytest.raises(ValidationError):
        DiagnosisEnvelope(answered=False, refusal_message="no", confidence=confidence)


def test_citations_default_to_empty_rather_than_none() -> None:
    """So the frontend can iterate unconditionally."""
    envelope = DiagnosisEnvelope(answered=False, refusal_message="no", confidence=0.1)
    assert envelope.citations == []
