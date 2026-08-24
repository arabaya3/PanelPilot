"""Diagnostic conversation schemas."""

from __future__ import annotations

from pydantic import BaseModel

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
    """An answer whose every citation resolves to supplied evidence."""

    text: str
    citations: list[Citation]


class ConfidenceBreakdown(BaseModel):
    """Overall confidence with the per-signal components behind it."""

    overall: float
    retrieval_score: float
    passage_agreement: float
    citation_density: float


class DiagnosticResponse(BaseModel):
    """A completed diagnostic turn as returned to the caller."""

    session_id: str
    answer: VerifiedAnswer
    confidence: ConfidenceBreakdown
    low_confidence: bool


class DiagnosticTurn(BaseModel):
    """One stored exchange in a session."""

    request: DiagnosticRequest
    response: DiagnosticResponse


class DiagnosticSession(BaseModel):
    """A diagnostic conversation and its ordered turns."""

    id: str
    turns: list[DiagnosticTurn]
