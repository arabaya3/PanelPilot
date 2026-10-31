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
from collections.abc import Callable
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Paragraph, Spacer

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
    if not mark.findings:
        return ["No unresolved findings."]
    lines: list[str] = []
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
        sheet.add(
            Text(FRAME_LEFT, FRAME_TOP - 3.0, mark.label, size=4.0, bold=True),
            Text(FRAME_RIGHT, FRAME_TOP - 3.0, mark.identity, size=2.2, anchor=Anchor.END),
            Text(FRAME_LEFT, SHEET_HEIGHT - 3.5, mark.banner, size=2.2, bold=True),
        )
    return sheets


PageStamp = Callable[[Canvas, BaseDocTemplate], None]


def pdf_page_stamp(mark: ExportMark) -> PageStamp:
    """A ReportLab page callback drawing the mark at the top and foot of every page.

    Args:
        mark: The export's mark.

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
        # line ran off the page, and with it what the file is.
        for index, line in enumerate(_wrapped(mark.identity, room, bold=False)):
            font, drawn = font_for(line, right_to_left=has_rtl(line))
            canvas.setFont(font, 7)
            canvas.drawString(document.leftMargin, height - (10.5 + 3 * index) * mm, drawn)
        # The foot repeats the label and identity, wrapped upwards from 5 mm.
        lines = _wrapped(mark.banner, room, bold=True)
        for index, line in enumerate(reversed(lines)):
            font, drawn = font_for(line, bold=True, right_to_left=has_rtl(line))
            canvas.setFont(font, 7)
            canvas.drawString(document.leftMargin, (5 + 3 * index) * mm, drawn)
        canvas.restoreState()

    return stamp


def _wrapped(text: str, room: float, *, bold: bool) -> list[str]:
    """``text`` split into lines of at most ``room`` points at 7 pt, by words.

    At most three lines, which the page margins hold; a longer text keeps
    its first three, so what must survive goes first. A word wider than the
    room is broken across lines, never drawn off the page.
    """

    def fits(candidate: str) -> bool:
        font, drawn = font_for(candidate, bold=bold, right_to_left=has_rtl(candidate))
        return bool(stringWidth(drawn, font, 7) <= room)

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
    return lines[:3]


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


def mark_json(project_json: str, mark: ExportMark) -> bytes:
    """The project JSON inside an envelope stating its mark and findings.

    Args:
        project_json: The project, as JSON.
        mark: The export's mark.

    Returns:
        ``{"export_status": {...}, "findings": [...], "project": {...}}``.
    """
    envelope = {
        "export_status": {"label": mark.label, "identity": mark.identity},
        "findings": [found.model_dump(mode="json") for found in mark.findings],
        "project": json.loads(project_json),
    }
    return json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")
