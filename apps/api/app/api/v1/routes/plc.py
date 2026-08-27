"""PLC code generation and review endpoints.

Two surfaces: generate code from a description, and review code an engineer
already has. Both return the shape FE-009 draws, and both are thin — they
parse, delegate to ``app.domain.plc``, and map one failure onto a status code.

The policy that makes these endpoints trustworthy (a validator that raises is
not a validator that passed) lives in the domain, not here. A route is the
wrong place for it: it would be untestable without a client, and invisible to
any caller that is not an HTTP request.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.domain import plc as plc_domain
from app.models.schemas.plc import (
    PlcGenerationRequest,
    PlcGenerationResult,
    PlcValidationRequest,
    PlcValidationResult,
)

router = APIRouter()


@router.post("/generate", response_model=PlcGenerationResult)
def generate(payload: PlcGenerationRequest) -> PlcGenerationResult:
    """Generate PLC code for a description, with its validation verdict.

    Raises:
        HTTPException: 422 if the request cannot be generated as asked.
    """
    try:
        return plc_domain.generate_code(payload)
    except plc_domain.PlcError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/review", response_model=PlcValidationResult)
def review(payload: PlcValidationRequest) -> PlcValidationResult:
    """Validate code the caller already has."""
    return plc_domain.review_code(payload.source, dialect=payload.dialect)
