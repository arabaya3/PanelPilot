"""Tests for `app/models/schemas/responses.py`.

Mirrors the module 1:1. These cover the models as a contract in their own
right — `test_structured_output.py` covers how the parse boundary uses them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.responses import (
    DiagnosisStep,
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
        "severity": "info",
    }
    payload.update(overrides)
    return DiagnosisStep.model_validate(payload)


# --- DiagnosisStep ----------------------------------------------------------


def test_a_step_must_state_its_severity() -> None:
    """Require an explicit severity rather than defaulting one.

    An omitted severity defaulting to INFO would render an arc-flash warning
    in the same colour as a note, and omission is exactly what a model does
    when it is unsure.
    """
    with pytest.raises(ValidationError):
        DiagnosisStep.model_validate(
            {"order": 1, "instruction": "x", "rationale": "y", "citation_ids": ["p1"]}
        )


def test_a_step_cannot_be_uncited() -> None:
    """The whole point of cite-or-refuse, expressed in the type."""
    with pytest.raises(ValidationError):
        _step(citation_ids=[])


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n  "])
def test_a_step_cannot_carry_a_blank_citation_id(blank: str) -> None:
    """`min_length=1` alone accepts "   "; the constraint strips first."""
    with pytest.raises(ValidationError):
        _step(citation_ids=[blank])


def test_a_step_rejects_a_blank_id_alongside_a_real_one() -> None:
    """The check is over every id, not just the first."""
    with pytest.raises(ValidationError):
        _step(citation_ids=["p1", "  "])


def test_a_step_cannot_cite_the_same_passage_twice() -> None:
    """One passage shown three times reads as three corroborating sources."""
    with pytest.raises(ValidationError, match="more than once"):
        _step(citation_ids=["p1", "p1"])


def test_citation_ids_are_stripped_rather_than_trusted_as_given() -> None:
    """So " p1" and "p1" cannot both be present as if they were two sources."""
    assert _step(citation_ids=[" p1 "]).citation_ids == ["p1"]


def test_step_order_is_one_indexed() -> None:
    with pytest.raises(ValidationError):
        _step(order=0)


@pytest.mark.parametrize("field", ["instruction", "rationale"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_step_cannot_be_blank_text(field: str, blank: str) -> None:
    """Reject whitespace-only prose, not merely empty prose.

    Whitespace-only prose renders as a blank card, which is the failure
    mode this contract exists to remove — so it must not merely be non-empty.
    """
    with pytest.raises(ValidationError):
        _step(**{field: blank})


@pytest.mark.parametrize("field", ["instruction", "rationale"])
def test_step_prose_is_bounded(field: str) -> None:
    """A runaway generation must not push megabytes into a browser."""
    with pytest.raises(ValidationError):
        _step(**{field: "x" * 4001})


# --- StructuredDiagnosis ----------------------------------------------------


def _diagnosis(**overrides: object) -> StructuredDiagnosis:
    payload: dict[str, object] = {
        "summary": "Overcurrent on acceleration.",
        "summary_citation_ids": ["p1"],
        "severity": "info",
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


def test_a_summary_cannot_cite_the_same_passage_twice() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        _diagnosis(summary_citation_ids=["p1", "p1"])


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_diagnosis_summary_cannot_be_blank(blank: str) -> None:
    with pytest.raises(ValidationError):
        _diagnosis(summary=blank)


def test_a_diagnosis_must_state_its_severity() -> None:
    with pytest.raises(ValidationError):
        StructuredDiagnosis.model_validate(
            {"summary": "x", "summary_citation_ids": ["p1"], "steps": [_step()]}
        )


def test_the_step_count_is_bounded() -> None:
    """A 10,000-step procedure is a runaway generation, not a diagnosis."""
    steps = [
        {
            "order": n + 1,
            "instruction": "x",
            "rationale": "y",
            "citation_ids": ["p1"],
            "severity": "info",
        }
        for n in range(51)
    ]
    with pytest.raises(ValidationError):
        _diagnosis(steps=steps)


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
        {
            "order": o,
            "instruction": "x",
            "rationale": "y",
            "citation_ids": ["p1"],
            "severity": "info",
        }
        for o in orders
    ]
    with pytest.raises(ValidationError):
        _diagnosis(steps=steps)


def test_a_single_step_diagnosis_is_valid() -> None:
    """The sequence rule must not accidentally require two steps."""
    assert len(_diagnosis(steps=[_step()]).steps) == 1


def test_equipment_model_is_the_one_optional_field() -> None:
    """Optional because a general question legitimately names no unit."""
    assert _diagnosis().equipment_model is None
