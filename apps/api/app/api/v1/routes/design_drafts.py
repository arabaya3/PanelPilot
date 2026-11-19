"""Server-backed design drafts (slice 1): the author's own unsaved work on a saved project.

On their own rate budget (``design-drafts``). Every route binds the tenant
and the caller as author; see :mod:`app.domain.design_drafts`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, SessionDep, enforce_draft_rate_limit
from app.domain import design_drafts
from app.models.schemas.design_drafts import (
    DraftDelete,
    DraftList,
    DraftLookup,
    DraftState,
    DraftWrite,
)

router = APIRouter(dependencies=[Depends(enforce_draft_rate_limit)])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "Writes are off (server_drafts_disabled), or no account (trial)"},
    404: {"description": "No such project, or not the caller's"},
    409: {"description": "draft_conflict (nothing changed) or request_expired"},
    422: {"description": "request_id_reused, draft_too_large, or a malformed body"},
}


@router.get("/drafts", response_model=DraftList)
def list_drafts(session: SessionDep, user: CurrentUserDep) -> DraftList:
    """The caller's own live drafts (no bodies), and whether writes are on."""
    return design_drafts.list_drafts(session=session, user=user)


@router.get("/projects/{project_id}/draft", response_model=DraftLookup, responses=_RESPONSES)
def get_draft(project_id: str, session: SessionDep, user: CurrentUserDep) -> DraftLookup:
    """The caller's own draft of a saved project, if any (a tombstone has no body)."""
    return design_drafts.get_draft(session=session, user=user, project_id=project_id)


@router.put("/projects/{project_id}/draft", response_model=DraftState, responses=_RESPONSES)
def put_draft(
    project_id: str, payload: DraftWrite, session: SessionDep, user: CurrentUserDep
) -> DraftState:
    """Keep the caller's draft when ``if_match`` is the current token (or ``None`` for none)."""
    return design_drafts.put_draft(session=session, user=user, project_id=project_id, write=payload)


@router.delete("/projects/{project_id}/draft", response_model=DraftState, responses=_RESPONSES)
def delete_draft(
    project_id: str, payload: DraftDelete, session: SessionDep, user: CurrentUserDep
) -> DraftState:
    """Delete the caller's draft when ``if_match`` is its current token (leaves a tombstone)."""
    return design_drafts.delete_draft(
        session=session, user=user, project_id=project_id, request=payload
    )
