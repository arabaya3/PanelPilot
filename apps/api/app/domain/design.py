"""Panel design: design a board, and export a project in any format.

Routes call these; these call ``app.design``. Nothing is persisted: a project
is returned to the caller, who sends it back to export it, so an engineer's
edits between the two are what gets exported.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.ai import schedule_writer
from app.ai.plc.validation import validate_plc_code
from app.core.errors import ValidationError
from app.design import (
    calc_report,
    designations,
    export_aml,
    export_dxf,
    export_lists,
    export_qet,
    markup_suggestions,
    markups,
    motors,
    pages,
    plc_program,
    profile,
    project,
    quotation,
    quotation_pdf,
    render_pdf,
    schedule_import,
    schedule_split,
)
from app.design.notes import note
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
    MarkupItem,
    MarkupReport,
    MarkupSuggestion,
    PlcIoPoint,
    PlcProgramRequest,
    PlcProgramResponse,
    PriceListEntry,
    ProjectDesignRequest,
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
    ExportFormat.TERMINALS_CSV: ("text/csv; charset=utf-8", "terminals.csv"),
    ExportFormat.QUOTATION_PDF: ("application/pdf", "quotation.pdf"),
    ExportFormat.CALCULATIONS_PDF: ("application/pdf", "calculations.pdf"),
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
        filename: The name to save it under, in ASCII, for a client that
            reads no other.
        display_name: The same name in the project's own script, which a
            client that reads RFC 6266 ``filename*`` saves it under.
    """

    content: bytes
    media_type: str
    filename: str
    display_name: str = ""


def _profile(settings: dict[str, Any] | None) -> CompanyProfile:
    if not settings:
        return profile.default_profile()
    return profile.load_profile(settings)


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return slug or "project"


def _unicode_slug(name: str) -> str:
    """A project name as a file name, its own script kept ("برج-القمة").

    Letters, marks (an Arabic shadda) and digits of any script stay, as do
    "." and "-"; anything else (spaces, path separators, quotes, control
    characters) becomes one hyphen.
    """
    kept = "".join(
        char if unicodedata.category(char)[0] in "LMN" or char in ".-" else " " for char in name
    )
    slug = "-".join(kept.split()).strip("-.")
    return slug[:100] or "project"


def design_board(
    *, session: Session, user: CurrentUser, request: BoardDesignRequest
) -> BoardDesignResponse:
    """Design a single distribution board and issue it under the company's profile.

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
    return design_project(
        session=session,
        user=user,
        request=ProjectDesignRequest(
            info=request.info, boards=[request.board], profile=request.profile
        ),
    )


def design_project(
    *, session: Session, user: CurrentUser, request: ProjectDesignRequest
) -> BoardDesignResponse:
    """Design every board in a project and issue it under the company's profile.

    Args:
        session: Open database session. Unused: nothing is persisted.
        user: The authenticated caller, for the log line.
        request: Title-block data, each board's schedule, and the profile.

    Returns:
        The designated project and the profile applied.

    Raises:
        ValidationError: If the profile is malformed, the boards' feeding is
            inconsistent, or a load cannot be protected or cabled.
    """
    del session
    company = _profile(request.profile)
    boards = project.design_boards(request.boards, company)
    designed = designations.designate_project(
        DesignProject(info=request.info, boards=boards, parts=motors.parts_for(boards)), company
    )
    logger.info(
        "design.project_designed",
        tenant_id=user.tenant_id,
        boards=len(boards),
        circuits=sum(len(board.circuits) for board in boards),
        profile=company.key,
    )
    return BoardDesignResponse(project=designed, profile=company)


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
    elif request.format is ExportFormat.TERMINALS_CSV:
        content = export_lists.terminal_list(project).encode("utf-8")
    elif request.format is ExportFormat.CALCULATIONS_PDF:
        content = calc_report.render_calculations_pdf(project, company)
    elif request.format in (ExportFormat.QUOTATION_PDF, ExportFormat.QUOTATION_CSV):
        if request.pricing is None:
            raise ValidationError(
                "a quotation needs the company's pricing settings", code="quotation_needs_pricing"
            )
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
        display_name=f"{_unicode_slug(project.info.name)}.{extension}",
    )


#: The largest reviewed drawing set read for markups.
MAX_MARKUP_BYTES = 5 * 1024 * 1024


def read_markups(
    *,
    user: CurrentUser,
    data: bytes,
    project_json: str | None = None,
    profile_json: str | None = None,
) -> MarkupReport:
    """Read a reviewer's marks off a drawing set PDF.

    Args:
        user: The authenticated caller, for the log line.
        data: The PDF's bytes.
        project_json: This project as designed, to place each mark on its
            board and nearest label; ``None`` to read the marks alone.
        profile_json: The company settings the drawing set was issued under,
            as JSON, so its pages are laid out as the PDF's were.

    Returns:
        The marks, and whether the PDF matched the project's drawing set.

    Raises:
        ValidationError: If the file is too large or not a readable PDF, the
            project is malformed, or the profile is.
    """
    if len(data) > MAX_MARKUP_BYTES:
        raise ValidationError(
            "the PDF is larger than the 5 MB read for markups", code="markups_too_large"
        )
    sheets = None
    designated: DesignProject | None = None
    if project_json:
        try:
            designed = DesignProject.model_validate_json(project_json)
        except PydanticValidationError as exc:
            raise ValidationError("the project sent is not one this service issued") from exc
        try:
            settings = json.loads(profile_json) if profile_json else None
        except json.JSONDecodeError as exc:
            raise ValidationError("the company settings are not JSON") from exc
        if settings is not None and not isinstance(settings, dict):
            raise ValidationError("the company settings are not a JSON object")
        company = _profile(settings)
        designated = designations.designate_project(designed, company)
        sheets = pages.build_drawing_set(designated, company)
    found, matched = markups.read_markups(data, sheets)
    logger.info(
        "design.markups_read", tenant_id=user.tenant_id, markups=len(found), matched=matched
    )
    items = []
    for markup in found:
        made = (
            markup_suggestions.suggest(designated, markup.board, markup.labels, markup.text)
            if designated is not None and matched
            else None
        )
        items.append(
            MarkupItem(
                page=markup.page,
                kind=markup.kind,
                author=markup.author,
                text=markup.text,
                sheet=markup.sheet,
                board=markup.board,
                near=markup.near,
                suggestion=_suggestion(made) if made else None,
            )
        )
    return MarkupReport(markups=items, matched=matched)


def _suggestion(made: markup_suggestions.Suggestion) -> MarkupSuggestion:
    return MarkupSuggestion.model_validate(
        {
            **vars(made),
            "candidates": [
                {"circuit": circuit, "load_index": index} for circuit, index in made.candidates
            ],
        }
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
    overall = [note("text", text=text) for text in drafted.assumptions]
    logger.info("design.schedule_suggested", tenant_id=user.tenant_id, loads=len(loads))
    return LoadScheduleSuggestion(loads=loads, assumptions=[*overall, *notes])


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
            "no circuit is PLC-switched: mark the loads the PLC controls, then design again",
            code="plc_nothing_switched",
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
