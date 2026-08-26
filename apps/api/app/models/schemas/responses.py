"""The structured diagnosis contract.

This is the single definition. The JSON Schema constraining generation, the
OpenAPI document, and the frontend's TypeScript types all derive from these
models — so "the schema the model must satisfy" and "the shape the frontend
renders" are the same object rather than two things somebody keeps in sync.
``DiagnosticResponse`` embeds ``StructuredDiagnosis`` directly for that reason;
a parallel response envelope would reintroduce exactly the drift this removes.

Every field the frontend renders is required. An optional field is one the
frontend has to defensively handle, which is the parsing-by-hand this contract
exists to remove.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

# Prose the frontend renders. `min_length` alone accepts "   ", which renders
# as a blank card — the exact failure this contract exists to remove — so the
# constraint strips first. The ceiling is not a business rule; it bounds what a
# runaway generation can push into a browser.
NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]

# Passage ids are short opaque tokens, not prose.
PassageId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


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
        citation_ids: Passage ids supporting this step. Required, non-empty,
            and deduplicated — a step with no source is exactly what
            cite-or-refuse exists to prevent, and the same id repeated would
            overstate how much evidence there is.
        severity: Urgency of this step. Required rather than defaulted: a
            missing severity defaulting to ``INFO`` would render an arc-flash
            warning in the same colour as a note.
    """

    order: int = Field(ge=1)
    instruction: NonBlankText
    rationale: NonBlankText
    citation_ids: list[PassageId] = Field(min_length=1)
    severity: Severity

    @model_validator(mode="after")
    def _citations_are_distinct(self) -> DiagnosisStep:
        """Refuse a repeated citation id.

        Returns:
            The validated step.

        Raises:
            ValueError: If an id appears twice. Presenting one passage three
                times reads to an engineer as three corroborating sources.
        """
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError(f"step {self.order} cites the same passage more than once")
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
        severity: Overall urgency, for the card header. Required, as a step's is.
        equipment_model: The model this diagnosis applies to, echoed back so an
            engineer can see the assistant understood which unit they meant.
    """

    summary: NonBlankText
    summary_citation_ids: list[PassageId] = Field(min_length=1)
    steps: list[DiagnosisStep] = Field(min_length=1, max_length=50)
    severity: Severity
    equipment_model: PassageId | None = None

    @model_validator(mode="after")
    def _summary_citations_are_distinct(self) -> StructuredDiagnosis:
        """Refuse a repeated summary citation id.

        Returns:
            The validated diagnosis.

        Raises:
            ValueError: If an id appears twice, for the same reason as a step's.
        """
        if len(set(self.summary_citation_ids)) != len(self.summary_citation_ids):
            raise ValueError("the summary cites the same passage more than once")
        return self

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
