"""Panel design endpoints: design a board, export a project.

Routes never call ``app.design`` directly — they call the domain service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import design as design_domain
from app.models.schemas.design import (
    BoardDesignRequest,
    BoardDesignResponse,
    DesignExportRequest,
    LoadScheduleImport,
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


@router.post("/load-schedule/import", response_model=LoadScheduleImport)
async def import_load_schedule(
    user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
) -> LoadScheduleImport:
    """Read a consultant's load schedule (.xlsx, .csv or .pdf) into loads."""
    # A ceiling on the read; the domain refuses anything over its limit.
    data = await file.read(design_domain.MAX_SCHEDULE_BYTES + 1)
    return design_domain.import_load_schedule(user=user, data=data)
