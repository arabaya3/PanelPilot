"""Panel design: design a board, and export a project in any format.

Routes call these; these call ``app.design``. Nothing is persisted: a project
is returned to the caller, who sends it back to export it, so an engineer's
edits between the two are what gets exported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.ai import schedule_writer
from app.design import (
    designations,
    distribution,
    export_aml,
    export_dxf,
    export_lists,
    export_qet,
    pages,
    profile,
    render_pdf,
    schedule_import,
    schedule_split,
)
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    BoardDesignRequest,
    BoardDesignResponse,
    CompanyProfile,
    DesignExportRequest,
    DesignProject,
    ExportFormat,
    LoadScheduleImport,
    LoadScheduleSuggestion,
    ScheduleSuggestionRequest,
)

logger = structlog.get_logger(__name__)

#: The largest load schedule file read.
MAX_SCHEDULE_BYTES = schedule_import.MAX_SCHEDULE_BYTES

_MEDIA_TYPES: dict[ExportFormat, tuple[str, str]] = {
    ExportFormat.PDF: ("application/pdf", "pdf"),
    ExportFormat.DXF: ("application/dxf", "dxf"),
    ExportFormat.QET: ("application/xml", "qet"),
    ExportFormat.AML: ("application/xml", "aml"),
    ExportFormat.DEVICES_CSV: ("text/csv; charset=utf-8", "devices.csv"),
    ExportFormat.PARTS_CSV: ("text/csv; charset=utf-8", "parts.csv"),
    ExportFormat.CABLES_CSV: ("text/csv; charset=utf-8", "cables.csv"),
    ExportFormat.CIRCUITS_CSV: ("text/csv; charset=utf-8", "circuits.csv"),
    ExportFormat.JSON: ("application/json", "json"),
}


@dataclass(frozen=True)
class ExportedFile:
    """A file ready to send.

    Attributes:
        content: Its bytes.
        media_type: Its MIME type.
        filename: The name to save it under.
    """

    content: bytes
    media_type: str
    filename: str


def _profile(settings: dict[str, Any] | None) -> CompanyProfile:
    if not settings:
        return profile.default_profile()
    return profile.load_profile(settings)


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return slug or "project"


def design_board(
    *, session: Session, user: CurrentUser, request: BoardDesignRequest
) -> BoardDesignResponse:
    """Design a distribution board and issue it under the company's profile.

    Args:
        session: Open database session. Unused: nothing is persisted.
        user: The authenticated caller, for the log line.
        request: Title-block data, the load schedule, and the profile settings.

    Returns:
        The designated project and the profile applied.

    Raises:
        ValidationError: If the profile is malformed or a load cannot be
            protected or cabled from the tables held.
    """
    del session
    company = _profile(request.profile)
    board = distribution.design_distribution_board(request.board, company)
    project = designations.designate_project(
        DesignProject(info=request.info, boards=[board]), company
    )
    logger.info(
        "design.board_designed",
        tenant_id=user.tenant_id,
        circuits=len(board.circuits),
        profile=company.key,
    )
    return BoardDesignResponse(project=project, profile=company)


def export_design(
    *, session: Session, user: CurrentUser, request: DesignExportRequest
) -> ExportedFile:
    """Export a project, designated afresh under the company's profile.

    Designations are reassigned on every export, so a circuit an engineer
    added or removed since the design renumbers cleanly.

    Args:
        session: Open database session. Unused: nothing is persisted.
        user: The authenticated caller, for the log line.
        request: The project, the profile settings and the format.

    Returns:
        The file.

    Raises:
        ValidationError: If the profile is malformed.
    """
    del session
    company = _profile(request.profile)
    project = designations.designate_project(request.project, company)
    content: bytes
    if request.format is ExportFormat.PDF:
        sheets = pages.build_drawing_set(project, company)
        content = render_pdf.render_pdf(sheets, title=project.info.name, author=company.name)
    elif request.format is ExportFormat.DXF:
        content = export_dxf.export_dxf(pages.build_drawing_set(project, company))
    elif request.format is ExportFormat.QET:
        content = export_qet.export_qet(project)
    elif request.format is ExportFormat.AML:
        content = export_aml.export_aml(project)
    elif request.format is ExportFormat.DEVICES_CSV:
        content = export_lists.device_list(project).encode("utf-8")
    elif request.format is ExportFormat.PARTS_CSV:
        content = export_lists.parts_list(project).encode("utf-8")
    elif request.format is ExportFormat.CABLES_CSV:
        content = export_lists.cable_list(project).encode("utf-8")
    elif request.format is ExportFormat.CIRCUITS_CSV:
        content = export_lists.circuit_schedule(project).encode("utf-8")
    else:
        content = project.model_dump_json(indent=2).encode("utf-8")
    media_type, extension = _MEDIA_TYPES[request.format]
    logger.info(
        "design.exported",
        tenant_id=user.tenant_id,
        format=request.format.value,
        bytes=len(content),
    )
    return ExportedFile(
        content=content,
        media_type=media_type,
        filename=f"{_slug(project.info.name)}.{extension}",
    )


def import_load_schedule(*, user: CurrentUser, data: bytes) -> LoadScheduleImport:
    """Read a consultant's load schedule file into loads.

    Args:
        user: The authenticated caller, for the log line.
        data: The file's bytes (.xlsx, .csv or .pdf).

    Returns:
        The loads read, with every row skipped and every assumption made.

    Raises:
        ValidationError: If the file cannot be read or has no usable table.
    """
    result = schedule_import.import_schedule(data)
    logger.info(
        "design.schedule_imported",
        tenant_id=user.tenant_id,
        loads=len(result.loads),
        warnings=len(result.warnings),
    )
    return LoadScheduleImport(
        loads=result.loads, warnings=result.warnings, rows_read=result.rows_read
    )


def suggest_load_schedule(
    *, user: CurrentUser, request: ScheduleSuggestionRequest
) -> LoadScheduleSuggestion:
    """Draft a load schedule from a plain description, for the engineer to check.

    The model reads the points out of the description; the split into
    circuits follows the company's rule, so it is the same every time.

    Args:
        user: The authenticated caller, for the log line.
        request: The description, the supply and the company's settings.

    Returns:
        The proposed loads, and every assumption behind them: overall first,
        then one per circuit.

    Raises:
        ValidationError: If nothing usable came back, or the profile is bad.
        ServiceUnavailableError: If the model could not be reached.
    """
    company = _profile(request.profile)
    drafted = schedule_writer.write_schedule(request)
    loads, notes = schedule_split.split_points(
        drafted.items, company, supply_phases=request.supply_phases
    )
    logger.info("design.schedule_suggested", tenant_id=user.tenant_id, loads=len(loads))
    return LoadScheduleSuggestion(loads=loads, assumptions=[*drafted.assumptions, *notes])
