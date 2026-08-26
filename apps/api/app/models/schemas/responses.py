"""The structured diagnosis contract.

This is the single definition. The JSON Schema constraining generation, the
OpenAPI document, and the frontend's TypeScript types all derive from these
models — so "the schema the model must satisfy" and "the shape the frontend
renders" are the same object rather than two things somebody keeps in sync.

Every field the frontend renders is required. An optional field is one the
frontend has to defensively handle, which is the parsing-by-hand this contract
exists to remove.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.models.schemas.search import Citation


class Severity(StrEnum):
    """How urgent a finding is.

    A closed set: the frontend renders each with its own colour token, so a
    free-text severity would arrive as an unstyled string.
    """

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class DiagnosisStep(BaseModel):
    """One step an engineer should carry out.

    Attributes:
        order: 1-indexed position. Explicit rather than implied by array index
            so a step cannot be silently reordered by a serialisation layer.
        instruction: What to do, in one imperative sentence.
        rationale: Why this step, so the engineer can judge whether it applies
            to their situation rather than following blindly.
        citation_ids: Passage ids supporting this step. **Required and
            non-empty** — a step with no source is exactly what cite-or-refuse
            exists to prevent, and making it optional would let one through.
        severity: Urgency of this step.
    """

    order: int = Field(ge=1)
    instruction: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    severity: Severity = Severity.INFO

    @model_validator(mode="after")
    def _reject_blank_citation_ids(self) -> DiagnosisStep:
        """Refuse whitespace-only citation ids.

        Returns:
            The validated step.

        Raises:
            ValueError: If any citation id is blank. ``min_length=1`` on the
                list catches an empty list, not an empty string inside it.
        """
        if any(not cid.strip() for cid in self.citation_ids):
            raise ValueError(f"step {self.order} carries a blank citation id")
        return self


class StructuredDiagnosis(BaseModel):
    """A complete diagnostic response, as constrained at generation time.

    Attributes:
        summary: One-paragraph answer to the engineer's question.
        summary_citation_ids: Passages supporting the summary. Required for the
            same reason as a step's.
        steps: Ordered actions. Non-empty: a diagnosis with no action is not a
            diagnosis, and the refuse path exists for when there is nothing to
            say.
        severity: Overall urgency, for the card header.
        equipment_model: The model this diagnosis applies to, echoed back so an
            engineer can see the assistant understood which unit they meant.
    """

    summary: str = Field(min_length=1)
    summary_citation_ids: list[str] = Field(min_length=1)
    steps: list[DiagnosisStep] = Field(min_length=1)
    severity: Severity = Severity.INFO
    equipment_model: str | None = None

    @model_validator(mode="after")
    def _steps_are_sequential(self) -> StructuredDiagnosis:
        """Require step numbering to be 1..n with no gaps or repeats.

        Returns:
            The validated diagnosis.

        Raises:
            ValueError: If the ordering is not a clean sequence. A procedure
                jumping from step 2 to step 4 reads as though a step is missing
                — which, for an isolation procedure, is a safety problem rather
                than a cosmetic one.
        """
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError(f"steps must be numbered 1..{len(orders)}, got {orders}")
        return self


class DiagnosisEnvelope(BaseModel):
    """What the API returns: either a diagnosis or a refusal, never both.

    Attributes:
        answered: Whether a diagnosis is present. The frontend branches on this
            single boolean rather than probing which fields are populated.
        diagnosis: The structured answer, when answered.
        refusal_message: The rendered uncertain-state text, when not.
        citations: Resolved citations for whichever branch applies.
        confidence: The retrieval-derived score behind the decision.
    """

    answered: bool
    diagnosis: StructuredDiagnosis | None = None
    refusal_message: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _exactly_one_branch(self) -> DiagnosisEnvelope:
        """Keep the envelope from representing a state the frontend cannot render.

        Returns:
            The validated envelope.

        Raises:
            ValueError: If it claims to be answered without a diagnosis, or
                unanswered without a message. Either would leave the frontend
                rendering an empty card, which is the failure mode a contract
                is supposed to eliminate.
        """
        if self.answered:
            if self.diagnosis is None:
                raise ValueError("answered envelope carries no diagnosis")
            if self.refusal_message is not None:
                raise ValueError("answered envelope must not carry a refusal message")
        else:
            if self.diagnosis is not None:
                raise ValueError("unanswered envelope must not carry a diagnosis")
            if not (self.refusal_message or "").strip():
                raise ValueError("unanswered envelope must explain itself")
        return self
