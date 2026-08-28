"""Diagnostic conversation service.

The single path a question takes to an answer, and the only place the safety
machinery upstream becomes load-bearing. Every guardrail in ``app/ai`` is
worthless if this function forgets to call it, so the order here is the
product's accuracy claim expressed as code:

1. Retrieve and judge the evidence first. The quota is charged later, so an
   over-quota caller is told after one wasted retrieval rather than being
   billed for an answer they never saw — the cheaper mistake of the two.
2. Retrieve from **production only**. ``search`` takes no index argument, so
   staging is not reachable from here even by mistake.
3. Ask the guardrail. If it refuses, render the refusal from a template and
   **return without invoking the model at all** — cheaper, and strictly safer
   than generating and then discarding, because generation that happened
   cannot be un-happened if a later branch forgets to discard it.
4. Only then generate, under a schema constraint, and only through
   ``generate_diagnosis`` which re-checks the decision itself.
5. Persist the turn, then charge the quota — and only for an answer the
   engineer actually received. ``TenantRow.free_questions_used`` documents
   that decision explicitly: "a failed or refused answer must not burn a
   question the engineer never received." A refusal is the product declining
   to help; billing for it would charge for the one outcome the engineer
   cannot use.

Framework-agnostic — nothing here imports FastAPI.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.guardrails.cite_or_refuse import evaluate_confidence, verify_citations
from app.ai.guardrails.confidence import score_confidence
from app.ai.guardrails.refusal_text import render_refusal
from app.ai.localisation import generate_localised_diagnosis
from app.ai.prompts.diagnostic import SYSTEM_PROMPT, build_diagnostic_prompt
from app.ai.retrieval.hybrid_search import search
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.observability import record_latency, timed
from app.domain.auth import consume_free_question
from app.models.schemas.auth import CurrentUser
from app.models.schemas.diagnostics import (
    ConfidenceBreakdown,
    DiagnosticRequest,
    DiagnosticResponse,
    DiagnosticSession,
    DiagnosticTurn,
    GeneratedAnswer,
    VerifiedAnswer,
)
from app.models.schemas.guardrail import ConfidenceDecision
from app.models.schemas.responses import DiagnosisStep, Severity, StructuredDiagnosis
from app.models.schemas.search import RetrievedPassage
from app.models.schemas.streaming import DiagnosisEvent
from app.models.tables.diagnostics import DiagnosticSessionRow, DiagnosticTurnRow

# A replayed turn cites this rather than a real passage id: the citations an
# engineer saw are not stored on the row, and inventing an id that resolves to
# nothing would be worse than one that plainly says where it came from.
_REPLAY_CITATION_ID = "recorded-turn"

# Above this, a replayed answer is shown without the uncertainty banner. The
# banner is about how much to trust the answer, and a stored score is the same
# score the engineer originally saw.
_REPLAY_CONFIDENT = 0.6


logger = structlog.get_logger(__name__)


def run_diagnosis(
    *,
    session: Session,
    user: CurrentUser,
    request: DiagnosticRequest,
    charge: bool = True,
) -> DiagnosticResponse:
    """Produce one grounded diagnostic answer for a user's symptom description.

    Retrieval runs against the production index only; if the guardrail finds no
    citable passage the call refuses rather than answering unsourced.

    Args:
        session: Open database session for persisting the conversation turn.
        user: The authenticated caller.
        request: Symptom description, equipment context, and session id.
        charge: Whether to consume a free question here. ``False`` only for the
            streaming caller, which charges after the result has left for the
            client — a disconnect part-way through must not bill for an answer
            nobody received.

    Returns:
        The diagnosis with its supporting citations and confidence score.

    Raises:
        ValidationError: If the tenant has no free questions left.
            Raised by ``consume_free_question``; it maps to a 400, not a
            payment-specific status, because the free tier is a usage
            limit rather than a billing state.
        NotFoundError: If ``request.session_id`` refers to an unknown session.
    """
    # The quota is NOT charged here. See step 5 in the module docstring: only
    # an answer the engineer receives burns a question.

    conversation = _resolve_session(session=session, user=user, request=request)

    # Production only. `search` exposes no index argument, so this cannot be
    # pointed at unverified staging content by passing an argument.
    with timed("retrieval"):
        passages = search(
            request.symptom,
            brand=request.equipment.manufacturer if request.equipment else None,
            model=request.equipment.model if request.equipment else None,
        )
    # Counts and lengths, never the question or the passages themselves.
    record_latency("retrieval_result", 0.0, passages=len(passages))

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

    with timed("generation", locale=request.locale.value):
        diagnosis, decision = generate_localised_diagnosis(
            _anthropic_client(),
            model=get_settings().llm_model,
            system=SYSTEM_PROMPT,
            question=build_diagnostic_prompt(request=request, evidence=passages),
            evidence_ids={passage.id for passage in passages},
            decision=decision,
            # From the request, never a server-side default: the engineer's
            # language is theirs to state.
            locale=request.locale,
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

    # Charged here, not at the top: only an answer the engineer receives
    # burns a question, per the policy recorded on ``TenantRow``. Atomic —
    # `consume_free_question` locks the row and raises rather than reporting,
    # so concurrent requests cannot each see "allowed" and all proceed.
    if charge:
        consume_free_question(session=session, tenant_id=user.tenant_id)

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
        conversation = DiagnosticSessionRow(tenant_id=_tenant_uuid(user))
        session.add(conversation)
        session.flush()
        return conversation

    return _load_session(session=session, user=user, session_id=request.session_id)


#: Shown when a turn fails for an infrastructure reason rather than a lack of
#: evidence. Deliberately does not name the failing service or echo the
#: exception: the person reading it cannot act on "OpenSearch returned 400",
#: and an exception string on a user-facing surface is a disclosure risk. The
#: detail goes to the log, which is where whoever can fix it will look.
_STREAM_FAILURE_MESSAGE = (
    "This question could not be answered because a service it depends on is "
    "unavailable. Nothing was charged for this attempt. Please try again; if "
    "it keeps happening, report it."
)


def _stream_failure_response(request: DiagnosticRequest) -> DiagnosticResponse:
    """Build the terminal result for a turn that failed mid-stream.

    Args:
        request: The question that failed.

    Returns:
        A refusal-shaped response the frontend can render unchanged.

    Every confidence signal is zero rather than omitted, because the failure
    happened before anything was retrieved or scored. A non-zero placeholder
    would be a fabricated measurement, and the uncertainty banner is raised so
    the turn cannot read as a confident "no".
    """
    return DiagnosticResponse(
        session_id=request.session_id or "",
        confidence=ConfidenceBreakdown(
            overall=0.0,
            retrieval_score=0.0,
            passage_agreement=0.0,
            citation_density=0.0,
        ),
        low_confidence=True,
        refusal_message=_STREAM_FAILURE_MESSAGE,
    )


def stream_diagnosis(
    *,
    session: Session,
    user: CurrentUser,
    request: DiagnosticRequest,
) -> Generator[DiagnosisEvent, None, None]:
    """Produce one diagnostic turn as a sequence of progress events.

    Yields the same result ``run_diagnosis`` returns, preceded by the stages
    it passed through. The stages exist because retrieval and generation take
    seconds and an engineer watching a blank panel cannot tell a slow answer
    from a hung one.

    **The final event carries the complete response.** A client that ignores
    every progress event and reads only the last one loses nothing — the
    stages are a progress indicator, not a protocol the frontend must
    reassemble an answer from. Streaming a partially-built answer would put
    unvalidated text in front of an engineer before the guardrail had ruled
    on it, which is the opposite of what cite-or-refuse is for.

    Args:
        session: Open database session.
        user: The authenticated caller.
        request: The question.

    **Nothing raises out of this generator once streaming has begun.** A
    generator body does not run until its first item is requested, which for a
    ``StreamingResponse`` is after ``200 OK`` and the SSE headers have gone to
    the client. An exception escaping past that point cannot become a status
    code: it aborts the response body, and the client sees a connection that
    ended after ``retrieving`` with no terminal event — indistinguishable from
    a dropped network. So every failure is converted to a ``refused`` event
    followed by a ``result``, which is the shape the client already
    understands. Unknown event names are ignored by the frontend by design, so
    inventing an ``error`` event here would be *less* visible than a refusal,
    not more.

    Yields:
        Progress events, then exactly one ``result`` event — including when
        the turn fails.
    """
    yield DiagnosisEvent(event="retrieving", data={})

    try:
        response = run_diagnosis(session=session, user=user, request=request, charge=False)
    except Exception:
        # Broad because the alternative is silence. Retrieval reaches
        # OpenSearch, generation reaches Anthropic, and embedding reaches
        # Voyage; each can fail in ways this layer cannot enumerate, and every
        # one of them must still leave the engineer with a terminal event
        # rather than a stalled panel.
        logger.exception("diagnosis.stream_failed", tenant_id=user.tenant_id)
        yield DiagnosisEvent(event="refused", data={"reason": _STREAM_FAILURE_MESSAGE})
        yield DiagnosisEvent(
            event="result",
            data=_stream_failure_response(request).model_dump(mode="json"),
        )
        # Deliberately not re-raised, and the quota deliberately not charged:
        # the turn produced no answer, so billing for it is the exact failure
        # the charge-after-delivery ordering below exists to prevent.
        return

    if response.diagnosis is None:
        yield DiagnosisEvent(event="refused", data={"reason": response.refusal_message})
    else:
        yield DiagnosisEvent(event="generated", data={})

    yield DiagnosisEvent(event="result", data=response.model_dump(mode="json"))

    # Charged only once the result has actually left for the client. A
    # disconnect part-way through a stream would otherwise bill for an answer
    # nobody received — the precise thing ``TenantRow`` says must not happen,
    # and streaming makes long-lived connections the normal case rather than
    # the exception.
    #
    # `yield` above returns here only when the consumer asked for the next
    # item, which for a `StreamingResponse` means the previous frame was
    # handed to the transport. A client that vanished mid-stream never
    # resumes this generator, so the charge simply never happens.
    if response.diagnosis is not None:
        consume_free_question(session=session, tenant_id=user.tenant_id)


def _tenant_uuid(user: CurrentUser) -> uuid.UUID:
    """Return the caller's tenant as the type the column stores.

    ``CurrentUser.tenant_id`` is a string because it arrives in a JWT claim;
    every tenant-scoped column is a ``UUID``. Comparing the two forms silently
    matches nothing — a scoped query returns "not found" for rows that exist,
    which reads as correct isolation while actually being a broken filter.

    Args:
        user: The authenticated caller.

    Returns:
        The tenant id as a UUID.

    Raises:
        NotFoundError: If the claim is not a UUID. A malformed tenant claim is
            a caller problem, not a server fault, and the rows it would scope
            to do not exist by definition.
    """
    try:
        return uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc


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
            DiagnosticSessionRow.tenant_id == _tenant_uuid(user),
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
    # Lock the conversation before reading the last position. Without it two
    # concurrent turns both read the same maximum and both write position N+1,
    # and `get_session`'s ORDER BY then returns them in arbitrary order — a
    # history that silently reorders question and answer. The unique
    # constraint on (session_id, position) is the backstop if this is ever
    # bypassed; this lock is what stops it happening in the first place.
    session.execute(
        select(DiagnosticSessionRow.id)
        .where(DiagnosticSessionRow.id == conversation.id)
        .with_for_update()
    )
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
            # Recorded rather than inferred at read time: whether a turn was
            # refused is a fact about what happened, and deriving it from the
            # stored text later would guess.
            refused=response.diagnosis is None,
            confidence=response.confidence.overall,
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
    # `DiagnosticResponse` guarantees a non-blank refusal message whenever
    # there is no answer, so this cannot be empty — and an empty one would
    # make the whole session unreadable when replayed.
    return response.refusal_message or "No answer was recorded for this turn."


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
        turns=[_replay_turn(conversation.id, turn) for turn in turns],
    )


def _replay_turn(conversation_id: uuid.UUID, turn: DiagnosticTurnRow) -> DiagnosticTurn:
    """Render a stored turn as the response the engineer originally saw.

    Args:
        conversation_id: The conversation this turn belongs to.
        turn: The stored row.

    Returns:
        The turn. A turn stored as refused replays as a refusal; one stored as
        answered replays as an answer, **not** as a refusal carrying the answer
        text. Getting that backwards makes a past successful diagnosis reload
        as "the assistant declined to help", which is both wrong and alarming.

        The structured card is not reconstructed: the row keeps the prose that
        was shown, and re-deriving steps from it would invent detail the
        engineer never saw. The prose therefore comes back as the answer, with
        the citations recorded alongside it.
    """
    request = DiagnosticRequest(session_id=str(conversation_id), symptom=turn.question)
    stored_confidence = ConfidenceBreakdown(
        overall=turn.confidence,
        retrieval_score=turn.confidence,
        passage_agreement=0.0,
        citation_density=0.0,
    )

    if turn.refused:
        response = DiagnosticResponse(
            session_id=str(conversation_id),
            refusal_message=turn.answer,
            confidence=stored_confidence,
            low_confidence=True,
        )
    else:
        response = DiagnosticResponse(
            session_id=str(conversation_id),
            answer=VerifiedAnswer(text=turn.answer, citations=[]),
            # A replayed answer carries no structured card, for the reason in
            # this function's docstring. `DiagnosticResponse` requires a
            # diagnosis alongside an answer, so the card is rebuilt from the
            # single stored step: what was shown, nothing invented.
            diagnosis=_replayed_diagnosis(turn.answer),
            confidence=stored_confidence,
            low_confidence=turn.confidence < _REPLAY_CONFIDENT,
        )
    return DiagnosticTurn(request=request, response=response)


def _replayed_diagnosis(text: str) -> StructuredDiagnosis:
    """Rebuild the minimum valid card for a stored answer.

    Args:
        text: The stored answer prose.

    Returns:
        A single-step diagnosis carrying the stored text. Deliberately minimal:
        the steps an engineer originally saw are not stored, and inventing
        plausible ones would put instructions in front of them that nobody
        ever wrote.
    """
    return StructuredDiagnosis(
        summary=text,
        summary_citation_ids=[_REPLAY_CITATION_ID],
        steps=[
            DiagnosisStep(
                order=1,
                instruction=text,
                rationale="Recorded from an earlier turn; the original steps were not stored.",
                citation_ids=[_REPLAY_CITATION_ID],
                severity=Severity.INFO,
            )
        ],
        severity=Severity.INFO,
    )
