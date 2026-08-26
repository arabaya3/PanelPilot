"""Schema-constrained generation for diagnostic responses.

**Constrained, not requested.** ``generate_diagnosis`` hands the model a tool
whose ``input_schema`` is derived from ``StructuredDiagnosis`` and forces a call
to it, rather than asking in a prompt for JSON. Prompting leaves the whole
family of "the model wrote a friendly sentence before the opening brace"
failures live; constraining the output removes them at the source.

**Not yet on a live request path.** ``run_diagnosis`` in ``app/domain`` is still
a stub — BE-008 owns wiring the endpoint, and it should call
``generate_diagnosis`` rather than reaching for a client itself. Until it does,
this module constrains nothing in production. The architecture test enforces
that whatever eventually calls a model consults the guardrail first.

**One definition.** The JSON Schema the model is constrained to is derived from
``StructuredDiagnosis`` — the same model ``DiagnosticResponse`` embeds and the
API serialises. ``packages/shared-types`` generates its TypeScript from that
OpenAPI document, and the ``shared types drift`` CI job fails if the checked-in
copy differs, so the constraint and the rendered type are one definition
mechanically rather than by convention.

**A validation failure is a confidence failure.** Output that fails to validate
is not repaired, retried into shape, or shown partially. It takes AI-003's
refuse path, because a response the system could not parse is a response it
cannot vouch for.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.models.schemas.guardrail import ConfidenceDecision, DecisionOutcome, RefusalReason
from app.models.schemas.responses import StructuredDiagnosis

# Name the model sees. Stable: it appears in stored tool-call transcripts, so
# renaming it makes historical turns harder to interpret.
DIAGNOSIS_TOOL_NAME = "emit_diagnosis"

TOOL_DESCRIPTION = (
    "Return the diagnosis as structured data. Every claim must cite a passage "
    "id drawn from the supplied evidence; never cite an id that was not given "
    "to you."
)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve ``$ref`` pointers into their definitions, recursively.

    Pydantic emits nested models as ``$defs`` plus ``$ref``, and for a field
    carrying a default it emits ``$ref`` as a *sibling* of ``default`` — the
    ambiguous draft form, which a consumer may resolve either way. Inlining
    removes the ambiguity rather than betting on how the API handles it.

    Args:
        node: A JSON Schema fragment.
        defs: The ``$defs`` block the refs point into.

    Returns:
        The fragment with every ref replaced by its target.
    """
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    resolved = dict(node)
    ref = resolved.pop("$ref", None)
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = _inline_refs(defs[ref.removeprefix("#/$defs/")], defs)
        # Siblings of the ref (``default``, ``title``) win; the target supplies
        # the type.
        resolved = {**target, **resolved}

    return {key: _inline_refs(value, defs) for key, value in resolved.items()}


def _strip_prose(node: Any) -> Any:
    """Remove ``title`` and ``description`` keys from a schema fragment.

    Pydantic copies each model's full docstring into ``description``. Those are
    written for the engineers reading this file — multi-paragraph, with an
    ``Attributes:`` block and markdown — and the model would read every word of
    them on every request. The field names and the tool description carry what
    it actually needs.

    Args:
        node: A JSON Schema fragment.

    Returns:
        The fragment without human-facing prose.
    """
    if isinstance(node, list):
        return [_strip_prose(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _strip_prose(value)
        for key, value in node.items()
        if key not in {"title", "description"}
    }


def _close_objects(node: Any) -> Any:
    """Set ``additionalProperties: false`` on every object in the schema.

    Without it the model may emit extra keys, which validate here and then
    reach a frontend that does not know what they are.

    Args:
        node: A JSON Schema fragment.

    Returns:
        The fragment with every object closed.
    """
    if isinstance(node, list):
        return [_close_objects(item) for item in node]
    if not isinstance(node, dict):
        return node

    closed = {key: _close_objects(value) for key, value in node.items()}
    if closed.get("type") == "object":
        closed["additionalProperties"] = False
    return closed


def input_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Return a JSON Schema constraining a model's output to one pydantic type.

    Derived from the model rather than hand-written, then normalised into the
    form the Claude API consumes best: refs inlined, objects closed, human
    docstrings removed.

    Args:
        model: The pydantic model the output must match.

    Returns:
        A self-contained JSON Schema object with no ``$defs`` or ``$ref``.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    normalised = _close_objects(_strip_prose(_inline_refs(schema, defs)))
    # The helpers are recursive over arbitrary fragments, so they are typed
    # `Any`. The top level of a model schema is always an object.
    assert isinstance(normalised, dict)
    return normalised


def diagnosis_input_schema() -> dict[str, Any]:
    """Return the JSON Schema constraining a diagnosis.

    Returns:
        A self-contained JSON Schema object with no ``$defs`` or ``$ref``.
    """
    return input_schema_for(StructuredDiagnosis)


def diagnosis_tool_definition() -> dict[str, Any]:
    """Return the tool definition constraining the model's output.

    Returns:
        A tool definition in the shape the Claude API expects.
    """
    return {
        "name": DIAGNOSIS_TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": diagnosis_input_schema(),
    }


def extract_named_tool_payload(message: Any, tool_name: str) -> dict[str, Any] | None:
    """Pull one named tool call out of an API response.

    Args:
        message: The message returned by the Claude API.
        tool_name: Which tool call to look for.

    Returns:
        The tool call's ``input``, or ``None`` if the model emitted no call to
        that tool — which is a refusal, not an answer, and never a reason to
        fall back to reading its prose.
    """
    for block in getattr(message, "content", None) or []:
        is_call = getattr(block, "type", None) == "tool_use"
        if is_call and getattr(block, "name", None) == tool_name:
            payload = getattr(block, "input", None)
            return payload if isinstance(payload, dict) else None
    return None


def extract_tool_payload(message: Any) -> dict[str, Any] | None:
    """Pull the diagnosis tool call out of an API response.

    Args:
        message: The message returned by the Claude API.

    Returns:
        The tool call's ``input``, or ``None`` if there was no diagnosis call.
    """
    return extract_named_tool_payload(message, DIAGNOSIS_TOOL_NAME)


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
    # `PassageId` strips the model's ids during validation, so the supplied set
    # must be normalised the same way. Comparing a stripped id against an
    # unstripped set sends a correctly grounded diagnosis to the refuse path
    # over nothing but whitespace in whoever assembled the evidence.
    unknown = sorted(cited - {e.strip() for e in evidence_ids})
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
        The reason is ``UNVALIDATABLE_OUTPUT`` rather than ``BELOW_THRESHOLD``,
        because the threshold *was* met — filing it otherwise would make every
        malformed generation look like a retrieval-quality problem to whoever
        reads the escalation rows.
    """
    return ConfidenceDecision(
        outcome=DecisionOutcome.UNCERTAIN,
        score=decision.score,
        threshold=decision.threshold,
        reason=RefusalReason.UNVALIDATABLE_OUTPUT,
        # The closest match only. An answer's full citation list would present
        # this refusal as though all of them still supported it.
        citations=decision.citations[:1],
        detail=f"structured output could not be validated: {detail}",
    )


def generate_diagnosis(
    client: Any,
    *,
    model: str,
    system: str,
    question: str,
    evidence_ids: set[str],
    decision: ConfidenceDecision,
    max_tokens: int = 2048,
) -> tuple[StructuredDiagnosis | None, ConfidenceDecision]:
    """Generate a diagnosis under schema constraint, or refuse.

    The only generation call on the diagnosis path. It declines to run at all
    unless ``decision`` permits it, so the guardrail cannot be bypassed by a
    caller that forgets to check, and it forces the tool call rather than
    leaving the model free to answer in prose.

    Args:
        client: An Anthropic client.
        model: Model id to call.
        system: System prompt, including the retrieved evidence.
        question: The engineer's question.
        evidence_ids: Passage ids present in the evidence.
        decision: The cite-or-refuse verdict for this turn.
        max_tokens: Generation ceiling.

    Returns:
        ``(diagnosis, decision)`` when the model returned schema-valid output,
        otherwise ``(None, refusal)`` with the original decision when the
        guardrail refused in the first place.
    """
    if not decision.may_generate:
        # Refusals render from a fixed template without invoking the model.
        # Asking a model to decline is another generation, not a refusal.
        return None, decision

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": question}],
        tools=[diagnosis_tool_definition()],
        tool_choice={"type": "tool", "name": DIAGNOSIS_TOOL_NAME},
    )
    return structured_or_refuse(
        extract_tool_payload(message), evidence_ids=evidence_ids, decision=decision
    )
