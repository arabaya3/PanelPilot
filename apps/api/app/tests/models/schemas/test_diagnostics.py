"""Tests for `app/models/schemas/diagnostics.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.diagnostics import (
    ConfidenceBreakdown,
    DiagnosticResponse,
    VerifiedAnswer,
)
from app.models.schemas.responses import DiagnosisStep, Severity, StructuredDiagnosis

# --- the response either diagnoses or refuses, never both -------------------


def _confidence() -> ConfidenceBreakdown:
    return ConfidenceBreakdown(
        overall=0.9, retrieval_score=0.9, passage_agreement=0.8, citation_density=0.7
    )


def _verified() -> VerifiedAnswer:
    return VerifiedAnswer(text="The drive tripped on overcurrent.", citations=[])


def _structured() -> StructuredDiagnosis:
    return StructuredDiagnosis(
        summary="Overcurrent on acceleration.",
        summary_citation_ids=["p1"],
        steps=[
            DiagnosisStep(
                order=1,
                instruction="Isolate the drive.",
                rationale="A live DC link is fatal.",
                citation_ids=["p1"],
                severity=Severity.CRITICAL,
            )
        ],
        severity=Severity.CRITICAL,
    )


def test_the_rendered_type_carries_the_structured_diagnosis() -> None:
    """The constraint is derived from this same model, so they cannot drift.

    If this embedding is ever replaced by a hand-written copy, the schema the
    model is constrained to and the shape the frontend renders become two
    things somebody has to keep in sync.
    """
    response = DiagnosticResponse(
        session_id="s1",
        answer=_verified(),
        diagnosis=_structured(),
        confidence=_confidence(),
        low_confidence=False,
    )
    assert response.diagnosis is not None
    assert response.diagnosis.steps[0].severity is Severity.CRITICAL


def test_the_openapi_schema_exposes_the_structured_shape() -> None:
    """What the frontend generates its types from must include the diagnosis.

    Asserted on the emitted schema rather than the Python model, because the
    generated TypeScript is produced from the schema.
    """
    schema = DiagnosticResponse.model_json_schema()
    assert "diagnosis" in schema["properties"]
    assert "StructuredDiagnosis" in schema["$defs"]
    assert set(schema["$defs"]["StructuredDiagnosis"]["properties"]) == set(
        StructuredDiagnosis.model_fields
    )


def test_a_response_must_diagnose_or_refuse() -> None:
    """Neither would leave the frontend rendering an empty card."""
    with pytest.raises(ValidationError, match="must carry a refusal message"):
        DiagnosticResponse(
            session_id="s1",
            answer=_verified(),
            confidence=_confidence(),
            low_confidence=True,
        )


def test_a_response_cannot_do_both() -> None:
    """Otherwise the frontend has to decide which half to believe."""
    with pytest.raises(ValidationError, match="cannot both diagnose and refuse"):
        DiagnosticResponse(
            session_id="s1",
            answer=_verified(),
            diagnosis=_structured(),
            refusal_message="No verified source.",
            confidence=_confidence(),
            low_confidence=False,
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_refusal_message_does_not_count_as_refusing(blank: str) -> None:
    """A blank message renders as an empty card, same as no message at all."""
    with pytest.raises(ValidationError, match="must carry a refusal message"):
        DiagnosticResponse(
            session_id="s1",
            answer=_verified(),
            refusal_message=blank,
            confidence=_confidence(),
            low_confidence=True,
        )


def test_a_refusal_response_is_valid() -> None:
    """A refusal carries neither form of the answer, only the message."""
    response = DiagnosticResponse(
        session_id="s1",
        refusal_message="No verified source covers that fault code.",
        confidence=_confidence(),
        low_confidence=True,
    )
    assert response.diagnosis is None
    assert response.answer is None


def test_a_refusal_cannot_smuggle_prose_past_the_missing_diagnosis() -> None:
    """Otherwise the frontend renders an unvalidated answer under a refusal."""
    with pytest.raises(ValidationError, match="must not carry an answer"):
        DiagnosticResponse(
            session_id="s1",
            answer=_verified(),
            refusal_message="No verified source covers that fault code.",
            confidence=_confidence(),
            low_confidence=True,
        )


def test_a_diagnosis_must_bring_its_prose_answer() -> None:
    """Both forms travel together; one without the other is half a response."""
    with pytest.raises(ValidationError, match="must carry its prose answer"):
        DiagnosticResponse(
            session_id="s1",
            diagnosis=_structured(),
            confidence=_confidence(),
            low_confidence=False,
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_verified_answer_cannot_be_blank_prose(blank: str) -> None:
    """Same reason the structured summary cannot: it renders as an empty card."""
    with pytest.raises(ValidationError):
        VerifiedAnswer(text=blank, citations=[])
