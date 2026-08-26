"""Diagnostic conversation schemas."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from app.models.schemas.responses import NonBlankText, StructuredDiagnosis
from app.models.schemas.search import Citation


class EquipmentContext(BaseModel):
    """What the engineer is working on."""

    manufacturer: str | None = None
    model: str | None = None
    fault_codes: list[str] = []


class DiagnosticRequest(BaseModel):
    """A single diagnostic question."""

    session_id: str | None = None
    symptom: str
    equipment: EquipmentContext | None = None


class GeneratedAnswer(BaseModel):
    """The model's raw answer, before citations are verified."""

    text: str
    cited_passage_ids: list[str]


class VerifiedAnswer(BaseModel):
    """An answer whose every citation resolves to supplied evidence.

    Attributes:
        text: The prose answer. Non-blank for the same reason the structured
            summary is: "   " renders as an empty card.
        citations: Resolved citations backing the text.
    """

    text: NonBlankText
    citations: list[Citation]


class ConfidenceBreakdown(BaseModel):
    """Overall confidence with the per-signal components behind it."""

    overall: float
    retrieval_score: float
    passage_agreement: float
    citation_density: float


class DiagnosticResponse(BaseModel):
    """A completed diagnostic turn as returned to the caller.

    This is the type the frontend renders, and the schema constraining
    generation is derived from the ``StructuredDiagnosis`` embedded here — the
    same model, not a copy of it. That is what makes "the constraint and the rendered type
    cannot drift" true rather than aspirational: there is one definition, and
    ``packages/shared-types`` regenerates its TypeScript from this schema.

    Attributes:
        session_id: The conversation this turn belongs to.
        answer: Prose form of the answer, with resolved citations. Absent on a
            refusal — a required-but-empty answer object would be one more
            thing the frontend has to inspect before deciding what to show.
        diagnosis: The structured form, when the model produced schema-valid
            output. ``None`` on a refusal, which is the only case where the
            frontend renders the refusal template instead of the card.
        confidence: Per-signal confidence behind the decision.
        low_confidence: Whether to show the uncertainty banner.
        refusal_message: Rendered refusal text, present exactly when
            ``diagnosis`` is absent.
    """

    session_id: str
    answer: VerifiedAnswer | None = None
    diagnosis: StructuredDiagnosis | None = None
    confidence: ConfidenceBreakdown
    low_confidence: bool
    refusal_message: str | None = None

    @model_validator(mode="after")
    def _answers_or_refuses(self) -> DiagnosticResponse:
        """Keep the response from representing a state the frontend cannot render.

        Returns:
            The validated response.

        Raises:
            ValueError: If it carries both a diagnosis and a refusal, or
                neither. Either would leave the frontend deciding for itself
                which half to believe, which is the parsing-by-hand this
                contract exists to remove.
        """
        refusing = bool((self.refusal_message or "").strip())
        if self.diagnosis is not None and refusing:
            raise ValueError("a response cannot both diagnose and refuse")
        if self.diagnosis is None and not refusing:
            raise ValueError("a response with no diagnosis must carry a refusal message")
        # Both forms of the answer travel together or not at all. A diagnosis
        # with no prose, or prose with no diagnosis, leaves the frontend
        # deciding which half to render.
        if self.diagnosis is not None and self.answer is None:
            raise ValueError("a diagnosing response must carry its prose answer")
        if self.diagnosis is None and self.answer is not None:
            raise ValueError("a refusing response must not carry an answer")
        return self


class DiagnosticTurn(BaseModel):
    """One stored exchange in a session."""

    request: DiagnosticRequest
    response: DiagnosticResponse


class DiagnosticSession(BaseModel):
    """A diagnostic conversation and its ordered turns."""

    id: str
    turns: list[DiagnosticTurn]
