"""Rendering a refusal decision into the text an engineer reads.

**Fixed templates, not generation.** A refusal produced by asking a model to
decline is another generation that happened to decline this time — it can be
argued out of declining, and it can invent a reason. These are `str.format`
over a decision the guardrail already made.

There is one template per :class:`RefusalReason`, and a test pins the mapping
as exhaustive: a reason added without text to render surfaces to an engineer
as a blank card, which is the failure the response contract exists to remove.

**What the engineer is told.** Enough to act: what was searched, why it fell
short, and what would resolve it. Never the raw validation error — that
carries the model's own unvalidated output, which is the thing being refused.
"""

from __future__ import annotations

from app.models.schemas.guardrail import ConfidenceDecision, RefusalReason

_TEMPLATES: dict[RefusalReason, str] = {
    RefusalReason.NO_EVIDENCE: (
        "No document in the library covers this. Rather than answer from "
        "general knowledge, PanelPilot is stopping here — add the "
        "manufacturer's manual for this equipment and ask again."
    ),
    RefusalReason.BELOW_THRESHOLD: (
        "The closest documentation found is only a partial match, below the "
        "confidence needed to answer safely. The nearest passage is cited so "
        "you can judge it yourself."
    ),
    RefusalReason.OUT_OF_VALIDATED_RANGE: (
        "This falls outside the range the calculation has been validated "
        "against, so the result would not be trustworthy. Consult the "
        "manufacturer's tables directly."
    ),
    RefusalReason.UNUSABLE_SCORE: (
        "Retrieval returned a result PanelPilot could not score, so it cannot "
        "tell which passage to trust. This is a system fault rather than a gap "
        "in the documentation — please report it."
    ),
    RefusalReason.UNVALIDATABLE_OUTPUT: (
        "The documentation supports an answer, but the generated response did "
        "not pass validation and has been withheld rather than shown "
        "partially. Asking again will usually succeed."
    ),
}


def render_refusal(decision: ConfidenceDecision) -> str:
    """Render the message shown in place of a diagnosis.

    Args:
        decision: A decision that does not permit generation.

    Returns:
        The refusal text, with the validated-range specifics appended when the
        guardrail supplied them — "out of range" alone tells an engineer
        nothing they can act on, whereas the quantity and its bounds tell them
        what to change.

    Raises:
        ValueError: If ``decision`` permits generation, or carries no reason.
            Rendering a refusal for a decision that allowed an answer would put
            a refusal in front of an engineer who was entitled to one.
    """
    if decision.may_generate:
        raise ValueError("render_refusal called on a decision that permits generation")
    if decision.reason is None:
        raise ValueError("a refusing decision must carry a reason")

    text = _TEMPLATES[decision.reason]

    # The guardrail's own detail is safe to show; a validation error's is not,
    # because it quotes the model output being refused.
    if decision.reason is RefusalReason.OUT_OF_VALIDATED_RANGE and decision.detail:
        text = f"{text} {decision.detail}"

    return text
