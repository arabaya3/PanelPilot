"""Panel design endpoints: design a board, export a project.

Routes never call ``app.design`` directly — they call the domain service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.api.deps import (
    CurrentUserDep,
    SessionDep,
    enforce_design_rate_limit,
    enforce_trial_rate_limit,
)
from app.domain import design as design_domain
from app.domain import design_projects, model_budget
from app.models.schemas.design import (
    BoardDesignRequest,
    BoardDesignResponse,
    DesignExportRequest,
    LoadScheduleImport,
    LoadScheduleSuggestion,
    PlcProgramRequest,
    PlcProgramResponse,
    PriceListEntry,
    ProjectDesignRequest,
    ProjectPage,
    Quotation,
    QuotationRequest,
    ReviseProjectRequest,
    SavedProject,
    SaveProjectRequest,
    ScheduleSuggestionRequest,
)

# Every design route costs CPU (drawing sets, PDF table scans) and is open to a
# trial session, so all of them share one per-address budget.
router = APIRouter(dependencies=[Depends(enforce_design_rate_limit)])


@router.post("/distribution-board", response_model=BoardDesignResponse)
def design_distribution_board(
    payload: BoardDesignRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BoardDesignResponse:
    return design_domain.design_board(session=session, user=user, request=payload)


@router.post("/project", response_model=BoardDesignResponse)
def design_project(
    payload: ProjectDesignRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BoardDesignResponse:
    """Design a project of boards, each sub-board's feeder sized from its design."""
    return design_domain.design_project(session=session, user=user, request=payload)


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


@router.post(
    "/load-schedule/suggest",
    response_model=LoadScheduleSuggestion,
    dependencies=[Depends(enforce_trial_rate_limit)],
)
def suggest_load_schedule(
    payload: ScheduleSuggestionRequest,
    user: CurrentUserDep,
    session: SessionDep,
) -> LoadScheduleSuggestion:
    """Draft a load schedule from a plain description. Each call is a paid model request."""
    model_budget.charge_model_call(session=session, tenant_id=user.tenant_id)
    result = design_domain.suggest_load_schedule(user=user, request=payload)
    session.commit()
    return result


@router.post("/quotation", response_model=Quotation)
def price_design(
    payload: QuotationRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> Quotation:
    del session
    return design_domain.price_design(user=user, request=payload)


@router.post("/price-list/import", response_model=list[PriceListEntry])
async def import_price_list(
    user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
) -> list[PriceListEntry]:
    """Read a company's price list (.xlsx or .csv)."""
    data = await file.read(design_domain.MAX_SCHEDULE_BYTES + 1)
    return design_domain.import_price_list(user=user, data=data)


@router.post("/plc", response_model=PlcProgramResponse)
def write_plc_program(
    payload: PlcProgramRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> PlcProgramResponse:
    """Write and check the control program for the PLC-switched circuits."""
    del session
    return design_domain.write_plc_program(user=user, request=payload)


@router.get("/projects", response_model=ProjectPage)
def list_projects(
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> ProjectPage:
    """List the caller's saved projects, most recently saved first."""
    return design_projects.list_projects(session=session, user=user, limit=limit, cursor=cursor)


@router.post("/projects", response_model=SavedProject, status_code=201)
def save_project(
    payload: SaveProjectRequest, session: SessionDep, user: CurrentUserDep
) -> SavedProject:
    """Save a new project as its first revision."""
    return design_projects.save_project(session=session, user=user, request=payload)


@router.get("/projects/{project_id}", response_model=SavedProject)
def open_project(
    project_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    revision: Annotated[int | None, Query(ge=1)] = None,
) -> SavedProject:
    """Open a saved project at its latest revision, or the one asked for."""
    return design_projects.open_project(
        session=session, user=user, project_id=project_id, revision=revision
    )


@router.post("/projects/{project_id}/revisions", response_model=SavedProject, status_code=201)
def revise_project(
    project_id: str, payload: ReviseProjectRequest, session: SessionDep, user: CurrentUserDep
) -> SavedProject:
    """Save a new revision of a project."""
    return design_projects.revise_project(
        session=session, user=user, project_id=project_id, request=payload
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Delete a saved project and all its revisions."""
    design_projects.delete_project(session=session, user=user, project_id=project_id)
    return Response(status_code=204)
