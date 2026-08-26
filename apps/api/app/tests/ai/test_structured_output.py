"""Tests for `app/ai/structured_output.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

AI-004's criterion is 100 varied queries with **zero** parsing failures. That
target is only achievable because malformed output falls back to a refusal, so
these tests check both halves: that valid output validates, and that every way
output can be malformed becomes a refusal rather than something the frontend
has to defend against.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.structured_output import (
    DIAGNOSIS_TOOL_NAME,
    diagnosis_tool_definition,
    parse_tool_output,
    structured_or_refuse,
)
from app.models.schemas.guardrail import (
    ConfidenceDecision,
    DecisionOutcome,
    RefusalReason,
)
from app.models.schemas.responses import (
    DiagnosisEnvelope,
    DiagnosisStep,
    Severity,
    StructuredDiagnosis,
)
from app.models.schemas.search import Citation

_CITATION = Citation(
    document_id="doc-1",
    document_title="ACS880 firmware manual",
    manufacturer="ABB",
    page=88,
    section="3 Fault tracing",
)

EVIDENCE_IDS = {"p1", "p2", "p3"}


def _permitting_decision() -> ConfidenceDecision:
    return ConfidenceDecision(
        outcome=DecisionOutcome.ANSWER,
        score=0.87,
        threshold=0.6,
        citations=[_CITATION],
    )


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": "The drive tripped on overcurrent during acceleration.",
        "summary_citation_ids": ["p1"],
        "steps": [
            {
                "order": 1,
                "instruction": "Isolate the drive and verify zero voltage.",
                "rationale": "Work on a live DC link is fatal.",
                "citation_ids": ["p1"],
                "severity": "critical",
            },
            {
                "order": 2,
                "instruction": "Megger the motor cable.",
                "rationale": "An insulation fault presents as overcurrent.",
                "citation_ids": ["p2"],
                "severity": "warning",
            },
        ],
        "severity": "critical",
        "equipment_model": "ACS880",
    }
    payload.update(overrides)
    return payload


# --- the constraint is generated, not hand-written --------------------------


def test_the_tool_schema_is_derived_from_the_response_model() -> None:
    """One definition. A hand-written constraint would drift from the type."""
    definition = diagnosis_tool_definition()
    assert definition["name"] == DIAGNOSIS_TOOL_NAME
    assert definition["input_schema"] == StructuredDiagnosis.model_json_schema()


def test_the_schema_marks_every_rendered_field_required() -> None:
    """An optional field is one the frontend must defensively handle."""
    schema = StructuredDiagnosis.model_json_schema()
    assert set(schema["required"]) >= {"summary", "summary_citation_ids", "steps"}


def test_the_schema_is_serialisable_for_the_api() -> None:
    """It travels to the model as JSON; an unserialisable schema fails at runtime."""
    json.dumps(diagnosis_tool_definition())


# --- the acceptance criterion: 100 varied queries, zero failures ------------


def _vary(index: int) -> dict[str, Any]:
    """Build one of 100 varied but valid payloads."""
    severities = ["critical", "warning", "info"]
    step_count = (index % 4) + 1
    return {
        "summary": f"Finding {index}: the unit reports fault F{index:04d}.",
        "summary_citation_ids": [sorted(EVIDENCE_IDS)[index % 3]],
        "steps": [
            {
                "order": n + 1,
                "instruction": f"Step {n + 1} for finding {index}.",
                "rationale": f"Because condition {n} applies.",
                "citation_ids": [sorted(EVIDENCE_IDS)[(index + n) % 3]],
                "severity": severities[(index + n) % 3],
            }
            for n in range(step_count)
        ],
        "severity": severities[index % 3],
        "equipment_model": None if index % 5 == 0 else f"MODEL-{index}",
    }


@pytest.mark.parametrize("index", range(100))
def test_a_hundred_varied_payloads_all_validate(index: int) -> None:
    """The criterion, asserted at the parse boundary.

    Varies step count, severity, citation targets and the optional model field
    — the axes a real response actually varies on.
    """
    diagnosis = parse_tool_output(_vary(index), evidence_ids=EVIDENCE_IDS)
    assert diagnosis.steps
    assert diagnosis.summary


def test_the_hundred_payloads_are_actually_varied() -> None:
    """Guard against the fixture degenerating into one payload repeated.

    A hundred identical cases would satisfy the count while testing one thing.
    """
    payloads = [_vary(i) for i in range(100)]
    assert len({p["severity"] for p in payloads}) == 3
    assert len({len(p["steps"]) for p in payloads}) == 4
    assert len({json.dumps(p, sort_keys=True) for p in payloads}) == 100


# --- every malformation becomes a refusal, never broken output --------------


@pytest.mark.parametrize(
    ("description", "payload"),
    [
        ("no summary", _valid_payload(summary="")),
        ("no steps", _valid_payload(steps=[])),
        ("uncited summary", _valid_payload(summary_citation_ids=[])),
        ("wrong type for steps", _valid_payload(steps="two steps")),
        ("missing required key", {"summary": "x"}),
        ("unknown severity", _valid_payload(severity="catastrophic")),
        (
            "step numbering gap",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "First.",
                        "rationale": "Because.",
                        "citation_ids": ["p1"],
                    },
                    {
                        "order": 3,
                        "instruction": "Third.",
                        "rationale": "Because.",
                        "citation_ids": ["p1"],
                    },
                ]
            ),
        ),
        (
            "uncited step",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "Do it.",
                        "rationale": "Because.",
                        "citation_ids": [],
                    }
                ]
            ),
        ),
        (
            "blank citation id",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "Do it.",
                        "rationale": "Because.",
                        "citation_ids": ["   "],
                    }
                ]
            ),
        ),
    ],
)
def test_malformed_output_becomes_a_refusal(description: str, payload: dict[str, Any]) -> None:
    """Never repaired, never partially rendered, never shown broken."""
    diagnosis, decision = structured_or_refuse(
        payload, evidence_ids=EVIDENCE_IDS, decision=_permitting_decision()
    )
    assert diagnosis is None, f"{description} produced a diagnosis"
    assert not decision.may_generate
    assert decision.detail is not None
    assert "could not be validated" in decision.detail


def test_no_tool_call_at_all_becomes_a_refusal() -> None:
    """The model may decline to call the tool; that is not an answer."""
    diagnosis, decision = structured_or_refuse(
        None, evidence_ids=EVIDENCE_IDS, decision=_permitting_decision()
    )
    assert diagnosis is None
    assert not decision.may_generate
    assert decision.detail is not None
    assert "no structured output" in decision.detail


def test_a_well_formed_but_invented_citation_is_refused() -> None:
    """Constrained generation guarantees the shape, not the truth of the ids.

    A syntactically perfect citation of a passage the model was never shown is
    a fabricated source, and it looks identical to a real one.
    """
    with pytest.raises(ValueError, match="never supplied"):
        parse_tool_output(
            _valid_payload(summary_citation_ids=["p9-invented"]), evidence_ids=EVIDENCE_IDS
        )


def test_the_refusal_preserves_the_original_confidence() -> None:
    """The turn must stay explainable: good evidence, unusable output."""
    original = _permitting_decision()
    _, decision = structured_or_refuse(
        {"broken": True}, evidence_ids=EVIDENCE_IDS, decision=original
    )
    assert decision.score == original.score
    assert decision.threshold == original.threshold
    assert decision.reason is RefusalReason.BELOW_THRESHOLD


def test_valid_output_passes_the_decision_through_unchanged() -> None:
    original = _permitting_decision()
    diagnosis, decision = structured_or_refuse(
        _valid_payload(), evidence_ids=EVIDENCE_IDS, decision=original
    )
    assert diagnosis is not None
    assert decision is original


# --- the envelope cannot express a state the frontend cannot render ---------


def test_an_answered_envelope_must_carry_a_diagnosis() -> None:
    with pytest.raises(ValidationError, match="carries no diagnosis"):
        DiagnosisEnvelope(answered=True, confidence=0.9)


def test_an_unanswered_envelope_must_explain_itself() -> None:
    with pytest.raises(ValidationError, match="must explain itself"):
        DiagnosisEnvelope(answered=False, confidence=0.2)


def test_an_envelope_cannot_be_both() -> None:
    """Otherwise the frontend has to decide which half to believe."""
    diagnosis = StructuredDiagnosis.model_validate(_valid_payload())
    with pytest.raises(ValidationError, match="must not carry a refusal message"):
        DiagnosisEnvelope(
            answered=True,
            diagnosis=diagnosis,
            refusal_message="also refused",
            confidence=0.9,
        )


def test_an_unanswered_envelope_cannot_smuggle_a_diagnosis() -> None:
    diagnosis = StructuredDiagnosis.model_validate(_valid_payload())
    with pytest.raises(ValidationError, match="must not carry a diagnosis"):
        DiagnosisEnvelope(
            answered=False,
            diagnosis=diagnosis,
            refusal_message="refused",
            confidence=0.2,
        )


def test_severity_is_a_closed_set() -> None:
    """The frontend renders each with its own token; free text arrives unstyled."""
    assert {s.value for s in Severity} == {"critical", "warning", "info"}


def test_step_order_is_explicit_rather_than_positional() -> None:
    """So a serialisation layer cannot silently reorder a procedure."""
    step = DiagnosisStep(order=1, instruction="Do it.", rationale="Because.", citation_ids=["p1"])
    assert step.order == 1
