"""Panel design endpoints: design a board, export a project.

Routes never call ``app.design`` directly — they call the domain service.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile

from app.api.deps import (
    CurrentUserDep,
    SessionDep,
    enforce_design_rate_limit,
    enforce_trial_rate_limit,
)
from app.core.errors import GoneError
from app.domain import company_settings, design_ai, design_projects, model_budget
from app.domain import design as design_domain
from app.models.schemas.design import (
    AcknowledgedRequirements,
    AiDecisionRequest,
    AiProposal,
    AiProposalRequest,
    ApproveRevisionRequest,
    BoardDesignRequest,
    BoardDesignResponse,
    CompanySettings,
    ConfigurationReview,
    ConfigurationValidateRequest,
    DesignExportRequest,
    LoadScheduleImport,
    LoadScheduleSuggestion,
    MarketInfo,
    MarkupReport,
    PlcProgramRequest,
    PlcProgramResponse,
    PriceListEntry,
    ProjectDesignRequest,
    ProjectPage,
    QuestionAction,
    QuotationEstimate,
    QuotationRequest,
    RequirementsPreview,
    RequirementsPreviewRequest,
    ReviseProjectRequest,
    RevisionExportRequest,
    SafetyResolutionRequest,
    SaveCompanySettings,
    SavedProject,
    SaveProjectRequest,
    ScheduleSuggestionRequest,
)

# Every design route costs CPU (drawing sets, PDF table scans) and is open to a
# trial session, so all of them share one per-address budget.
#: The largest revision number a path or query may name: the column is a
#: Postgres ``integer``, so a larger number is a 422, not a database error.
_MAX_REVISION = 2_147_483_647

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
        headers={"Content-Disposition": _disposition(exported.filename, exported.display_name)},
    )


def _disposition(filename: str, display_name: str) -> str:
    """``attachment`` with an ASCII name, and the project's own (RFC 6266/5987)."""
    header = f'attachment; filename="{filename}"'
    if display_name and display_name != filename:
        header += f"; filename*=UTF-8''{quote(display_name, safe='')}"
    return header


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


@router.post("/quotation", response_model=QuotationEstimate)
def price_design(
    payload: QuotationRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> QuotationEstimate:
    """Price an unsaved design as a provisional estimate, with its findings.

    Never a quotation: a quotation is the ``quotation_pdf`` export of a saved
    revision (``POST /design/projects/{id}/revisions/{n}/export``).
    """
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


@router.post(
    "/projects/requirements/preview",
    response_model=RequirementsPreview,
    responses={422: {"description": "As a save refuses the request, or the design's refusal"}},
)
def preview_requirements(
    payload: RequirementsPreviewRequest, user: CurrentUserDep, session: SessionDep
) -> RequirementsPreview:
    """The requirements record and design a guided input would be saved with; nothing is saved."""
    return design_projects.preview_requirements(user=user, request=payload, session=session)


_AI_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "`ai_requires_account` (a trial)"},
    404: {"description": "`ai_proposal_not_found` (also another tenant's)"},
}


@router.post(
    "/projects/requirements/ai-proposals",
    response_model=AiProposal,
    status_code=201,
    # The design routes' per-address budget (the router's), plus the
    # per-address trial limit every paid model call carries.
    dependencies=[Depends(enforce_trial_rate_limit)],
    responses={
        403: {"description": "`ai_requires_account` (a trial)"},
        409: {"description": "`ai_request_in_flight` (the account's other call)"},
        422: {"description": "`ai_consent_required`, `ai_input_empty`, `ai_input_too_long`"},
        429: {"description": "`rate_limited`, `ai_user_limit_exceeded`, `model_budget_exceeded`"},
        502: {"description": "`ai_proposal_malformed`"},
        503: {"description": "`ai_unavailable`"},
        504: {"description": "`ai_timeout`"},
    },
)
def create_ai_proposal(
    payload: AiProposalRequest,
    user: CurrentUserDep,
    session: SessionDep,
    provider: Annotated[Any, Depends(design_ai.get_proposal_provider)],
) -> AiProposal:
    """Propose requirement entries from a description (M6b). A paid model call; designs nothing."""
    return design_ai.create_proposal(session=session, user=user, request=payload, provider=provider)


@router.get(
    "/projects/requirements/ai-proposals/{proposal_id}",
    response_model=AiProposal,
    responses=_AI_ERRORS,
)
def get_ai_proposal(
    proposal_id: Annotated[str, Path(max_length=64)], user: CurrentUserDep, session: SessionDep
) -> AiProposal:
    """One of the tenant's AI proposals, with each item's decision state."""
    return design_ai.get_proposal(session=session, user=user, proposal_id=proposal_id)


@router.delete(
    "/projects/requirements/ai-proposals/{proposal_id}",
    status_code=204,
    responses={
        **_AI_ERRORS,
        403: {"description": "`ai_requires_account`, `ai_proposal_not_yours`"},
        409: {"description": "`ai_proposal_used` (part of a saved revision's record)"},
    },
)
def delete_ai_proposal(
    proposal_id: Annotated[str, Path(max_length=64)], user: CurrentUserDep, session: SessionDep
) -> Response:
    """Delete one of the caller's own proposals that no saved revision applied (S3)."""
    design_ai.delete_proposal(session=session, user=user, proposal_id=proposal_id)
    return Response(status_code=204)


@router.post(
    "/projects/requirements/ai-proposals/{proposal_id}/items/{item_id}/decision",
    response_model=AiProposal,
    responses={
        **_AI_ERRORS,
        409: {"description": "`ai_proposal_used`"},
        422: {"description": "`ai_edit_not_valid`, `ai_decisions_full`, or a who/when field"},
    },
)
def decide_ai_item(
    proposal_id: Annotated[str, Path(max_length=64)],
    item_id: Annotated[str, Path(max_length=40)],
    payload: AiDecisionRequest,
    user: CurrentUserDep,
    session: SessionDep,
) -> AiProposal:
    """Accept, reject or edit one proposal item; append-only, who and when the server's."""
    return design_ai.decide(
        session=session, user=user, proposal_id=proposal_id, item_id=item_id, decision=payload
    )


@router.post(
    "/projects/{project_id}/revisions/{number}/questions/{question_id}",
    response_model=SavedProject | AcknowledgedRequirements,
    status_code=201,
    responses={
        200: {
            "description": "Acknowledged: the revision's updated record",
            "model": AcknowledgedRequirements,
        },
        201: {"description": "Answered: the project at its new revision", "model": SavedProject},
        404: {"description": "`revision_not_found` or `question_not_found`"},
        409: {
            "description": (
                "`requirements_derived`, `revision_already_approved`, `revision_not_latest`, "
                "`question_not_answerable`, `question_answered`, `snapshot_integrity_failed`"
            )
        },
    },
)
def answer_question(
    project_id: str,
    number: Annotated[int, Path(ge=1, le=_MAX_REVISION)],
    question_id: Annotated[str, Path(max_length=40)],
    payload: QuestionAction,
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
) -> SavedProject | AcknowledgedRequirements:
    """Answer a question (201, a new revision) or acknowledge it (200, this revision's record)."""
    result = design_projects.answer_question(
        session=session,
        user=user,
        project_id=project_id,
        number=number,
        question_id=question_id,
        action=payload,
    )
    if isinstance(result, AcknowledgedRequirements):
        response.status_code = 200
    return result


@router.post(
    "/projects/{project_id}/revisions/{number}/requirements/safety-clarifications",
    status_code=410,
    responses={410: {"description": "`clarification_route_retired`"}},
)
def clarify_safety_mention(
    project_id: str,  # noqa: ARG001
    number: int,  # noqa: ARG001
    user: CurrentUserDep,  # noqa: ARG001  (a signed-in caller only)
) -> None:
    """Retired (M6e): use ``safety-resolutions``. Stored clarifications keep printing."""
    raise GoneError(
        "this route is retired; record a resolution with POST …/requirements/safety-resolutions",
        code="clarification_route_retired",
    )


@router.post(
    "/projects/{project_id}/revisions/{number}/requirements/safety-resolutions",
    response_model=AcknowledgedRequirements,
    responses={
        403: {
            "description": (
                "`safety_resolution_requires_account`, `safety_resolution_not_permitted`, "
                "`safety_resolution_by_proposal_author`, `safety_resolution_by_revision_author`"
            )
        },
        404: {"description": "`revision_not_found` or `safety_target_not_found`"},
        409: {
            "description": (
                "`requirements_derived`, `revision_already_approved`, `revision_not_latest`, "
                "`safety_concern_already_resolved`, `false_positive_not_allowed`, "
                "`snapshot_integrity_failed`"
            )
        },
    },
)
def resolve_safety_concern(
    project_id: str,
    number: Annotated[int, Path(ge=1, le=_MAX_REVISION)],
    payload: SafetyResolutionRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> AcknowledgedRequirements:
    """Resolve one safety concern as excluded from scope or a false positive: four-eyes, append-only."""
    return design_projects.resolve_safety_concern(
        session=session, user=user, project_id=project_id, number=number, request=payload
    )


@router.get("/projects/{project_id}", response_model=SavedProject)
def open_project(
    project_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    revision: Annotated[int | None, Query(ge=1, le=_MAX_REVISION)] = None,
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


@router.post(
    "/projects/{project_id}/configuration/validate",
    response_model=ConfigurationReview,
    responses={
        404: {"description": "No such project is the caller's (`project_not_found`)"},
        409: {"description": "`revision_not_validated`"},
        422: {
            "description": (
                "`configuration_too_large`, `configuration_claims_review`, "
                "`configuration_ref_reserved` or `configuration_refused`"
            )
        },
    },
)
def validate_configuration(
    project_id: str,
    payload: ConfigurationValidateRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> ConfigurationReview:
    """Compose a configuration with the latest revision, designed now; nothing is saved."""
    return design_projects.validate_configuration(
        session=session, user=user, project_id=project_id, request=payload
    )


@router.post(
    "/projects/{project_id}/revisions/{number}/resave",
    response_model=SavedProject,
    status_code=201,
    responses={
        403: {"description": "`resave_requires_account`"},
        404: {"description": "No such revision is the caller's (`revision_not_found`)"},
        409: {
            "description": (
                "`revision_not_validated`, `snapshot_integrity_failed` or `revision_conflict`"
            )
        },
        422: {
            "description": (
                "As a revision save refuses the stored request, unchanged "
                "(e.g. `feeder_server_owned`, `sub_board_load_server_owned`)"
            )
        },
    },
)
def resave_revision(
    project_id: str,
    number: Annotated[int, Path(ge=1, le=_MAX_REVISION)],
    session: SessionDep,
    user: CurrentUserDep,
) -> SavedProject:
    """Save a revision's stored request again as the caller's new revision, for independent review."""
    return design_projects.resave_revision(
        session=session, user=user, project_id=project_id, number=number
    )


@router.post("/projects/{project_id}/revisions/{number}/approval", response_model=SavedProject)
def approve_revision(
    project_id: str,
    number: Annotated[int, Path(ge=1, le=_MAX_REVISION)],
    payload: ApproveRevisionRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> SavedProject:
    """Approve one revision of a saved project, once; it then cannot be deleted."""
    return design_projects.approve_revision(
        session=session, user=user, project_id=project_id, number=number, request=payload
    )


@router.post(
    "/projects/{project_id}/revisions/{number}/export",
    response_class=Response,
    responses={
        200: {"description": "The exported file", "content": {"application/octet-stream": {}}},
        404: {"description": "No such revision is the caller's (`revision_not_found`)"},
        409: {
            "description": (
                "`revision_not_validated`, `snapshot_integrity_failed`, `snapshot_schema_unknown`, "
                "`revision_stale`, `export_format_unprotected`, `schedule_requires_v2`, "
                "or `execution_package_incomplete`"
            )
        },
    },
)
def export_revision(
    project_id: str,
    number: Annotated[int, Path(ge=1, le=_MAX_REVISION)],
    payload: RevisionExportRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> Response:
    """Export a saved revision from the design the server stored and checked for it."""
    exported = design_projects.export_revision(
        session=session, user=user, project_id=project_id, number=number, request=payload
    )
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={"Content-Disposition": _disposition(exported.filename, exported.display_name)},
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Delete a saved project and all its revisions."""
    design_projects.delete_project(session=session, user=user, project_id=project_id)
    return Response(status_code=204)


@router.get("/markets", response_model=list[MarketInfo])
def list_markets() -> list[MarketInfo]:
    """Every market and its supply defaults; public, for a new visitor's board."""
    return design_domain.list_markets()


@router.get("/company-settings", response_model=CompanySettings)
def get_company_settings(session: SessionDep, user: CurrentUserDep) -> CompanySettings:
    """The caller's company profile settings, as saved."""
    return company_settings.get_settings(session=session, user=user)


@router.put("/company-settings", response_model=CompanySettings)
def save_company_settings(
    payload: SaveCompanySettings, session: SessionDep, user: CurrentUserDep
) -> CompanySettings:
    """Save the caller's company profile settings, refused if a design would refuse them."""
    return company_settings.save_settings(session=session, user=user, settings=payload.settings)


@router.post("/markups", response_model=MarkupReport)
async def read_markups(
    user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
    project: Annotated[str | None, Form()] = None,
    profile: Annotated[str | None, Form()] = None,
) -> MarkupReport:
    """Read a reviewer's marks off a drawing set PDF, placed on this project's drawings."""
    data = await file.read(design_domain.MAX_MARKUP_BYTES + 1)
    return design_domain.read_markups(
        user=user, data=data, project_json=project, profile_json=profile
    )
