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

from app.ai.structured_output import (
    DIAGNOSIS_TOOL_NAME,
    diagnosis_input_schema,
    diagnosis_tool_definition,
    extract_tool_payload,
    generate_diagnosis,
    parse_tool_output,
    structured_or_refuse,
)
from app.models.schemas.guardrail import (
    ConfidenceDecision,
    DecisionOutcome,
    RefusalReason,
)
from app.models.schemas.responses import (
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


def test_the_tool_schema_tracks_the_response_model() -> None:
    """One definition, so the constraint cannot drift from the type.

    Asserted against the model's own field names rather than against
    ``model_json_schema()`` — comparing the schema to itself would pass no
    matter what the normalisation did to it.
    """
    definition = diagnosis_tool_definition()
    assert definition["name"] == DIAGNOSIS_TOOL_NAME
    assert set(definition["input_schema"]["properties"]) == set(StructuredDiagnosis.model_fields)


def test_the_schema_marks_every_rendered_field_required() -> None:
    """An optional field is one the frontend must defensively handle.

    ``severity`` included: a model that omits it under uncertainty would
    otherwise have that read as the least alarming value.
    """
    required = set(diagnosis_input_schema()["required"])
    assert required == {"summary", "summary_citation_ids", "steps", "severity"}


def test_the_schema_carries_no_refs_the_api_might_mishandle() -> None:
    """Pydantic emits `$ref` as a sibling of `default` — the ambiguous form.

    Inlined here rather than betting on how the API resolves it.
    """
    rendered = json.dumps(diagnosis_input_schema())
    assert "$ref" not in rendered
    assert "$defs" not in rendered


def test_every_object_in_the_schema_is_closed() -> None:
    """Otherwise the model may emit keys the frontend does not know."""

    def check(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                check(item)
        elif isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                check(value)

    check(diagnosis_input_schema())


def test_the_schema_does_not_ship_python_docstrings_to_the_model() -> None:
    """Keep human prose out of the constraint.

    Docstrings are written for engineers reading the file, and cost tokens on
    every single request.
    """
    rendered = json.dumps(diagnosis_input_schema())
    assert "Attributes:" not in rendered
    assert "cite-or-refuse" not in rendered


def test_the_schema_still_describes_the_nested_step_fields() -> None:
    """Guard against the normalisation flattening the schema into nothing."""
    step = diagnosis_input_schema()["properties"]["steps"]["items"]
    assert set(step["properties"]) == set(DiagnosisStep.model_fields)
    assert set(step["required"]) == set(DiagnosisStep.model_fields)


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
                "instruction": f"Step {n + 1} for finding {index}: r\u00e9gler le variateur.",
                "rationale": f"Because condition {n} applies \u2014 see \u00a7{n}.",
                "citation_ids": (
                    # Some steps rest on two passages, some on one.
                    sorted(EVIDENCE_IDS)[:2]
                    if (index + n) % 3 == 0
                    else [sorted(EVIDENCE_IDS)[(index + n) % 3]]
                ),
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
    # Multi-citation and single-citation steps both occur.
    counts = {len(step["citation_ids"]) for p in payloads for step in p["steps"]}
    assert counts == {1, 2}
    # Non-ASCII prose is present, since real manuals are not ASCII.
    assert any("\u00e9" in p["steps"][0]["instruction"] for p in payloads)


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
        ("missing severity", {k: v for k, v in _valid_payload().items() if k != "severity"}),
        ("whitespace-only summary", _valid_payload(summary="   ")),
        ("duplicate summary citations", _valid_payload(summary_citation_ids=["p1", "p1"])),
        (
            "step numbering gap",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "First.",
                        "rationale": "Because.",
                        "citation_ids": ["p1"],
                        "severity": "info",
                    },
                    {
                        "order": 3,
                        "instruction": "Third.",
                        "rationale": "Because.",
                        "citation_ids": ["p1"],
                        "severity": "info",
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
                        "severity": "info",
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
                        "severity": "info",
                    }
                ]
            ),
        ),
        (
            "duplicate step citations",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "Do it.",
                        "rationale": "Because.",
                        "citation_ids": ["p1", "p1"],
                        "severity": "info",
                    }
                ]
            ),
        ),
        (
            "whitespace-only instruction",
            _valid_payload(
                steps=[
                    {
                        "order": 1,
                        "instruction": "   ",
                        "rationale": "Because.",
                        "citation_ids": ["p1"],
                        "severity": "info",
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


def test_a_correct_citation_is_not_refused_over_stray_whitespace() -> None:
    """`PassageId` strips the model's ids, so the supplied set must match.

    Comparing a stripped id against an unstripped evidence set sends a
    correctly grounded diagnosis to the refuse path over nothing but the
    whitespace in whoever assembled the evidence.
    """
    diagnosis = parse_tool_output(_valid_payload(), evidence_ids={" p1 ", "p2\n", "\tp3"})
    assert diagnosis.summary_citation_ids == ["p1"]


def test_normalising_the_evidence_set_does_not_admit_an_invented_citation() -> None:
    """Stripping must not become a way to match something that is not there."""
    with pytest.raises(ValueError, match="never supplied"):
        parse_tool_output(_valid_payload(summary_citation_ids=["p9"]), evidence_ids={" p1 ", "p2"})


def test_the_refusal_is_not_filed_as_a_threshold_problem() -> None:
    """The evidence cleared the threshold; the output failed.

    Filing it as BELOW_THRESHOLD would send anyone tuning the threshold off
    the escalation rows after a signal that has nothing to do with it.
    """
    _, decision = structured_or_refuse(
        {"broken": True}, evidence_ids=EVIDENCE_IDS, decision=_permitting_decision()
    )
    assert decision.reason is not RefusalReason.BELOW_THRESHOLD


def test_a_refusal_survives_a_decision_with_no_citations() -> None:
    """Build a refusal from a decision that cites nothing.

    `_to_refusal` slices citations, so the refusal must still be
    constructible when there was nothing to slice.
    """
    bare = ConfidenceDecision(
        outcome=DecisionOutcome.UNCERTAIN,
        score=0.4,
        threshold=0.6,
        reason=RefusalReason.BELOW_THRESHOLD,
    )
    diagnosis, decision = structured_or_refuse(
        {"broken": True}, evidence_ids=EVIDENCE_IDS, decision=bare
    )
    assert diagnosis is None
    assert decision.citations == []


def test_the_refusal_preserves_the_original_confidence() -> None:
    """The turn must stay explainable: good evidence, unusable output."""
    original = _permitting_decision()
    _, decision = structured_or_refuse(
        {"broken": True}, evidence_ids=EVIDENCE_IDS, decision=original
    )
    assert decision.score == original.score
    assert decision.threshold == original.threshold
    assert decision.reason is RefusalReason.UNVALIDATABLE_OUTPUT


def test_valid_output_passes_the_decision_through_unchanged() -> None:
    original = _permitting_decision()
    diagnosis, decision = structured_or_refuse(
        _valid_payload(), evidence_ids=EVIDENCE_IDS, decision=original
    )
    assert diagnosis is not None
    assert decision is original


def test_severity_is_a_closed_set() -> None:
    """The frontend renders each with its own token; free text arrives unstyled."""
    assert {s.value for s in Severity} == {"critical", "warning", "info"}


def test_step_order_is_explicit_rather_than_positional() -> None:
    """So a serialisation layer cannot silently reorder a procedure."""
    step = DiagnosisStep(
        order=1,
        instruction="Do it.",
        rationale="Because.",
        citation_ids=["p1"],
        severity=Severity.INFO,
    )
    assert step.order == 1


# --- generation is constrained, not requested ------------------------------


class _Block:
    """One content block of a fake API response."""

    def __init__(self, kind: str, name: str | None = None, payload: Any = None) -> None:
        # Attribute names mirror the API's block shape; the parameter names do
        # not have to, and `type`/`input` would shadow builtins.
        self.type = kind
        self.name = name
        self.input = payload


class _Message:
    """A fake API response."""

    def __init__(self, *blocks: _Block) -> None:
        self.content = list(blocks)


class _FakeClient:
    """Records the request instead of issuing it."""

    def __init__(self, message: _Message) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return self._message


def _client_returning(payload: Any) -> _FakeClient:
    return _FakeClient(_Message(_Block(kind="tool_use", name=DIAGNOSIS_TOOL_NAME, payload=payload)))


def _generate(client: _FakeClient, decision: ConfidenceDecision | None = None) -> Any:
    return generate_diagnosis(
        client,
        model="claude-sonnet-5",
        system="evidence here",
        question="Why does the drive trip?",
        evidence_ids=EVIDENCE_IDS,
        decision=decision or _permitting_decision(),
    )


def test_generation_forces_the_tool_rather_than_asking_for_json() -> None:
    """The whole point of AI-004: the model cannot answer in prose."""
    client = _client_returning(_valid_payload())
    _generate(client)
    request = client.calls[0]
    assert request["tool_choice"] == {"type": "tool", "name": DIAGNOSIS_TOOL_NAME}
    assert [t["name"] for t in request["tools"]] == [DIAGNOSIS_TOOL_NAME]


def test_generation_sends_the_derived_schema() -> None:
    """Not a hand-written one that could disagree with the response type."""
    client = _client_returning(_valid_payload())
    _generate(client)
    assert client.calls[0]["tools"][0]["input_schema"] == diagnosis_input_schema()


def test_generation_returns_the_validated_diagnosis() -> None:
    client = _client_returning(_valid_payload())
    diagnosis, decision = _generate(client)
    assert diagnosis is not None
    assert decision.may_generate


def test_generation_refuses_without_calling_the_model() -> None:
    """A refusal produced by asking a model to refuse is another generation."""
    refused = ConfidenceDecision(
        outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
        score=0.0,
        threshold=0.6,
        reason=RefusalReason.NO_EVIDENCE,
    )
    client = _client_returning(_valid_payload())
    diagnosis, decision = _generate(client, refused)
    assert diagnosis is None
    assert client.calls == [], "the guardrail was consulted but not obeyed"
    assert decision is refused


def test_malformed_generation_becomes_a_refusal_end_to_end() -> None:
    client = _client_returning({"summary": "no steps"})
    diagnosis, decision = _generate(client)
    assert diagnosis is None
    assert decision.reason is RefusalReason.UNVALIDATABLE_OUTPUT


def test_prose_alongside_the_tool_call_is_ignored() -> None:
    """The structured payload is the answer; the chatter is not."""
    client = _FakeClient(
        _Message(
            _Block(kind="text"),
            _Block(kind="tool_use", name=DIAGNOSIS_TOOL_NAME, payload=_valid_payload()),
        )
    )
    diagnosis, _ = _generate(client)
    assert diagnosis is not None


def test_a_prose_only_reply_is_a_refusal_not_an_answer() -> None:
    """Never fall back to reading the model's sentences."""
    client = _FakeClient(_Message(_Block(kind="text")))
    diagnosis, decision = _generate(client)
    assert diagnosis is None
    assert not decision.may_generate


def test_a_call_to_some_other_tool_is_not_a_diagnosis() -> None:
    client = _FakeClient(
        _Message(_Block(kind="tool_use", name="something_else", payload=_valid_payload()))
    )
    assert (
        extract_tool_payload(_Message(_Block(kind="tool_use", name="something_else", payload={})))
        is None
    )
    diagnosis, _ = _generate(client)
    assert diagnosis is None


def test_an_empty_response_extracts_nothing() -> None:
    assert extract_tool_payload(_Message()) is None
