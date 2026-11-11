"""The status mark every export carries, and its findings section.

No export of a design says it is fit for execution: this service produces no
execution package yet (wiring and terminal connectivity, control schematics,
orderable parts, mechanical layout). Every exported file is therefore marked,
in whatever way its format can carry a mark:

* a draft (``POST /design/export``, or a saved revision not approved):
  ``DRAFT — NOT FOR EXECUTION``;
* a saved revision approved for exactly the design it holds:
  ``REVIEW APPROVED — STUDY, NOT FOR EXECUTION``, with the approver, the date,
  the revision, the design's hash and the rules version, all from the
  database.

Drawing sheets (PDF and DXF) carry it on every page, top and bottom, and end
with a findings page; the calculations and quotation PDFs on every page with
a findings section; a CSV in a first comment line; XML (QElectroTech,
AutomationML) in a comment after the declaration; Structured Text in a
comment; the project JSON in an envelope around the project.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Paragraph, Spacer

from app.design import approval_scope
from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl
from app.design.sheet import (
    FRAME_BOTTOM,
    FRAME_LEFT,
    FRAME_RIGHT,
    FRAME_TOP,
    SHEET_HEIGHT,
    Anchor,
    Rect,
    Sheet,
    Text,
)
from app.models.schemas.design import DesignFinding, FindingSeverity

DRAFT_LABEL = "DRAFT — NOT FOR EXECUTION"
REVIEW_APPROVED_LABEL = "REVIEW APPROVED — STUDY, NOT FOR EXECUTION"
#: Every quotation file's label, whatever the design's state: its prices are
#: supplied with the export and no review or approval covers them.
PROVISIONAL_ESTIMATE_LABEL = "PROVISIONAL ESTIMATE — PRICES NOT APPROVED — NOT FOR EXECUTION"

#: What an execution package needs that no export produces yet.
EXECUTION_PACKAGE_MISSING: tuple[str, ...] = (
    "wiring_and_terminal_connectivity",
    "control_schematics",
    "orderable_parts",
    "mechanical_layout",
)

_SEVERITY_ORDER = {FindingSeverity.BLOCKING: 0, FindingSeverity.WARNING: 1, FindingSeverity.INFO: 2}

#: Findings lines one findings sheet holds, and the characters of each.
_LINES_PER_SHEET = 50
_LINE_CHARS = 220


@dataclass(frozen=True)
class ExportMark:
    """What an export is labelled with.

    Attributes:
        label: :data:`DRAFT_LABEL`, :data:`REVIEW_APPROVED_LABEL`, or for a
            quotation always :data:`PROVISIONAL_ESTIMATE_LABEL`.
        identity: What the file is: the revision, its approval and the
            design's hash, or that it is an unsaved draft.
        findings: The unresolved findings, printed in the file where its
            format allows.
    """

    label: str
    identity: str
    findings: tuple[DesignFinding, ...] = field(default_factory=tuple)
    #: The revision's requirements lines (M6a): its open items (``[OPEN] …``)
    #: and acknowledged assumptions, printed after the findings.
    requirements: tuple[str, ...] = field(default_factory=tuple)
    #: What a review approval covers and does not (M6d), printed after the
    #: requirements lines on every review export.
    scope: tuple[str, ...] = field(default_factory=lambda: tuple(approval_scope.statement_lines()))
    #: The JSON export's ``approval`` block (``approval_scope.block``);
    #: ``None`` prints an unapproved one with no exclusions.
    approval: Mapping[str, Any] | None = None

    @property
    def banner(self) -> str:
        """The label and identity on one line."""
        return f"{self.label} | {self.identity}"

    def counts(self) -> str:
        """How many findings of each severity, as one phrase."""
        totals = dict.fromkeys(FindingSeverity, 0)
        for found in self.findings:
            totals[found.severity] += 1
        return ", ".join(f"{totals[severity]} {severity.value}" for severity in FindingSeverity)


def finding_lines(mark: ExportMark) -> list[str]:
    """The findings as printed lines, blocking first.

    Args:
        mark: The export's mark.

    Returns:
        One line per finding (wrapped lines indented), or one line saying
        there are none.
    """
    lines: list[str] = [] if mark.findings else ["No unresolved findings."]
    ordered = sorted(mark.findings, key=lambda found: _SEVERITY_ORDER[found.severity])
    for found in ordered:
        where = " / ".join(part for part in (found.board_id, found.circuit_id) if part)
        confirm = (
            "; engineering confirmation required" if found.requires_engineering_confirmation else ""
        )
        text = (
            f"[{found.severity.value.upper()}] {where}: {found.message} "
            f"({found.code}; source: {found.source}{confirm})"
        )
        wrapped = textwrap.wrap(text, _LINE_CHARS, subsequent_indent="    ") or [text]
        lines.extend(wrapped)
    for text in (*mark.requirements, *mark.scope):
        lines.extend(textwrap.wrap(text, _LINE_CHARS, subsequent_indent="    ") or [text])
    return lines


def _finding_chunks(mark: ExportMark) -> list[list[str]]:
    lines = finding_lines(mark)
    return [lines[i : i + _LINES_PER_SHEET] for i in range(0, len(lines), _LINES_PER_SHEET)]


def findings_sheet_count(mark: ExportMark) -> int:
    """How many findings sheets ``mark_sheets`` adds for a mark.

    Known before the drawing set is laid out, so its title blocks can count
    them in their "N of M".

    Args:
        mark: The export's mark.

    Returns:
        The number of findings sheets; at least one, since no findings is
        said on a sheet of its own.
    """
    return len(_finding_chunks(mark))


def mark_sheets(sheets: list[Sheet], mark: ExportMark) -> list[Sheet]:
    """Mark every drawing sheet, and add the findings pages after them.

    Args:
        sheets: The drawing set's sheets, numbered from 1.
        mark: The export's mark.

    Returns:
        The same sheets, marked, followed by the findings sheets.
    """
    chunks = _finding_chunks(mark)
    start = len(sheets)
    total = start + len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        sheet = Sheet(number=start + index, title="Findings")
        sheet.add(
            Rect(FRAME_LEFT, FRAME_TOP, FRAME_RIGHT - FRAME_LEFT, FRAME_BOTTOM - FRAME_TOP, 0.5),
            Text(
                FRAME_LEFT + 5,
                FRAME_TOP + 12,
                f"Findings ({mark.counts()}), page {index} of {len(chunks)}"
                f" — sheet {start + index} of {total}",
                size=4.0,
                bold=True,
            ),
        )
        for row, line in enumerate(chunk):
            sheet.add(Text(FRAME_LEFT + 5, FRAME_TOP + 22 + row * 4.8, line, size=2.4))
        sheets.append(sheet)
    for sheet in sheets:
        sheet.add(*_sheet_mark(mark))
    return sheets


#: A drawing sheet's mark text sizes (character height, mm), largest first:
#: the identity and banner are drawn at the largest that holds them whole.
_SHEET_MARK_SIZES = (2.2, 1.8, 1.5, 1.2)
#: The label's size, and the gap between it and the identity beside it (mm).
_SHEET_LABEL_SIZE = 4.0
_SHEET_GAP = 4.0


def _sheet_width(text: str, size: float, bold: bool) -> float:
    """A sheet text's printed width in mm, as ``render_pdf`` draws it."""
    font, drawn = font_for(text, bold=bold, right_to_left=has_rtl(text))
    width: float = stringWidth(drawn, font, size * mm / 0.72)
    return float(width / mm)


def _sheet_fit(
    text: str, room: float, last: float, ceiling: float, *, bold: bool
) -> tuple[float, float, list[str]]:
    """``(size, pitch, lines)``: the largest size at which ``text`` fits a margin.

    Wrapped to ``room`` mm, its last line's baseline at ``last`` and the lines
    stacked upwards ``pitch`` apart, the first line's box (character height
    over 0.72, the PDF's font size) must stay below ``ceiling``. Never
    shortened: at the smallest size the text is wrapped whole regardless.
    """
    lines: list[str] = []
    pitch = 0.0
    for size in _SHEET_MARK_SIZES:
        box = size / 0.72
        pitch = box * 1.05

        def fits(candidate: str, size: float = size) -> bool:
            return _sheet_width(candidate, size, bold) <= room

        lines = _wrap(text, fits)
        if last - (len(lines) - 1) * pitch - box >= ceiling:
            return size, pitch, lines
    return _SHEET_MARK_SIZES[-1], pitch, lines


def _sheet_mark(mark: ExportMark) -> list[Text]:
    """A drawing sheet's mark: the label, the identity beside it, the banner below the frame.

    The identity is wrapped to the room right of the label, and the banner to
    the frame's width, each at the largest size its margin holds whole (a
    long identity drawn on one line ran over the label and off the sheet). A
    mark that fits one line is drawn where it always was.
    """
    label_end = FRAME_LEFT + _sheet_width(mark.label, _SHEET_LABEL_SIZE, True) + _SHEET_GAP
    items = [Text(FRAME_LEFT, FRAME_TOP - 3.0, mark.label, size=_SHEET_LABEL_SIZE, bold=True)]
    size, pitch, lines = _sheet_fit(
        mark.identity, FRAME_RIGHT - label_end, FRAME_TOP - 3.0, 0.2, bold=False
    )
    for index, line in enumerate(reversed(lines)):
        y = FRAME_TOP - 3.0 - index * pitch
        items.append(Text(FRAME_RIGHT, y, line, size=size, anchor=Anchor.END))
    size, pitch, lines = _sheet_fit(
        mark.banner, FRAME_RIGHT - FRAME_LEFT, SHEET_HEIGHT - 3.5, FRAME_BOTTOM + 0.3, bold=True
    )
    for index, line in enumerate(reversed(lines)):
        y = SHEET_HEIGHT - 3.5 - index * pitch
        items.append(Text(FRAME_LEFT, y, line, size=size, bold=True))
    return items


PageStamp = Callable[[Canvas, BaseDocTemplate], None]

#: The mark's line pitch at the top and foot of a ReportLab page (7 pt text).
_PITCH = 3 * mm


def _head_lines(mark: ExportMark, room: float) -> list[str]:
    return _wrapped(mark.identity, room, bold=False)


def _foot_lines(mark: ExportMark, room: float) -> list[str]:
    return _wrapped(mark.banner, room, bold=True)


def pdf_mark_margins(
    mark: ExportMark,
    pagesize: tuple[float, float],
    *,
    left: float,
    right: float,
    top: float,
    bottom: float,
    foot_reserve: float = 0.0,
) -> tuple[float, float]:
    """The top and bottom margins a page needs to hold its mark clear of the body.

    The identity is wrapped downwards from 10.5 mm and the banner upwards
    from 5 mm, never shortened, so a long one needs more margin; a mark that
    fits the given margins leaves them as they are.

    Args:
        mark: The export's mark.
        pagesize: The page's size.
        left: The left margin.
        right: The right margin.
        top: The document's own top margin.
        bottom: The document's own bottom margin.
        foot_reserve: Width kept free at the foot's right for the document's
            own label (``pdf_page_stamp``'s ``reserve``).

    Returns:
        ``(top, bottom)``: at least the given margins.
    """
    room = pagesize[0] - left - right
    head = len(_head_lines(mark, room))
    foot = len(_foot_lines(mark, room - foot_reserve))
    need_top = 10.5 * mm + (head - 1) * _PITCH + 1.5 * mm
    need_bottom = 5 * mm + (foot - 1) * _PITCH + 4 * mm
    return max(top, need_top), max(bottom, need_bottom)


def foot_reserve(text: str) -> float:
    """The foot width ``text`` (7 pt, at the right) keeps free, with a 4 mm gap."""
    font, drawn = font_for(text)
    width: float = stringWidth(drawn, font, 7)
    return float(width + 4 * mm)


def pdf_page_stamp(mark: ExportMark, *, reserve: float = 0.0) -> PageStamp:
    """A ReportLab page callback drawing the mark at the top and foot of every page.

    Args:
        mark: The export's mark.
        reserve: Width (pt) kept free at the right of the foot for the
            document's own foot label (``foot_reserve``); the banner is
            wrapped clear of it.

    Returns:
        A callback for ``onFirstPage`` and ``onLaterPages``.
    """

    def stamp(canvas: Canvas, document: BaseDocTemplate) -> None:
        width, height = document.pagesize
        room = width - document.leftMargin - document.rightMargin
        canvas.saveState()
        font, label = font_for(mark.label, bold=True)
        canvas.setFont(font, 10)
        canvas.setFillColor(colors.HexColor("#b00020"))
        canvas.drawString(document.leftMargin, height - 7 * mm, label)
        # On its own lines, wrapped to the page: a long identity drawn on one
        # line ran off the page, and with it what the file is. Never cut:
        # ``pdf_mark_margins`` gives the lines room.
        for index, line in enumerate(_head_lines(mark, room)):
            font, drawn = font_for(line, right_to_left=has_rtl(line))
            canvas.setFont(font, 7)
            canvas.drawString(document.leftMargin, height - 10.5 * mm - index * _PITCH, drawn)
        # The foot repeats the label and identity, wrapped upwards from 5 mm,
        # clear of the document's own foot label.
        lines = _foot_lines(mark, room - reserve)
        for index, line in enumerate(reversed(lines)):
            font, drawn = font_for(line, bold=True, right_to_left=has_rtl(line))
            canvas.setFont(font, 7)
            canvas.drawString(document.leftMargin, 5 * mm + index * _PITCH, drawn)
        canvas.restoreState()

    return stamp


def _wrap(text: str, fits: Callable[[str], bool]) -> list[str]:
    """``text`` split by words into lines that each ``fits``; a word too wide is broken."""
    lines: list[str] = []
    for whole in text.split(" "):
        word = whole
        while not fits(word):
            cut = next((i for i in range(len(word) - 1, 0, -1) if fits(word[:i])), 1)
            lines.append(word[:cut])
            word = word[cut:]
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and fits(candidate):
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def _wrapped(text: str, room: float, *, bold: bool) -> list[str]:
    """``text`` split into lines of at most ``room`` points at 7 pt, by words.

    Every line: a long text is never cut short (it used to keep only its
    first three lines, silently dropping the rest of an approval).
    """

    def fits(candidate: str) -> bool:
        font, drawn = font_for(candidate, bold=bold, right_to_left=has_rtl(candidate))
        return bool(stringWidth(drawn, font, 7) <= room)

    return _wrap(text, fits)


def pdf_findings_section(
    mark: ExportMark, heading: ParagraphStyle, body: ParagraphStyle
) -> list[Flowable]:
    """The findings as a report section: a heading, then one paragraph each.

    Args:
        mark: The export's mark.
        heading: The heading's style.
        body: Each finding's style.

    Returns:
        The flowables.
    """
    section: list[Flowable] = [
        Spacer(1, 4 * mm),
        Paragraph(escape(f"Findings ({mark.counts()})"), heading),
    ]
    for line in finding_lines(mark):
        font, drawn = font_for(line, right_to_left=has_rtl(line))
        style = body if font == body.fontName else body.clone(f"{body.name}-{font}", fontName=font)
        section.append(Paragraph(escape(drawn), style))
    # Clear of what follows (the calculations report's signature table).
    section.append(Spacer(1, 6 * mm))
    return section


def mark_csv(text: str, mark: ExportMark) -> str:
    """A CSV with the mark as its first line, a ``#`` comment.

    Args:
        text: The CSV.
        mark: The export's mark.

    Returns:
        The marked CSV.
    """
    # A byte-order mark (which spreadsheet programs read the encoding by)
    # stays the file's first character, ahead of the comment.
    bom = "﻿" if text.startswith("﻿") else ""
    return f"{bom}# {mark.banner} | findings: {mark.counts()}\n{text.removeprefix(bom)}"


def mark_xml(content: bytes, mark: ExportMark) -> bytes:
    """An XML document with the mark as a comment after its declaration.

    Args:
        content: The document, its first line the XML declaration.
        mark: The export's mark.

    Returns:
        The marked document.
    """
    comment = f"{mark.banner} | findings: {mark.counts()}".replace("--", "- -")
    declaration, newline, rest = content.partition(b"\n")
    if not declaration.startswith(b"<?xml"):
        return f"<!-- {comment} -->\n".encode() + content
    return declaration + newline + f"<!-- {comment} -->\n".encode() + rest


def mark_plc_source(source: str, mark: ExportMark) -> str:
    """A Structured Text program with the mark as its first comment.

    Args:
        source: The IEC 61131-3 source.
        mark: The export's mark.

    Returns:
        The marked source.
    """
    comment = f"{mark.banner} | findings: {mark.counts()}".replace("*)", "* )")
    return f"(* {comment} *)\n{source}"


#: What the project JSON holds and does not, stated in every JSON export
#: (``export_scope``). It is a review copy of the calculated design: the docs
#: never promised a saved-revision backup, and nothing imports it back, so it
#: says it cannot restore a revision rather than implying a round trip.
JSON_EXPORT_SCOPE: dict[str, Any] = {
    "kind": "calculated_design_review_copy",
    "contains": [
        "export_status: the export's label and identity",
        "findings: the design's unresolved findings",
        "requirements: the revision's open items and acknowledged assumptions, when any",
        "approval: what a review approval covers and does not",
        "project: the calculated design (boards, circuits, devices, cables and notes)",
        "links: a pp-design/3 revision's board-to-board feeder links, as stored",
    ],
    "omits": [
        "the saved configuration of a pp-design/2 or pp-design/3 revision (model_v2: "
        "components, terminals, terminal strips, nets, connections, wires, templates "
        "and patterns); the terminal schedule, wire schedule and connection drawing "
        "PDFs print it for review",
    ],
    "restores_revision": False,
    "statement": (
        "A review copy of the calculated design and its findings, not a backup of the "
        "saved revision: it cannot be imported to restore the revision or its configuration."
    ),
}


def mark_json(
    project_json: str, mark: ExportMark, links: list[dict[str, Any]] | None = None
) -> bytes:
    """The project JSON inside an envelope stating its mark and findings.

    Args:
        project_json: The project, as JSON.
        mark: The export's mark.
        links: A ``pp-design/3`` revision's feeder links, as stored (M7a),
            carried under ``links`` after ``project``; ``None`` adds no key.

    Returns:
        ``{"export_status": {...}, "export_scope": {...}, "findings": [...],
        "project": {...}}`` (``export_scope`` is :data:`JSON_EXPORT_SCOPE`).
    """
    envelope = {
        "export_status": {"label": mark.label, "identity": mark.identity},
        "export_scope": JSON_EXPORT_SCOPE,
        "findings": [found.model_dump(mode="json") for found in mark.findings],
        **({"requirements": list(mark.requirements)} if mark.requirements else {}),
        "approval": dict(
            mark.approval
            if mark.approval is not None
            else approval_scope.block(
                approved=False, scope_version=None, excluded=(), false_positives=()
            )
        ),
        "project": json.loads(project_json),
        **({"links": links} if links is not None else {}),
    }
    return json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")
