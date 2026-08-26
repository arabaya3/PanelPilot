"""Schema-constrained generation for diagnostic responses.

**One schema, two consumers.** The JSON Schema the model is constrained to and
the type the frontend renders are derived from the same pydantic model. They
cannot drift, because there is nothing to keep in sync — the frontend's types
are generated from the API's OpenAPI document, which is generated from these
same models.

**Constrained, not requested.** The model is given the schema through the
Claude API's tool-calling mechanism rather than asked in a prompt to emit JSON.
Prompting for JSON leaves the whole family of "the model wrote a friendly
sentence before the opening brace" failures live; constraining the output
removes them at the source.

**A validation failure is a confidence failure.** If output somehow fails to
validate, it is not repaired, retried into shape, or shown partially. It takes
AI-003's refuse path, because a response the system could not parse is a
response it cannot vouch for — and AI-004's zero-tolerance target is only
achievable *because* that fallback exists.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.guardrails.cite_or_refuse import evaluate_confidence
from app.models.schemas.guardrail import ConfidenceDecision, DecisionOutcome, RefusalReason
from app.models.schemas.responses import StructuredDiagnosis

# Name the model sees. Stable: it appears in stored tool-call transcripts, so
# renaming it makes historical turns harder to interpret.
DIAGNOSIS_TOOL_NAME = "emit_diagnosis"


def diagnosis_tool_definition() -> dict[str, Any]:
    """Return the tool definition constraining the model's output.

    Generated from ``StructuredDiagnosis`` rather than hand-written, so the
    constraint and the response type cannot disagree about what a valid
    diagnosis looks like.

    Returns:
        A tool definition in the shape the Claude API expects.
    """
    return {
        "name": DIAGNOSIS_TOOL_NAME,
        "description": (
            "Return the diagnosis as structured data. Every claim must cite a "
            "passage id from the supplied evidence."
        ),
        "input_schema": StructuredDiagnosis.model_json_schema(),
    }


def parse_tool_output(
    payload: dict[str, Any],
    *,
    evidence_ids: set[str],
) -> StructuredDiagnosis:
    """Validate a tool-call payload into a diagnosis.

    Args:
        payload: The raw ``input`` from the model's tool call.
        evidence_ids: Passage ids the model was actually shown.

    Returns:
        The validated diagnosis.

    Raises:
        ValidationError: If the payload does not match the schema.
        ValueError: If it cites a passage that was never supplied. Constrained
            generation guarantees the shape, not the truthfulness of the ids —
            a well-formed citation of a passage the model invented is exactly
            the failure the citation check exists for.
    """
    diagnosis = StructuredDiagnosis.model_validate(payload)

    cited = {c for step in diagnosis.steps for c in step.citation_ids}
    cited |= set(diagnosis.summary_citation_ids)
    unknown = sorted(cited - evidence_ids)
    if unknown:
        raise ValueError(f"diagnosis cites passages that were never supplied: {unknown}")

    return diagnosis


def structured_or_refuse(
    payload: dict[str, Any] | None,
    *,
    evidence_ids: set[str],
    decision: ConfidenceDecision,
) -> tuple[StructuredDiagnosis | None, ConfidenceDecision]:
    """Return a validated diagnosis, or convert the failure into a refusal.

    The single place a generation result becomes either a response or a
    refusal. Callers get one of the two and never a half-built object, so no
    response path has to decide for itself what to do with malformed output.

    Args:
        payload: The model's tool-call input, or ``None`` if it emitted no
            tool call at all.
        evidence_ids: Passage ids the model was shown.
        decision: The confidence decision that permitted generation.

    Returns:
        ``(diagnosis, decision)`` on success, or ``(None, refusal)`` when the
        output could not be validated. The refusal carries the reason so the
        template can say what happened rather than showing nothing.
    """
    if payload is None:
        return None, _to_refusal(decision, "the model returned no structured output")

    try:
        return parse_tool_output(payload, evidence_ids=evidence_ids), decision
    except (ValidationError, ValueError) as exc:
        # Never repaired and never partially rendered. A response the system
        # could not parse is one it cannot vouch for.
        return None, _to_refusal(decision, str(exc))


def _to_refusal(decision: ConfidenceDecision, detail: str) -> ConfidenceDecision:
    """Convert a permitting decision into a refusal carrying the failure.

    Args:
        decision: The decision that permitted generation.
        detail: What went wrong, for the refusal template.

    Returns:
        A refusal preserving the original score and threshold, so the turn can
        still be explained: the evidence was good enough, the output was not.
    """
    return ConfidenceDecision(
        outcome=DecisionOutcome.UNCERTAIN,
        score=decision.score,
        threshold=decision.threshold,
        reason=RefusalReason.BELOW_THRESHOLD,
        citations=decision.citations[:1],
        detail=f"structured output could not be validated: {detail}",
    )


def evidence_gate(passages: list[Any], *, threshold: float | None = None) -> ConfidenceDecision:
    """Re-export of the AI-003 gate, so this module has one obvious entry point.

    Args:
        passages: Retrieved passages.
        threshold: Answer threshold; defaults to the configured value.

    Returns:
        The confidence decision.
    """
    return evaluate_confidence(passages, threshold=threshold)
