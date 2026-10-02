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
from app.ai.plc.validation import validate_plc_code
from app.core.errors import ValidationError
from app.design import (
    designations,
    distribution,
    export_aml,
    export_dxf,
    export_lists,
    export_qet,
    pages,
    plc_program,
    profile,
    quotation,
    quotation_pdf,
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
    PlcIoPoint,
    PlcProgramRequest,
    PlcProgramResponse,
    PriceListEntry,
    Quotation,
    QuotationRequest,
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
    ExportFormat.QUOTATION_PDF: ("application/pdf", "quotation.pdf"),
    ExportFormat.QUOTATION_CSV: ("text/csv; charset=utf-8", "quotation.csv"),
    ExportFormat.PLC_ST: ("text/plain; charset=utf-8", "st"),
    ExportFormat.PLC_IO_CSV: ("text/csv; charset=utf-8", "io.csv"),
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
        ValidationError: If the profile is malformed, a quotation is asked
            for without pricing settings, or a control program for a project
            with no PLC-switched circuit.
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
    elif request.format in (ExportFormat.QUOTATION_PDF, ExportFormat.QUOTATION_CSV):
        if request.pricing is None:
            raise ValidationError("a quotation needs the company's pricing settings")
        priced = quotation.price_project(project, request.pricing)
        content = (
            quotation_pdf.render_quotation_pdf(priced, project, company=company.name)
            if request.format is ExportFormat.QUOTATION_PDF
            else quotation.quotation_csv(priced).encode("utf-8")
        )
    elif request.format in (ExportFormat.PLC_ST, ExportFormat.PLC_IO_CSV):
        program = _program(project)
        content = (
            program.source.encode("utf-8")
            if request.format is ExportFormat.PLC_ST
            else plc_program.io_list_csv(program).encode("utf-8")
        )
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


def price_design(*, user: CurrentUser, request: QuotationRequest) -> Quotation:
    """Price a project from the company's price list and rates.

    Args:
        user: The authenticated caller, for the log line.
        request: The project, the profile settings and the pricing.

    Returns:
        The quotation, every unpriced line named.

    Raises:
        ValidationError: If the profile is malformed.
    """
    company = _profile(request.profile)
    project = designations.designate_project(request.project, company)
    result = quotation.price_project(project, request.pricing)
    logger.info(
        "design.priced",
        tenant_id=user.tenant_id,
        lines=len(result.lines),
        unpriced=len(result.unpriced),
    )
    return result


def import_price_list(*, user: CurrentUser, data: bytes) -> list[PriceListEntry]:
    """Read a company's price list from a spreadsheet.

    Args:
        user: The authenticated caller, for the log line.
        data: The file's bytes (.xlsx or .csv).

    Returns:
        The prices.

    Raises:
        ValidationError: If the file has no key and price columns.
    """
    entries = quotation.read_price_list(data)
    logger.info("design.price_list_imported", tenant_id=user.tenant_id, entries=len(entries))
    return entries


def _program(project: DesignProject) -> plc_program.PlcProgram:
    program = plc_program.build_program(project)
    if program is None:
        raise ValidationError(
            "no circuit is PLC-switched: mark the loads the PLC controls, then design again"
        )
    return program


def write_plc_program(*, user: CurrentUser, request: PlcProgramRequest) -> PlcProgramResponse:
    """Write the control program for a project's PLC-switched circuits, and check it.

    Args:
        user: The authenticated caller, for the log line.
        request: The project and the profile settings.

    Returns:
        The Structured Text, its I/O list and the checker's verdict.

    Raises:
        ValidationError: If the profile is malformed or no circuit is
            PLC-switched.
    """
    company = _profile(request.profile)
    project = designations.designate_project(request.project, company)
    program = _program(project)
    verdict = validate_plc_code(program.source)
    logger.info(
        "design.plc_written",
        tenant_id=user.tenant_id,
        outputs=sum(point.direction == "output" for point in program.io),
        status=verdict.status.value,
    )
    return PlcProgramResponse(
        name=program.name,
        source=program.source,
        io=[
            PlcIoPoint(
                tag=point.tag,
                direction=point.direction,
                board=point.board,
                device=point.device,
                description=point.description,
            )
            for point in program.io
        ],
        validation=verdict,
    )
