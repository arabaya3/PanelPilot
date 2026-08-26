"""Diagnostic conversation service.

The single path a question takes to an answer, and the only place the safety
machinery upstream becomes load-bearing. Every guardrail in ``app/ai`` is
worthless if this function forgets to call it, so the order here is the
product's accuracy claim expressed as code:

1. Charge the question against the tenant's quota, atomically, before any
   work — a request that fails later has still been asked.
2. Retrieve from **production only**. ``search`` takes no index argument, so
   staging is not reachable from here even by mistake.
3. Ask the guardrail. If it refuses, render the refusal from a template and
   **return without invoking the model at all** — cheaper, and strictly safer
   than generating and then discarding, because generation that happened
   cannot be un-happened if a later branch forgets to discard it.
4. Only then generate, under a schema constraint, and only through
   ``generate_diagnosis`` which re-checks the decision itself.
5. Persist the turn.

Framework-agnostic — nothing here imports FastAPI.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.guardrails.cite_or_refuse import evaluate_confidence, verify_citations
from app.ai.guardrails.confidence import score_confidence
from app.ai.guardrails.refusal_text import render_refusal
from app.ai.prompts.diagnostic import SYSTEM_PROMPT, build_diagnostic_prompt
from app.ai.retrieval.hybrid_search import search
from app.ai.structured_output import generate_diagnosis
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.domain.auth import consume_free_question
from app.models.schemas.auth import CurrentUser
from app.models.schemas.diagnostics import (
    ConfidenceBreakdown,
    DiagnosticRequest,
    DiagnosticResponse,
    DiagnosticSession,
    DiagnosticTurn,
    GeneratedAnswer,
)
from app.models.schemas.guardrail import ConfidenceDecision
from app.models.schemas.search import RetrievedPassage
from app.models.tables.diagnostics import DiagnosticSessionRow, DiagnosticTurnRow


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
        QuotaExceededError: If the tenant has no free questions left.
        NotFoundError: If ``request.session_id`` refers to an unknown session.
    """
    # Charged first, and atomically. A question asked is a question spent
    # whether or not the evidence supports an answer — billing after the fact
    # would let a caller mine the corpus for free by asking things that refuse.
    consume_free_question(session=session, tenant_id=user.tenant_id)

    conversation = _resolve_session(session=session, user=user, request=request)

    # Production only. `search` exposes no index argument, so this cannot be
    # pointed at unverified staging content by passing an argument.
    passages = search(
        request.symptom,
        brand=request.equipment.manufacturer if request.equipment else None,
        model=request.equipment.model if request.equipment else None,
    )

    decision = evaluate_confidence(passages)
    if not decision.may_generate:
        # No model call at all. Generating and then discarding would be both
        # more expensive and less safe: the discard is one more branch that
        # can be forgotten, and a forgotten discard is an unsourced answer.
        return _persist_and_return(
            session=session,
            conversation=conversation,
            request=request,
            response=_refusal_response(conversation.id, decision, passages),
        )

    diagnosis, decision = generate_diagnosis(
        _anthropic_client(),
        model=get_settings().llm_model,
        system=SYSTEM_PROMPT,
        question=build_diagnostic_prompt(request=request, evidence=passages),
        evidence_ids={passage.id for passage in passages},
        decision=decision,
    )
    if diagnosis is None:
        # Schema-invalid output takes the same refuse path as weak evidence.
        # A response the system could not parse is one it cannot vouch for.
        return _persist_and_return(
            session=session,
            conversation=conversation,
            request=request,
            response=_refusal_response(conversation.id, decision, passages),
        )

    answer = verify_citations(
        GeneratedAnswer(
            text=diagnosis.summary,
            cited_passage_ids=list(diagnosis.summary_citation_ids),
        ),
        evidence=passages,
    )
    response = DiagnosticResponse(
        session_id=str(conversation.id),
        answer=answer,
        diagnosis=diagnosis,
        confidence=score_confidence(answer, evidence=passages),
        low_confidence=False,
    )
    return _persist_and_return(
        session=session, conversation=conversation, request=request, response=response
    )


def _anthropic_client() -> object:
    """Return a Claude client.

    Constructed here rather than at import time so a missing key fails on the
    first request rather than at startup, and so tests can substitute one
    without a live key.

    Returns:
        An Anthropic client.
    """
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value())


def _resolve_session(
    *,
    session: Session,
    user: CurrentUser,
    request: DiagnosticRequest,
) -> DiagnosticSessionRow:
    """Load the conversation this turn belongs to, or start one.

    Args:
        session: Open database session.
        user: The authenticated caller.
        request: The incoming request.

    Returns:
        The conversation row.

    Raises:
        NotFoundError: If a session id was supplied but does not belong to
            this tenant. Scoped by tenant, not just by id: an id alone would
            let one tenant append to another's conversation, and read its
            history back through ``get_session``.
    """
    if request.session_id is None:
        conversation = DiagnosticSessionRow(tenant_id=user.tenant_id)
        session.add(conversation)
        session.flush()
        return conversation

    return _load_session(session=session, user=user, session_id=request.session_id)


def _load_session(
    *,
    session: Session,
    user: CurrentUser,
    session_id: str,
) -> DiagnosticSessionRow:
    """Load a conversation belonging to this tenant.

    Args:
        session: Open database session.
        user: The authenticated caller.
        session_id: The conversation to load.

    Returns:
        The conversation row.

    Raises:
        NotFoundError: If it does not exist or belongs to another tenant.
    """
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError as exc:
        raise NotFoundError(f"no diagnostic session {session_id!r}") from exc

    found = session.scalars(
        select(DiagnosticSessionRow).where(
            DiagnosticSessionRow.id == session_uuid,
            # Scoped by tenant, not just by id. An id alone would let one
            # tenant read another's conversation history.
            DiagnosticSessionRow.tenant_id == user.tenant_id,
        )
    ).one_or_none()
    if found is None:
        raise NotFoundError(f"no diagnostic session {session_id!r}")
    return found


def _refusal_response(
    conversation_id: uuid.UUID,
    decision: ConfidenceDecision,
    passages: list[RetrievedPassage],
) -> DiagnosticResponse:
    """Render a refusal as the response the frontend receives.

    Args:
        conversation_id: The conversation this turn belongs to.
        decision: The refusing decision.
        passages: What retrieval returned, for the confidence breakdown.

    Returns:
        A response carrying the refusal message and no diagnosis. The
        confidence is still reported: an engineer who is refused deserves to
        see how close it came, and the escalation row needs the number.
    """
    top = max((p.score for p in passages), default=0.0)
    return DiagnosticResponse(
        session_id=str(conversation_id),
        refusal_message=render_refusal(decision),
        citations=list(decision.citations),
        confidence=ConfidenceBreakdown(
            overall=decision.score,
            retrieval_score=top,
            passage_agreement=0.0,
            citation_density=0.0,
        ),
        low_confidence=True,
    )


def _persist_and_return(
    *,
    session: Session,
    conversation: DiagnosticSessionRow,
    request: DiagnosticRequest,
    response: DiagnosticResponse,
) -> DiagnosticResponse:
    """Record the turn and return the response.

    Refusals are stored too. A conversation that shows only the answered turns
    reads as though nothing was ever declined, which is exactly the history an
    engineer needs when asking why the assistant would not help.

    Args:
        session: Open database session.
        conversation: The conversation row.
        request: What was asked.
        response: What is being returned.

    Returns:
        ``response``, unchanged.
    """
    position = (
        session.scalars(
            select(DiagnosticTurnRow.position)
            .where(DiagnosticTurnRow.session_id == conversation.id)
            .order_by(DiagnosticTurnRow.position.desc())
            .limit(1)
        ).one_or_none()
        or 0
    ) + 1

    session.add(
        DiagnosticTurnRow(
            session_id=conversation.id,
            position=position,
            question=request.symptom,
            answer=_stored_answer(response),
        )
    )
    # The caller commits: one transaction per request, so a failure after this
    # point rolls the turn back rather than leaving a half-recorded exchange.
    session.flush()
    return response


def _stored_answer(response: DiagnosticResponse) -> str:
    """Return the text to record for a turn.

    Args:
        response: The response being returned.

    Returns:
        The answer prose, or the refusal message. One of the two is always
        present — ``DiagnosticResponse`` refuses to be constructed otherwise.
    """
    if response.answer is not None:
        return response.answer.text
    return response.refusal_message or ""


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
        NotFoundError: If no such session exists **or** it belongs to another
            tenant. Deliberately the same error for both: distinguishing them
            tells a caller that a session id they cannot read does exist,
            which is a membership oracle over other tenants' conversations.
    """
    conversation = _load_session(session=session, user=user, session_id=session_id)
    turns = session.scalars(
        select(DiagnosticTurnRow)
        .where(DiagnosticTurnRow.session_id == conversation.id)
        .order_by(DiagnosticTurnRow.position)
    ).all()

    return DiagnosticSession(
        id=str(conversation.id),
        turns=[
            DiagnosticTurn(
                request=DiagnosticRequest(session_id=str(conversation.id), symptom=turn.question),
                # The stored turn keeps the prose, not the structured card:
                # it is a record of what the engineer was shown, and
                # re-deriving a card from it would invent detail that was
                # never displayed.
                response=DiagnosticResponse(
                    session_id=str(conversation.id),
                    refusal_message=turn.answer,
                    confidence=ConfidenceBreakdown(
                        overall=0.0,
                        retrieval_score=0.0,
                        passage_agreement=0.0,
                        citation_density=0.0,
                    ),
                    low_confidence=True,
                ),
            )
            for turn in turns
        ],
    )
