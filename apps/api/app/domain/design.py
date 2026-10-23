"""Panel design: design a board, and export a project in any format.

Routes call these; these call ``app.design``. Nothing is persisted here: a
project is returned to the caller, who may send it back to export it as a
**draft**. A draft is whatever the client sent, so it is never trusted: it is
re-checked (``app.design.integrity``), its checker and approver are blanked,
and every format is marked ``DRAFT — NOT FOR EXECUTION`` with the findings
(``app.design.marking``). An export that carries an approval goes through a
saved revision instead (``app.domain.design_projects.export_revision``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.ai import schedule_writer
from app.core.errors import ConflictError, ValidationError
from app.design import (
    calc_report,
    designations,
    export_aml,
    export_dxf,
    export_ecad,
    export_lists,
    export_qet,
    integrity,
    markets,
    marking,
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
from app.design.sheet import Sheet
from app.domain import billing
from app.domain.plans import Feature
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
    MarketInfo,
    MarkupItem,
    MarkupReport,
    MarkupSuggestion,
    PlcProgramRequest,
    PlcProgramResponse,
    PriceListEntry,
    PricingSettings,
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
    ExportFormat.EPLAN_DEVICES_CSV: ("text/csv; charset=utf-8", "eplan-devices.csv"),
    ExportFormat.ACE_COMPONENTS_CSV: ("text/csv; charset=utf-8", "ace-components.csv"),
    ExportFormat.ACE_TERMINALS_CSV: ("text/csv; charset=utf-8", "ace-terminals.csv"),
    ExportFormat.QUOTATION_PDF: ("application/pdf", "quotation.pdf"),
    ExportFormat.CALCULATIONS_PDF: ("application/pdf", "calculations.pdf"),
    ExportFormat.QUOTATION_CSV: ("text/csv; charset=utf-8", "quotation.csv"),
    ExportFormat.PLC_ST: ("text/plain; charset=utf-8", "st"),
    ExportFormat.PLC_IO_CSV: ("text/csv; charset=utf-8", "io.csv"),
    ExportFormat.JSON: ("application/json", "json"),
}

#: The formats another drawing program opens: on plans with ``ECAD_EXPORT``.
ECAD_FORMATS = frozenset(
    {
        ExportFormat.DXF,
        ExportFormat.QET,
        ExportFormat.AML,
        ExportFormat.EPLAN_DEVICES_CSV,
        ExportFormat.ACE_COMPONENTS_CSV,
        ExportFormat.ACE_TERMINALS_CSV,
    }
)


#: The formats whose ``DRAFT — NOT FOR EXECUTION`` mark is only a comment,
#: which a normal import strips, so the file would arrive unmarked. Until a
#: mark that survives import is shown, none of them is exported, as a draft or
#: as a revision. The rest stay: the PDFs print the mark on every page, the DXF
#: carries it as visible TEXT entities, and the project JSON as its
#: ``export_status`` field.
UNPROTECTED_FORMATS = frozenset(
    {
        # CSV: the mark is a leading comment line; spreadsheet and ECAD list
        # imports drop or misread it.
        ExportFormat.DEVICES_CSV,
        ExportFormat.PARTS_CSV,
        ExportFormat.CABLES_CSV,
        ExportFormat.CIRCUITS_CSV,
        ExportFormat.TERMINALS_CSV,
        ExportFormat.EPLAN_DEVICES_CSV,
        ExportFormat.ACE_COMPONENTS_CSV,
        ExportFormat.ACE_TERMINALS_CSV,
        ExportFormat.QUOTATION_CSV,
        ExportFormat.PLC_IO_CSV,
        # QElectroTech and AutomationML: the mark is an XML comment, which an
        # XML parser discards on import.
        ExportFormat.QET,
        ExportFormat.AML,
        # PLC program (ST): the mark is a source comment, which a PLC IDE does
        # not keep visible once the program is imported and compiled.
        ExportFormat.PLC_ST,
    }
)


def refuse_unprotected(export_format: ExportFormat) -> None:
    """Refuse a format that cannot keep a draft mark once imported.

    Args:
        export_format: The file asked for.

    Raises:
        ConflictError: ``export_format_unprotected`` if ``export_format`` is
            in ``UNPROTECTED_FORMATS``.
    """
    if export_format not in UNPROTECTED_FORMATS:
        return
    allowed = ",".join(f.value for f in ExportFormat if f not in UNPROTECTED_FORMATS)
    raise ConflictError(
        f"{export_format.value} cannot keep a visible draft mark once imported; "
        f"export one of: {allowed}",
        code="export_format_unprotected",
        params={
            "format": export_format.value,
            "reason": "draft_mark_not_persistent",
            "allowed": allowed,
        },
    )


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


def resolve_profile(settings: dict[str, Any] | None) -> CompanyProfile:
    """The company profile a request's settings resolve to, over the default.

    Args:
        settings: The company's settings as entered; ``None`` for the default.

    Returns:
        The profile.

    Raises:
        ValidationError: If a setting is unknown or malformed.
    """
    return _profile(settings)


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
        session: Open database session, for the plan; nothing is persisted.
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


def build_design(request: ProjectDesignRequest) -> tuple[DesignProject, CompanyProfile]:
    """Design every board in a request and designate the project under its profile.

    Args:
        request: Title-block data, each board's schedule, and the profile.

    Returns:
        The designated project, and the profile it was designed under.

    Raises:
        ValidationError: If the profile is malformed, the boards' feeding is
            inconsistent, or a load cannot be protected or cabled.
    """
    company = _profile(request.profile)
    boards = project.design_boards(request.boards, company)
    designed = designations.designate_project(
        DesignProject(info=request.info, boards=boards, parts=motors.parts_for(boards)), company
    )
    return designed, company


def design_project(
    *, session: Session, user: CurrentUser, request: ProjectDesignRequest
) -> BoardDesignResponse:
    """Design every board in a project and issue it under the company's profile.

    Args:
        session: Open database session, for the plan; nothing is persisted.
        user: The authenticated caller, for the log line.
        request: Title-block data, each board's schedule, and the profile.

    Returns:
        The designated project and the profile applied.

    Raises:
        ValidationError: If the profile is malformed, the boards' feeding is
            inconsistent, or a load cannot be protected or cabled.
    """
    del session
    designed, company = build_design(request)
    logger.info(
        "design.project_designed",
        tenant_id=user.tenant_id,
        boards=len(designed.boards),
        circuits=sum(len(board.circuits) for board in designed.boards),
        profile=company.key,
    )
    return BoardDesignResponse(project=designed, profile=company)


def require_format(*, session: Session, user: CurrentUser, export_format: ExportFormat) -> None:
    """Refuse a format the caller's plan does not include.

    Args:
        session: Open database session, for the plan.
        user: The authenticated caller.
        export_format: The file asked for.

    Raises:
        AuthorizationError: If the caller's plan does not include the format.
    """
    if export_format in ECAD_FORMATS:
        billing.require_feature(session=session, user=user, feature=Feature.ECAD_EXPORT)
    elif export_format in (ExportFormat.QUOTATION_PDF, ExportFormat.QUOTATION_CSV):
        billing.require_feature(session=session, user=user, feature=Feature.QUOTATION)


def drawing_sheets(
    designated: DesignProject, company: CompanyProfile, mark: marking.ExportMark
) -> list[Sheet]:
    """A project's drawing set as exported: every page marked, the findings after.

    Args:
        designated: The project, designated under ``company``.
        company: The company whose pages apply.
        mark: The export's mark.

    Returns:
        The sheets.
    """
    return marking.mark_sheets(pages.build_drawing_set(designated, company), mark)


#: The CSV lists, each written from the project alone.
_LISTS: dict[ExportFormat, Callable[[DesignProject], str]] = {
    ExportFormat.DEVICES_CSV: export_lists.device_list,
    ExportFormat.PARTS_CSV: export_lists.parts_list,
    ExportFormat.CABLES_CSV: export_lists.cable_list,
    ExportFormat.CIRCUITS_CSV: export_lists.circuit_schedule,
    ExportFormat.TERMINALS_CSV: export_lists.terminal_list,
    ExportFormat.EPLAN_DEVICES_CSV: export_ecad.eplan_device_list,
    ExportFormat.ACE_COMPONENTS_CSV: export_ecad.ace_component_list,
    ExportFormat.ACE_TERMINALS_CSV: export_ecad.ace_terminal_list,
}


def render_export(
    *,
    designated: DesignProject,
    company: CompanyProfile,
    export_format: ExportFormat,
    pricing: PricingSettings | None,
    mark: marking.ExportMark,
) -> ExportedFile:
    """Write a designated project as one file, carrying its mark.

    Args:
        designated: The project, designated under ``company``.
        company: The company it is issued under.
        export_format: The file to produce.
        pricing: The company's prices, for a quotation.
        mark: The status mark and findings the file carries.

    Returns:
        The file.

    Raises:
        ValidationError: If a quotation is asked for without pricing
            settings, or a control program for a project with no PLC-switched
            circuit.
    """
    content: bytes
    if export_format is ExportFormat.PDF:
        sheets = drawing_sheets(designated, company, mark)
        content = render_pdf.render_pdf(sheets, title=designated.info.name, author=company.name)
    elif export_format is ExportFormat.DXF:
        content = export_dxf.export_dxf(drawing_sheets(designated, company, mark))
    elif export_format is ExportFormat.QET:
        content = marking.mark_xml(export_qet.export_qet(designated), mark)
    elif export_format is ExportFormat.AML:
        content = marking.mark_xml(export_aml.export_aml(designated), mark)
    elif export_format is ExportFormat.CALCULATIONS_PDF:
        content = calc_report.render_calculations_pdf(designated, company, mark)
    elif export_format in (ExportFormat.QUOTATION_PDF, ExportFormat.QUOTATION_CSV):
        if pricing is None:
            raise ValidationError(
                "a quotation needs the company's pricing settings", code="quotation_needs_pricing"
            )
        priced = quotation.price_project(designated, pricing)
        content = (
            quotation_pdf.render_quotation_pdf(priced, designated, company=company.name, mark=mark)
            if export_format is ExportFormat.QUOTATION_PDF
            else marking.mark_csv(quotation.quotation_csv(priced), mark).encode("utf-8")
        )
    elif export_format in (ExportFormat.PLC_ST, ExportFormat.PLC_IO_CSV):
        program = _program(designated)
        content = (
            marking.mark_plc_source(program.source, mark).encode("utf-8")
            if export_format is ExportFormat.PLC_ST
            else marking.mark_csv(plc_program.io_list_csv(program), mark).encode("utf-8")
        )
    elif export_format is ExportFormat.JSON:
        content = marking.mark_json(designated.model_dump_json(), mark)
    else:
        content = marking.mark_csv(_LISTS[export_format](designated), mark).encode("utf-8")
    media_type, extension = _MEDIA_TYPES[export_format]
    return ExportedFile(
        content=content,
        media_type=media_type,
        filename=f"{_slug(designated.info.name)}.{extension}",
        display_name=f"{_unicode_slug(designated.info.name)}.{extension}",
    )


#: What a draft says it is, beside its label.
DRAFT_IDENTITY = "Unsaved draft: not a saved, validated or approved revision"


def _as_draft(sent: DesignProject) -> DesignProject:
    """The project as a draft prints it: no checker or approver on any revision.

    The revision rows a client sends stay drawing metadata, but who checked
    and approved a design is recorded only by approving a saved revision, so
    a draft prints neither.
    """
    revisions = [
        revision.model_copy(update={"checked_by": "", "approved_by": ""})
        for revision in sent.info.revisions
    ]
    return sent.model_copy(update={"info": sent.info.model_copy(update={"revisions": revisions})})


def draft_mark(designated: DesignProject) -> marking.ExportMark:
    """The mark a draft export carries: draft, with the project's findings.

    Args:
        designated: The project as sent, designated.

    Returns:
        The mark.
    """
    return marking.ExportMark(
        label=marking.DRAFT_LABEL,
        identity=DRAFT_IDENTITY,
        findings=tuple(integrity.project_findings(designated)),
    )


def export_design(
    *, session: Session, user: CurrentUser, request: DesignExportRequest
) -> ExportedFile:
    """Export a project as a draft, designated afresh under the company's profile.

    Designations are reassigned on every export, so a circuit an engineer
    added or removed since the design renumbers cleanly. The project is the
    client's, so it is re-checked, its checker and approver are blanked, and
    the file is marked ``DRAFT — NOT FOR EXECUTION`` with what the checks
    found.

    Args:
        session: Open database session, for the plan; nothing is persisted.
        user: The authenticated caller, for the log line.
        request: The project, the profile settings and the format.

    Returns:
        The file.

    Raises:
        ValidationError: If the profile is malformed, a quotation is asked
            for without pricing settings, or a control program for a project
            with no PLC-switched circuit.
        AuthorizationError: If the caller's plan does not include the format.
        ConflictError: ``export_format_unprotected`` for a format that cannot
            keep a draft mark once imported.
    """
    refuse_unprotected(request.format)
    require_format(session=session, user=user, export_format=request.format)
    company = _profile(request.profile)
    designated = designations.designate_project(_as_draft(request.project), company)
    mark = draft_mark(designated)
    exported = render_export(
        designated=designated,
        company=company,
        export_format=request.format,
        pricing=request.pricing,
        mark=mark,
    )
    logger.info(
        "design.exported",
        tenant_id=user.tenant_id,
        format=request.format.value,
        bytes=len(exported.content),
        draft=True,
        blocking=len(integrity.blocking(list(mark.findings))),
    )
    return exported


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
        designated = designations.designate_project(_as_draft(designed), company)
        # Laid out as the exported drawing set is, findings pages included, so
        # its pages line up with the PDF's.
        sheets = drawing_sheets(designated, company, draft_mark(designated))
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
    """Refuse the control program for a project the client sent.

    The program's Structured Text and I/O list are the ``plc_st`` and
    ``plc_io_csv`` files, which cannot keep a draft mark once imported
    (``UNPROTECTED_FORMATS``). Returning them here would hand out, unmarked,
    what their export refuses, so this is refused the same way, before any of
    the program is written.

    Args:
        user: The authenticated caller, for the log line.
        request: The project and the profile settings.

    Returns:
        Never: the refusal is raised.

    Raises:
        ConflictError: ``export_format_unprotected``, with the ``plc_st``
            export's params.
    """
    logger.info("design.plc_refused", tenant_id=user.tenant_id, boards=len(request.project.boards))
    refuse_unprotected(ExportFormat.PLC_ST)
    raise AssertionError("refuse_unprotected refuses plc_st")  # pragma: no cover


def list_markets() -> list[MarketInfo]:
    """Every market a board can be designed for, with its supply and rules.

    Returns:
        The markets, as ``app.design.markets`` holds them.
    """
    return [
        MarketInfo(
            code=found.code,
            name=found.name,
            voltage_v=found.voltage_v,
            phase_voltage_v=found.phase_voltage_v,
            frequency_hz=found.frequency_hz,
            earthing=found.earthing,
            currency=found.currency,
            regulation=found.regulation,
            max_voltage_drop_percent=found.max_voltage_drop_percent,
            rcd_every_final_circuit=found.rcd_every_final_circuit,
            languages=list(found.languages),
        )
        for found in markets.MARKETS.values()
    ]
