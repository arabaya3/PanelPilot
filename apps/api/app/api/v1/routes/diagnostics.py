"""Diagnostic conversation endpoints.

Thin by contract: parse, call one domain function, return. Any change to *how*
a diagnosis is produced belongs in ``app.domain.diagnostics``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUserDep, SessionDep
from app.core.observability import StreamTimer
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.diagnostics import (
    DiagnosticRequest,
    DiagnosticResponse,
    DiagnosticSession,
    DiagnosticSessionPage,
)
from app.models.schemas.streaming import DiagnosisEvent

router = APIRouter()

#: Mounted at `/sessions` rather than under `/diagnostics`, and deliberately
#: outside that prefix's trial rate limit. That limit exists because asking a
#: question costs a model call; listing conversations the caller already owns
#: costs one indexed query, and throttling it would make an engineer's own
#: sidebar fail to load while they were reading it.
sessions_router = APIRouter()


@router.post("", response_model=DiagnosticResponse)
def create_diagnosis(
    payload: DiagnosticRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> DiagnosticResponse:
    return diagnostics_domain.run_diagnosis(session=session, user=user, request=payload)


@router.post(
    "/stream",
    # Documented explicitly: without this OpenAPI records an empty JSON schema
    # for an endpoint that emits `text/event-stream`, so the generated
    # frontend type for the payload this whole feature delivers would be
    # `unknown`. `DiagnosisEvent` is named so it reaches components.schemas.
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "A stream of server-sent events. Progress events report the "
                "stage; the final `result` event carries the complete "
                "DiagnosticResponse."
            ),
            "content": {"text/event-stream": {"schema": DiagnosisEvent.model_json_schema()}},
        }
    },
)
def stream_diagnosis(
    payload: DiagnosticRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> StreamingResponse:
    """Stream one diagnostic turn as server-sent events.

    The final ``result`` event carries the complete response; the events
    before it report progress only. See ``app.models.schemas.streaming`` for
    why no partial answer is ever streamed.
    """
    events = diagnostics_domain.stream_diagnosis(session=session, user=user, request=payload)
    return StreamingResponse(
        _timed_frames(events),
        media_type="text/event-stream",
        # Proxies buffer by default, which defeats the point: the client would
        # receive every event at once, at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _timed_frames(events: Iterable[DiagnosisEvent]) -> Iterator[str]:
    """Render events to the wire, measuring time-to-first-token.

    Args:
        events: The turn's events.

    Yields:
        SSE frames.

        Timed here rather than in the domain because this is where a frame
        actually reaches the transport — the number that matters is when the
        engineer stops looking at nothing, and measuring it one layer up would
        record when we decided to send rather than when we sent.

        A stream abandoned part-way still records: a client that disconnected
        after four seconds of nothing is the most interesting latency sample
        there is, and a timer that only fires on success loses exactly those.
    """
    timer = StreamTimer("diagnosis")
    failed = False
    try:
        for event in events:
            frame = event.render()
            timer.mark_event()
            yield frame
    except BaseException:
        failed = True
        raise
    finally:
        timer.finish(failed=failed)


@router.get("/{session_id}", response_model=DiagnosticSession)
def get_diagnostic_session(
    session_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> DiagnosticSession:
    return diagnostics_domain.get_session(session=session, user=user, session_id=session_id)


@sessions_router.get("", response_model=DiagnosticSessionPage)
def list_diagnostic_sessions(
    session: SessionDep,
    user: CurrentUserDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> DiagnosticSessionPage:
    """List the caller's conversations, most recently active first.

    `limit` is bounded here as well as in the domain so an out-of-range value
    is a 422 naming the field, rather than being silently clamped to something
    the caller did not ask for.
    """
    return diagnostics_domain.list_sessions(session=session, user=user, limit=limit, cursor=cursor)
