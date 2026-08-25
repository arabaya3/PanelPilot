"""The cite-or-refuse decision.

One decision type, produced by one function, consumed by every code path that
would otherwise generate a response. The point of centralising it is that
"never guess" cannot be a property each path implements for itself — the first
path that forgets is the one that fabricates an answer, and it will look
exactly like the paths that did not.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.models.schemas.search import Citation


class DecisionOutcome(StrEnum):
    """What a response path is permitted to do.

    Only ``ANSWER`` allows generation. Both other outcomes short-circuit to a
    fixed template **without invoking the model at all** — a refusal produced by
    asking a model to refuse is not a refusal, it is another generation that
    happens to have declined this time.
    """

    ANSWER = "answer"
    # Something was found, but not well enough to answer from. The engineer is
    # pointed at the closest match so they can judge it themselves.
    UNCERTAIN = "uncertain"
    # Nothing cleared even the floor. No document is worth naming.
    NO_VERIFIED_SOURCE = "no_verified_source"


class RefusalReason(StrEnum):
    """Why generation was refused, for the template and for the escalation row."""

    NO_EVIDENCE = "no_evidence"
    BELOW_THRESHOLD = "below_threshold"
    OUT_OF_VALIDATED_RANGE = "out_of_validated_range"
    # Retrieval returned a score that is not a real number in [0, 1].
    # Refusing beats guessing which passage to trust.
    UNUSABLE_SCORE = "unusable_score"


class ConfidenceDecision(BaseModel):
    """The verdict returned by ``evaluate_confidence``.

    Attributes:
        outcome: Whether generation may proceed.
        score: The retrieval-derived confidence, in [0, 1]. Derived from the
            top retrieval score — never from a model's self-reported
            confidence, which is not calibrated and is highest exactly when a
            model is confidently wrong.
        threshold: The answer threshold this decision was taken against,
            recorded so a decision can be re-explained later without guessing
            which configuration was live.
        reason: Set whenever ``outcome`` is not ``ANSWER``.
        citations: Evidence supporting an answer, or the closest match when
            uncertain. Empty only for ``NO_VERIFIED_SOURCE``.
    """

    outcome: DecisionOutcome
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    reason: RefusalReason | None = None
    citations: list[Citation] = Field(default_factory=list)
    # Human-readable specifics for the refusal template. "Out of range" alone
    # tells an engineer nothing they can act on; naming the quantity and the
    # validated bounds tells them what to change or which table to consult.
    detail: str | None = None

    @property
    def may_generate(self) -> bool:
        """Report whether a response path may invoke generation."""
        return self.outcome is DecisionOutcome.ANSWER

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> ConfidenceDecision:
        """Keep outcome and reason from disagreeing.

        Returns:
            The validated decision.

        Raises:
            ValueError: If an answer carries a refusal reason, a refusal
                carries none, or an answer carries no citations. Each of those
                is a decision that cannot be explained to the engineer who
                received it.
        """
        if self.outcome is DecisionOutcome.ANSWER:
            if self.reason is not None:
                raise ValueError("an ANSWER decision must not carry a refusal reason")
            if not self.citations:
                raise ValueError("an ANSWER decision must carry the evidence it rests on")
        elif self.reason is None:
            raise ValueError(f"a {self.outcome.value} decision must say why")

        if self.outcome is DecisionOutcome.NO_VERIFIED_SOURCE and self.citations:
            raise ValueError("NO_VERIFIED_SOURCE means nothing was worth naming; it cannot cite")
        return self
