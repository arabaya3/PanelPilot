"""Review-only terminal and wire schedules of a saved ``pp-design/2`` revision, as PDF.

A4 landscape. Every page carries the export's mark (its label says "NOT FOR
EXECUTION") and, beside it, "REVIEW SCHEDULE — NOT FOR EXECUTION". The first
page names the project, revision, the full design hash, the snapshot schema,
the rules and the renderer version. The rows are the model's own
(``app.design.model.schedules``): nothing is computed, defaulted or looked up,
and every value cell shows where its value came from (``user``, ``data``,
``assumption`` with its statement, or ``unresolved`` where the model holds
none). After the rows, every structural finding is listed, warnings and
unresolved items included, with its effect on approval and execution, then
the design's other findings.

A schedule is review content only: it is not an execution document, and it
says nothing about electrical correctness, ratings, numbering, colours or
standards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.design.marking import ExportMark, pdf_findings_section, pdf_page_stamp
from app.design.model import schedules
from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl
from app.models.schemas.design import V2Finding

#: Printed on every page of a schedule, beside the export's mark.
SCHEDULE_LABEL = "REVIEW SCHEDULE — NOT FOR EXECUTION"

#: What every schedule says it is not.
DISCLAIMER = (
    "Review content only, derived from the saved model's data. Not an execution document. "
    "It does not state or check any rating, cross-section, colour, numbering convention, "
    "electrical compatibility or standards requirement, and no template or pattern in it "
    "has been reviewed unless its review status says so."
)

_HEAD = colors.HexColor("#e7eef4")

#: The most characters of an assumption's statement a table cell shows; every
#: statement is printed in full under "Assumptions" (a table row cannot be
#: split across pages, so a cell must stay well under a page tall).
STATEMENT_EXCERPT = 120

#: The most characters any other table cell shows; a longer value is printed in
#: full under "Full values", with a note number in the cell.
CELL_LIMIT = 400


class _Overflow:
    """The long values of one schedule, printed in full after its tables."""

    def __init__(self) -> None:
        self.assumptions: dict[str, str] = {}
        self.values: list[str] = []

    def assumption(self, assumption_id: str | None, statement: str | None) -> str:
        key = assumption_id or "?"
        text = statement or ""
        self.assumptions.setdefault(key, text)
        if len(text) <= STATEMENT_EXCERPT:
            return f"assumption {key}: {text}"
        return f"assumption {key}: {text[:STATEMENT_EXCERPT]}… (full statement under Assumptions)"

    def bounded(self, text: str) -> str:
        if len(text) <= CELL_LIMIT:
            return text
        self.values.append(text)
        return f"{text[:CELL_LIMIT]}… (full value: note {len(self.values)} under Full values)"


@dataclass(frozen=True)
class ScheduleHeader:
    """What a schedule names on its first page."""

    project: str
    revision: str
    design_sha256: str
    schema: str
    rules_version: str
    renderer_version: str


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    font, drawn = font_for(text, bold=style.fontName.endswith("Bold"), right_to_left=has_rtl(text))
    if font != style.fontName:
        style = style.clone(f"{style.name}-{font}", fontName=font)
    return Paragraph(escape(drawn), style)


def _styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": sample["Title"],
        "h2": sample["Heading2"],
        "body": sample["BodyText"].clone("s-body", fontSize=9, leading=12),
        "small": sample["BodyText"].clone("s-small", fontSize=7.5, leading=9.5),
        "cell": sample["BodyText"].clone("s-cell", fontSize=7, leading=8.5),
        "head": sample["BodyText"].clone(
            "s-head", fontName="Helvetica-Bold", fontSize=7, leading=8.5
        ),
    }


def _stamp(mark: ExportMark) -> object:
    base = pdf_page_stamp(mark)

    def stamp(canvas: Canvas, document: BaseDocTemplate) -> None:
        base(canvas, document)
        width, height = document.pagesize
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(colors.HexColor("#b00020"))
        canvas.drawRightString(width - document.rightMargin, height - 7 * mm, SCHEDULE_LABEL)
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(
            width - document.rightMargin,
            5 * mm,
            f"{SCHEDULE_LABEL} — page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    return stamp


def _cell(cell: schedules.AttributeCell, overflow: _Overflow) -> str:
    """A value with its source: ``"<value> [data]"``, ``"— [unresolved]"``."""
    value = cell.value if cell.value is not None else "—"
    if cell.source == "assumption":
        return f"{value} [{overflow.assumption(cell.assumption_id, cell.assumption_statement)}]"
    return f"{value} [{cell.source}]"


def _required(row: schedules.TerminalRow) -> str:
    if row.owner_kind != "component":
        return "— [n/a: strip point]"
    if row.required is None:
        return f"— [{schedules.UNRESOLVED}]"
    return ("required" if row.required else "not required") + " [data]"


def _table(
    head: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[float],
    styles: dict[str, ParagraphStyle],
    overflow: _Overflow,
) -> Table:
    data = [[_p(h, styles["head"]) for h in head]]
    data += [[_p(overflow.bounded(value), styles["cell"]) for value in row] for row in rows]
    if not rows:
        data.append([_p("(none)", styles["cell"])] + [_p("", styles["cell"])] * (len(head) - 1))
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), _HEAD),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _findings(
    structural: Sequence[V2Finding], styles: dict[str, ParagraphStyle], overflow: _Overflow
) -> list[object]:
    blocking = sum(1 for f in structural if f.severity == "blocking")
    unresolved = sum(1 for f in structural if f.unresolved)
    out: list[object] = [
        Spacer(1, 4 * mm),
        _p(
            f"Structural findings ({len(structural)}: {blocking} blocking, "
            f"{len(structural) - blocking} warning, {unresolved} unresolved)",
            styles["h2"],
        ),
    ]
    rows = [
        [
            f.severity.upper() + (" / UNRESOLVED" if f.unresolved else ""),
            f.code,
            f.message,
            ", ".join(f"{k}={v}" for k, v in sorted(f.location.items())),
            "blocks approval" if f.blocks_approval else "does not block approval",
            "execution blocker" if f.blocks_execution else "—",
            "yes" if f.requires_engineering_confirmation else "no",
        ]
        for f in structural
    ]
    out.append(
        _table(
            (
                "Severity",
                "Code",
                "Message",
                "Location",
                "Approval",
                "Execution",
                "Confirm",
            ),
            rows,
            (24, 52, 72, 48, 30, 26, 25),
            styles,
            overflow,
        )
    )
    return out


def _long_values(overflow: _Overflow, styles: dict[str, ParagraphStyle]) -> list[object]:
    out: list[object] = []
    if overflow.assumptions:
        out += [Spacer(1, 4 * mm), _p("Assumptions", styles["h2"])]
        out += [
            _p(f"{key}: {statement}", styles["body"])
            for key, statement in sorted(overflow.assumptions.items())
        ]
    if overflow.values:
        out += [Spacer(1, 4 * mm), _p("Full values", styles["h2"])]
        out += [
            _p(f"Note {number}: {value}", styles["body"])
            for number, value in enumerate(overflow.values, start=1)
        ]
    return out


def _render(
    title: str,
    header: ScheduleHeader,
    mark: ExportMark,
    table: Table,
    structural: Sequence[V2Finding],
    styles: dict[str, ParagraphStyle],
    overflow: _Overflow,
) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=22 * mm,
        bottomMargin=16 * mm,
        title=f"{title} - {header.project}",
        creator="PanelPilot",
        invariant=True,
    )
    story: list[object] = [
        _p(f"{title}: {header.project}", styles["title"]),
        _p(f"Revision {header.revision}; snapshot schema {header.schema}", styles["body"]),
        _p(f"Design hash (SHA-256): {header.design_sha256}", styles["body"]),
        _p(
            f"Rules {header.rules_version}; renderer {header.renderer_version}",
            styles["body"],
        ),
        _p(DISCLAIMER, styles["small"]),
        _p(
            "Sources: [user] entered by a user; [data] from the server's design or the "
            "configuration's data; [assumption ...] a value its input stated as an "
            "assumption, with its statement; [unresolved] the model holds no value.",
            styles["small"],
        ),
        Spacer(1, 3 * mm),
        table,
    ]
    story.extend(_findings(structural, styles, overflow))
    story.extend(_long_values(overflow, styles))
    story.extend(pdf_findings_section(mark, styles["h2"], styles["small"]))
    stamp = _stamp(mark)
    document.build(story, onFirstPage=stamp, onLaterPages=stamp)
    return buffer.getvalue()


def render_terminal_schedule(
    rows: Sequence[schedules.TerminalRow],
    header: ScheduleHeader,
    mark: ExportMark,
    structural: Sequence[V2Finding],
) -> bytes:
    """The terminal schedule as a PDF.

    Args:
        rows: ``schedules.terminal_schedule`` of the saved model.
        header: What the first page names.
        mark: The export's mark (label, identity, the design's other findings).
        structural: Every structural finding, with its effects.

    Returns:
        The PDF's bytes.
    """
    styles = _styles()
    overflow = _Overflow()
    body = [
        [
            row.board_id,
            (row.designation.product if row.designation else row.owner_id) + f" ({row.owner_kind})",
            row.marking
            + (f" {row.side} p{row.position} L{row.level}" if row.side is not None else ""),
            row.kind + " [data]",
            _required(row),
            (f"{row.signal} [{row.net_origin}]" if row.signal is not None else "— [no net]"),
            ", ".join(row.connected_to) or "—",
            (row.template_ref or "—")
            + (f"; {', '.join(row.pattern_refs)}" if row.pattern_refs else ""),
            ", ".join(row.flags) or "—",
        ]
        for row in rows
    ]
    table = _table(
        (
            "Board",
            "Owner",
            "Terminal",
            "Kind",
            "Required",
            "Net signal [origin]",
            "Connected to",
            "Template; pattern",
            "Findings",
        ),
        body,
        (14, 30, 26, 22, 26, 26, 36, 44, 52),
        styles,
        overflow,
    )
    return _render(
        "Terminal schedule (review only)", header, mark, table, structural, styles, overflow
    )


def render_wire_schedule(
    rows: Sequence[schedules.WireRow],
    header: ScheduleHeader,
    mark: ExportMark,
    structural: Sequence[V2Finding],
) -> bytes:
    """The wire schedule as a PDF.

    Args:
        rows: ``schedules.wire_schedule`` of the saved model.
        header: What the first page names.
        mark: The export's mark (label, identity, the design's other findings).
        structural: Every structural finding, with its effects.

    Returns:
        The PDF's bytes.
    """
    styles = _styles()
    overflow = _Overflow()
    body = [
        [
            row.board_id,
            row.wire_id,
            row.start,
            row.end,
            row.signal,
            _cell(row.number, overflow),
            _cell(row.cross_section_mm2, overflow),
            _cell(row.colour, overflow),
            row.pattern_ref or "—",
            ", ".join(row.flags) or "—",
        ]
        for row in rows
    ]
    table = _table(
        (
            "Board",
            "Wire",
            "From",
            "To",
            "Signal",
            "Number [source]",
            "Cross-section mm² [source]",
            "Colour [source]",
            "Pattern",
            "Findings",
        ),
        body,
        (14, 34, 26, 26, 16, 30, 30, 30, 36, 34),
        styles,
        overflow,
    )
    return _render("Wire schedule (review only)", header, mark, table, structural, styles, overflow)
