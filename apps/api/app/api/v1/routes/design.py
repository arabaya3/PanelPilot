"""Panel design endpoints: design a board, export a project.

Routes never call ``app.design`` directly — they call the domain service.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import design as design_domain
from app.models.schemas.design import (
    BoardDesignRequest,
    BoardDesignResponse,
    DesignExportRequest,
)

router = APIRouter()


@router.post("/distribution-board", response_model=BoardDesignResponse)
def design_distribution_board(
    payload: BoardDesignRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BoardDesignResponse:
    return design_domain.design_board(session=session, user=user, request=payload)


@router.post(
    "/export",
    response_class=Response,
    responses={
        200: {"description": "The exported file", "content": {"application/octet-stream": {}}}
    },
)
def export_design(
    payload: DesignExportRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> Response:
    exported = design_domain.export_design(session=session, user=user, request=payload)
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )
