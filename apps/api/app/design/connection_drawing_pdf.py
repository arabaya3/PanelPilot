"""A review-only connection drawing of a saved ``pp-design/2`` revision, as PDF (M4).

Draws a :class:`~app.design.model.connection_drawing.DrawingSet` and decides
nothing about it: what is on each sheet, every cross-reference and every
printed value come from the layout (``app.design.model.connection_drawing``),
which takes them from the saved model alone. Symbols are drawn from the
template's symbol data; where there is none, a dashed placeholder box says
"symbol not defined".

A3 landscape. Sheet 1 is the cover; the drawing sheets follow, then the
open-items, sheet-index and findings sheets. Every sheet has a frame, a zone
grid, a title block naming the project, revision, sheet "n of N", board and
the full snapshot hash, and the export's mark at its top and foot with
:data:`DRAWING_LABEL` beside it, in the schedules' red.

A connection drawing is review content only: it is not an execution
document, and it states or checks no rating, cross-section, colour,
numbering convention, electrical compatibility or standard.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from app.design.marking import ExportMark, finding_lines, pdf_page_stamp
from app.design.model import connection_drawing as layout
from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl
from app.models.schemas.design import V2Finding

#: Printed on every sheet, beside the export's mark.
DRAWING_LABEL = "CONNECTION DRAWING — REVIEW ONLY — NOT FOR EXECUTION"

#: What every connection drawing says it is not.
DISCLAIMER = (
    "Review content only, drawn from the saved model's components, terminals, nets, "
    "connections and wires. Not an execution document. It does not state or check any "
    "rating, cross-section, colour, numbering convention, electrical compatibility or "
    "standards requirement; values are printed only as the saved design holds them, and "
    "'not set' where it holds none. No template, pattern or symbol in it has been reviewed "
    "unless its review status says so."
)

#: The legend printed on the cover.
LEGEND = (
    "→ X:1 (sheet n, zone 3B) via wire: the far end of a connection and where it is drawn.",
    "wire <id>: no. / mm² / colour: each value with its source [user] [data], "
    "'<value> assumed (<assumption>)', or 'not set' where the saved design holds none.",
    "UNCONNECTED: a terminal in no net (required, requiredness unresolved, or a strip point).",
    "UNREALISED: a net whose own connections do not join all its members.",
    "WIRE RECORD MISSING: a connection via wire with no wire record.",
    f"Dashed box '{layout.SYMBOL_NOT_DEFINED}': the template defines no symbol data.",
    "Zones: columns 1-8 across, rows A-F down; a terminal's zone is that of its point.",
)

_RED = colors.HexColor("#b00020")
_GREY = colors.HexColor("#555555")

_PAGE: tuple[float, float] = landscape(A3)
_MARGIN = 12 * mm
_BAND = 4 * mm
_AREA_LEFT: float = _MARGIN + _BAND
_AREA_RIGHT = _PAGE[0] - _MARGIN - _BAND
_AREA_TOP: float = _PAGE[1] - 24 * mm
_SLOT: float = 6.3 * mm
_AREA_BOTTOM = _AREA_TOP - layout.SLOTS * _SLOT
_COLUMN: float = (_AREA_RIGHT - _AREA_LEFT) / layout.COLUMNS
_ZONE = (_AREA_RIGHT - _AREA_LEFT) / layout.ZONE_COLUMNS
_POINT = _ZONE * layout.POINT_ZONE_OFFSET + 3 * mm
_SYMBOL_LEFT = 3 * mm
_SYMBOL_WIDTH = 30 * mm
_TITLE_LEFT = _PAGE[0] - _MARGIN - 180 * mm
_TITLE_BOTTOM = 15 * mm
_TITLE_TOP = _AREA_BOTTOM - _BAND - 1 * mm
_TEXT_LINE = 3.2 * mm
_TEXT_LINES = int((_AREA_TOP - _AREA_BOTTOM - 8 * mm) / _TEXT_LINE)
_TEXT_CHARS = 250


@dataclass(frozen=True)
class DrawingHeader:
    """What every sheet's title block and the cover name."""

    project: str
    revision: str
    design_sha256: str
    schema: str
    rules_version: str
    renderer_version: str


def _text(
    canvas: Canvas,
    x: float,
    y: float,
    text: str,
    size: float = 5.5,
    *,
    bold: bool = False,
    right: bool = False,
    room: float | None = None,
) -> None:
    font, drawn = font_for(text, bold=bold, right_to_left=has_rtl(text))
    if room is not None:
        # Shrunk to fit its room (never cut): every character stays printed.
        while size > 3.5 and stringWidth(drawn, font, size) > room:
            size -= 0.25
    canvas.setFont(font, size)
    if right:
        canvas.drawRightString(x, y, drawn)
    else:
        canvas.drawString(x, y, drawn)


def _sheet_frame(
    canvas: Canvas,
    header: DrawingHeader,
    mark: ExportMark,
    number: int,
    total: int,
    board: str,
    *,
    grid: bool,
) -> None:
    width, height = _PAGE
    pdf_page_stamp(mark)(
        canvas,
        SimpleNamespace(pagesize=_PAGE, leftMargin=_MARGIN, rightMargin=_MARGIN),
    )
    canvas.saveState()
    canvas.setFillColor(_RED)
    _text(canvas, width - _MARGIN, height - 7 * mm, DRAWING_LABEL, 10, bold=True, right=True)
    canvas.restoreState()
    canvas.setLineWidth(0.6)
    top = _AREA_TOP + _BAND
    bottom = _AREA_BOTTOM - _BAND
    canvas.rect(_MARGIN, bottom, width - 2 * _MARGIN, top - bottom)
    canvas.setLineWidth(0.3)
    canvas.rect(_AREA_LEFT, _AREA_BOTTOM, _AREA_RIGHT - _AREA_LEFT, _AREA_TOP - _AREA_BOTTOM)
    if grid:
        for n in range(layout.ZONE_COLUMNS):
            x = _AREA_LEFT + n * _ZONE
            if n:
                canvas.line(x, _AREA_TOP, x, top)
                canvas.line(x, _AREA_BOTTOM, x, bottom)
            for y in (_AREA_TOP + 1.2 * mm, bottom + 1.2 * mm):
                _text(canvas, x + _ZONE / 2, y, str(n + 1), 6)
        rows = layout.ZONE_ROWS
        step = (_AREA_TOP - _AREA_BOTTOM) / rows
        for n in range(rows):
            y = _AREA_TOP - n * step
            if n:
                canvas.line(_MARGIN, y, _AREA_LEFT, y)
                canvas.line(_AREA_RIGHT, y, width - _MARGIN, y)
            letter = layout.ZONE_LETTERS[n]
            _text(canvas, _MARGIN + 1.2 * mm, y - step / 2, letter, 6)
            _text(canvas, _AREA_RIGHT + 1.2 * mm, y - step / 2, letter, 6)
    # Title block.
    canvas.setLineWidth(0.6)
    canvas.rect(
        _TITLE_LEFT, _TITLE_BOTTOM, width - _MARGIN - _TITLE_LEFT, _TITLE_TOP - _TITLE_BOTTOM
    )
    x = _TITLE_LEFT + 2 * mm
    room = width - _MARGIN - x - 2 * mm
    lines = (
        ("Connection drawing (review only)", 8, True),
        (f"Project: {header.project}", 6.5, False),
        (
            f"Revision {header.revision}; schema {header.schema}; rules {header.rules_version}",
            5.5,
            False,
        ),
        (f"Renderer {header.renderer_version}; board {board or '-'}", 5.5, False),
        (f"Snapshot SHA-256: {header.design_sha256 or layout.NOT_SET}", 5.5, False),
        (f"Sheet {number} of {total}", 7, True),
    )
    y = _TITLE_TOP - 4 * mm
    for text, size, bold in lines:
        _text(canvas, x, y, text, size, bold=bold, room=room)
        y -= 3.4 * mm if size < 7 else 4 * mm
    canvas.saveState()
    canvas.setFillColor(_RED)
    _text(canvas, x, _TITLE_BOTTOM + 1.6 * mm, "REVIEW ONLY — NOT FOR EXECUTION", 7, bold=True)
    canvas.restoreState()


def _slot_y(slot: int) -> float:
    """The top of a slot."""
    return _AREA_TOP - slot * _SLOT


def _column_x(column: int) -> float:
    return _AREA_LEFT + column * _COLUMN


def _symbol(
    canvas: Canvas,
    part: layout.BlockPart,
    left: float,
    top: float,
    bottom: float,
    rows_y: dict[str, float],
) -> None:
    """The part's symbol from data, or a labelled placeholder; leads to its rows."""
    box_left = left + _SYMBOL_LEFT
    box_height = max(top - bottom, _SLOT)
    point_x = left + _POINT
    if part.symbol is None:
        canvas.saveState()
        canvas.setDash(2, 2)
        canvas.rect(box_left, top - box_height, _SYMBOL_WIDTH, box_height)
        canvas.restoreState()
        _text(canvas, box_left + 1.5 * mm, top - 3 * mm, layout.SYMBOL_NOT_DEFINED, 5.5, bold=True)
        for y in rows_y.values():
            canvas.line(box_left + _SYMBOL_WIDTH, y, point_x, y)
        return
    symbol = part.symbol
    scale = min(_SYMBOL_WIDTH / symbol.width, box_height / symbol.height, 3 * mm)

    def at(x: int, y: int) -> tuple[float, float]:
        return box_left + x * scale, top - y * scale

    canvas.setLineWidth(0.5)
    for shape in symbol.shapes:
        p = shape.points
        if shape.kind == "line":
            (x1, y1), (x2, y2) = at(p[0], p[1]), at(p[2], p[3])
            canvas.line(x1, y1, x2, y2)
        elif shape.kind == "rect":
            x1, y1 = at(p[0], p[1] + p[3])
            canvas.rect(x1, y1, p[2] * scale, p[3] * scale)
        elif shape.kind == "circle":
            cx, cy = at(p[0], p[1])
            canvas.circle(cx, cy, p[2] * scale)
        else:
            tx, ty = at(p[0], p[1])
            _text(canvas, tx, ty, shape.text or "", 5)
    canvas.setLineWidth(0.3)
    anchors = {a.terminal: a for a in symbol.anchors} if part.owner_kind == "component" else {}
    for terminal_id, y in rows_y.items():
        anchor = anchors.get(terminal_id)
        if anchor is None:
            # No anchor for this terminal in the symbol data: joined to the
            # symbol's edge, so the row is still drawn, not guessed onto a pin.
            canvas.saveState()
            canvas.setDash(1, 1.5)
            canvas.line(box_left + _SYMBOL_WIDTH, y, point_x, y)
            canvas.restoreState()
            continue
        ax, ay = at(anchor.x, anchor.y)
        canvas.line(ax, ay, box_left + _SYMBOL_WIDTH + 1 * mm, y)
        canvas.line(box_left + _SYMBOL_WIDTH + 1 * mm, y, point_x, y)


def _part(canvas: Canvas, part: layout.BlockPart, here: int) -> None:
    left = _column_x(part.place.column)
    right = left + _COLUMN - 2 * mm
    top = _slot_y(part.place.slot)
    _text(canvas, left + 2 * mm, top - 2.6 * mm, part.title, 7, bold=True, room=_COLUMN * 0.45)
    notes = "; ".join(part.notes)
    if part.continued_from is not None:
        notes = f"continued from {part.continued_from.reference(here)}; " + notes
    _text(canvas, left + 2 * mm, top - 5.2 * mm, notes, 5, room=_COLUMN - 4 * mm)
    if part.continues_on is not None:
        _text(
            canvas,
            right,
            top - 2.6 * mm,
            f"continues on {part.continues_on.reference(here)}",
            5.5,
            bold=True,
            right=True,
        )
    lines_top = top - _SLOT
    bottom = lines_top - max(len(part.lines), 1) * _SLOT
    point_x = left + _POINT
    text_x = point_x + 10 * mm
    room = right - text_x
    spans: dict[tuple[str, str, str], list[float]] = {}
    for n, line in enumerate(part.lines):
        y_top = lines_top - n * _SLOT
        y_mid = y_top - _SLOT / 2
        row = line.row
        spans.setdefault(row.key, []).append(y_mid)
        starts = n == 0 or part.lines[n - 1].row.key != row.key
        if starts:
            marking = layout.row_marking(row.terminal) + ("" if line.first else " (cont.)")
            _text(
                canvas,
                point_x - 2 * mm,
                y_mid + 0.4 * mm,
                marking,
                6,
                bold=True,
                right=True,
                room=_POINT - _SYMBOL_LEFT - _SYMBOL_WIDTH - 3 * mm,
            )
            _text(
                canvas,
                point_x - 2 * mm,
                y_mid - 2.2 * mm,
                layout.row_net(row.terminal),
                4.5,
                right=True,
                room=_POINT - _SYMBOL_LEFT - _SYMBOL_WIDTH - 3 * mm,
            )
            canvas.circle(point_x, y_mid, 0.7 * mm, stroke=1, fill=1)
        if line.kind == "note":
            text = row.notes[line.index]
            loud = text.startswith(("UNCONNECTED", "UNREALISED"))
            canvas.saveState()
            if loud:
                canvas.setFillColor(_RED)
            _text(canvas, text_x, y_mid - 1 * mm, text, 6, bold=loud, room=room)
            canvas.restoreState()
            continue
        end = row.ends[line.index]
        first, second = layout.end_texts(end, here)
        canvas.line(point_x, y_mid, text_x - 1.5 * mm, y_mid)
        canvas.line(text_x - 3 * mm, y_mid + 0.8 * mm, text_x - 1.5 * mm, y_mid)
        canvas.line(text_x - 3 * mm, y_mid - 0.8 * mm, text_x - 1.5 * mm, y_mid)
        _text(canvas, text_x, y_mid + 0.5 * mm, first, 5.5, room=room)
        loud = end.wire_missing
        canvas.saveState()
        canvas.setFillColor(_RED if loud else _GREY)
        _text(canvas, text_x, y_mid - 2.1 * mm, second, 5, bold=loud, room=room)
        canvas.restoreState()
    for ys in spans.values():
        if len(ys) > 1:
            canvas.line(point_x, max(ys), point_x, min(ys))
    rows_y = {
        line.row.terminal.terminal_id
        + ("" if part.owner_kind == "component" else f"/{line.row.terminal.side}"): y
        for line, y in _first_lines(part, lines_top)
    }
    _symbol(canvas, part, left, lines_top, bottom, rows_y)


def _first_lines(part: layout.BlockPart, lines_top: float) -> list[tuple[layout.Line, float]]:
    return [
        (line, lines_top - n * _SLOT - _SLOT / 2)
        for n, line in enumerate(part.lines)
        if n == 0 or part.lines[n - 1].row.key != line.row.key
    ]


def _wrapped(lines: Sequence[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        out.extend(textwrap.wrap(line, _TEXT_CHARS, subsequent_indent="    ") or [""])
    return out


def _structural_lines(structural: Sequence[V2Finding]) -> list[str]:
    if not structural:
        return ["No structural findings."]
    order = {"blocking": 0, "warning": 1}
    lines = []
    for found in sorted(structural, key=lambda f: (order.get(f.severity, 2), f.code)):
        where = ", ".join(f"{k}={v}" for k, v in sorted(found.location.items()))
        effects = [
            "blocks approval" if found.blocks_approval else "does not block approval",
            "blocks execution" if found.blocks_execution else "",
            "unresolved" if found.unresolved else "",
            "engineering confirmation required" if found.requires_engineering_confirmation else "",
        ]
        lines.append(
            f"[{found.severity.upper()}] {found.code}: {found.message} ({where}; "
            + "; ".join(e for e in effects if e)
            + ")"
        )
    return lines


def _text_pages(title: str, lines: Sequence[str]) -> list[tuple[str, list[str]]]:
    wrapped = _wrapped(lines) or ["(none)"]
    pages = [wrapped[i : i + _TEXT_LINES] for i in range(0, len(wrapped), _TEXT_LINES)]
    return [(title if n == 0 else f"{title} (continued)", page) for n, page in enumerate(pages)]


def _draw_text_page(canvas: Canvas, title: str, lines: Sequence[str]) -> None:
    y = _AREA_TOP - 6 * mm
    _text(canvas, _AREA_LEFT + 3 * mm, y, title, 10, bold=True)
    y -= 6 * mm
    for line in lines:
        loud = line.startswith(("[BLOCKING]", "UNREALISED", "UNCONNECTED"))
        _text(
            canvas,
            _AREA_LEFT + 3 * mm,
            y,
            line,
            6,
            bold=loud,
            room=_AREA_RIGHT - _AREA_LEFT - 6 * mm,
        )
        y -= _TEXT_LINE


def render_connection_drawing(
    drawing: layout.DrawingSet,
    header: DrawingHeader,
    mark: ExportMark,
    structural: Sequence[V2Finding],
) -> bytes:
    """The connection drawing as a PDF.

    Args:
        drawing: ``connection_drawing.drawing_set`` of the saved model.
        header: What the cover and every title block name.
        mark: The export's mark (label, identity, the design's other findings).
        structural: Every structural finding, with its effects.

    Returns:
        The PDF's bytes.
    """
    last_drawing = drawing.sheets[-1].number if drawing.sheets else layout.FIRST_SHEET - 1
    open_lines = [
        f"{item.kind}: {item.text}"
        + (f" (sheet {item.place.sheet}, zone {item.place.zone})" if item.place else "")
        for item in drawing.open_items
    ] or ["No open items."]
    index_lines = [
        f"Sheet {sheet.number}: board {sheet.board_id}: "
        + ", ".join(
            part.title + (f" (part {part.part})" if part.part > 1 else "") for part in sheet.parts
        )
        for sheet in drawing.sheets
    ] or ["No drawing sheets: the saved design holds no component or terminal strip."]
    finding_text = [
        "Structural findings of the saved model:",
        *_structural_lines(structural),
        "",
        "The design's other findings:",
        *finding_lines(mark),
    ]
    text_pages = (
        _text_pages("Open items: shown on the sheets and listed here", open_lines)
        + _text_pages("Sheet index", index_lines)
        + _text_pages("Findings", finding_text)
    )
    total = last_drawing + len(text_pages)
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=_PAGE, invariant=1)
    canvas.setTitle(f"Connection drawing - {header.project}")
    canvas.setCreator("PanelPilot")
    # Cover.
    _sheet_frame(canvas, header, mark, 1, total, "", grid=False)
    first_text = last_drawing + 1
    cover = [
        f"Project: {header.project}",
        f"Revision {header.revision}; snapshot schema {header.schema}",
        f"Design hash (SHA-256): {header.design_sha256 or layout.NOT_SET}",
        f"Rules {header.rules_version}; renderer {header.renderer_version}",
        "",
        DISCLAIMER,
        "",
        "Legend:",
        *(f"  {item}" for item in LEGEND),
        "",
        (
            f"Drawing sheets: {layout.FIRST_SHEET} to {last_drawing}"
            if drawing.sheets
            else "Drawing sheets: none"
        ),
        f"Terminals: {len(drawing.rows)}; nets: {len(drawing.nets)}; "
        f"connections: {len(drawing.connections)}; wires: {len(drawing.wire_rows)}; "
        f"open items: {len(drawing.open_items)}",
        f"Open items, sheet index and findings: sheets {first_text} to {total}",
    ]
    _draw_text_page(canvas, "Connection drawing (review only)", _wrapped(cover))
    canvas.showPage()
    for sheet in drawing.sheets:
        _sheet_frame(canvas, header, mark, sheet.number, total, sheet.board_id, grid=True)
        canvas.setLineWidth(0.3)
        for part in sheet.parts:
            _part(canvas, part, sheet.number)
        canvas.showPage()
    for n, (title, lines) in enumerate(text_pages):
        _sheet_frame(canvas, header, mark, first_text + n, total, "", grid=False)
        _draw_text_page(canvas, title, lines)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
