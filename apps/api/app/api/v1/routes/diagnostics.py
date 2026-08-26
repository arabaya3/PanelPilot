"""Diagnostic conversation endpoints.

Thin by contract: parse, call one domain function, return. Any change to *how*
a diagnosis is produced belongs in ``app.domain.diagnostics``.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.diagnostics import (
    DiagnosticRequest,
    DiagnosticResponse,
    DiagnosticSession,
)
from app.models.schemas.streaming import DiagnosisEvent

router = APIRouter()


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
        (event.render() for event in events),
        media_type="text/event-stream",
        # Proxies buffer by default, which defeats the point: the client would
        # receive every event at once, at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{session_id}", response_model=DiagnosticSession)
def get_diagnostic_session(
    session_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> DiagnosticSession:
    return diagnostics_domain.get_session(session=session, user=user, session_id=session_id)
