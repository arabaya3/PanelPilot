"""Engineering calculation endpoints (cable sizing, VFD selection, panel BOM).

Routes never call ``app.ai.tools`` directly — they call the domain service,
which owns input validation, unit handling, and audit logging.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import calculations as calculations_domain
from app.models.schemas.calculations import (
    CableSizingRequest,
    CableSizingResponse,
    PanelBomRequest,
    PanelBomResponse,
    VfdSelectionRequest,
    VfdSelectionResponse,
)

router = APIRouter()


@router.post("/cable-sizing", response_model=CableSizingResponse)
def size_cable(
    payload: CableSizingRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> CableSizingResponse:
    return calculations_domain.size_cable(session=session, user=user, request=payload)


@router.post("/vfd-selection", response_model=VfdSelectionResponse)
def select_vfd(
    payload: VfdSelectionRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> VfdSelectionResponse:
    return calculations_domain.select_vfd(session=session, user=user, request=payload)


@router.post("/panel-bom", response_model=PanelBomResponse)
def build_panel_bom(
    payload: PanelBomRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> PanelBomResponse:
    return calculations_domain.build_panel_bom(session=session, user=user, request=payload)
