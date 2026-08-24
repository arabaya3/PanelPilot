"""Diagnostic conversation service.

Orchestrates a single diagnostic turn: retrieve evidence, run the model against
the diagnostic prompt, apply the cite-or-refuse guardrail, persist the turn.
Framework-agnostic — nothing here imports FastAPI.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schemas.auth import CurrentUser
from app.models.schemas.diagnostics import (
    DiagnosticRequest,
    DiagnosticResponse,
    DiagnosticSession,
)


def run_diagnosis(
    *,
    session: Session,
    user: CurrentUser,
    request: DiagnosticRequest,
) -> DiagnosticResponse:
    """Produce one grounded diagnostic answer for a user's symptom description.

    Retrieval runs against the production index only; if the guardrail finds no
    citable passage the call refuses rather than answering unsourced.

    Args:
        session: Open database session for persisting the conversation turn.
        user: The authenticated caller.
        request: Symptom description, equipment context, and session id.

    Returns:
        The diagnosis with its supporting citations and confidence score.

    Raises:
        InsufficientEvidenceError: If no citable evidence supports an answer.
        NotFoundError: If ``request.session_id`` refers to an unknown session.
    """
    raise NotImplementedError


def get_session(
    *,
    session: Session,
    user: CurrentUser,
    session_id: str,
) -> DiagnosticSession:
    """Load a diagnostic session and its turns.

    Args:
        session: Open database session.
        user: The authenticated caller.
        session_id: Identifier of the diagnostic session to load.

    Returns:
        The session with its ordered turns.

    Raises:
        NotFoundError: If no such session exists.
        AuthorizationError: If the session belongs to another user.
    """
    raise NotImplementedError
