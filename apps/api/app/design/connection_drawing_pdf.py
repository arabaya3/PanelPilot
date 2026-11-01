"""A review-only connection drawing of a saved ``pp-design/2`` revision, as PDF (M4).

Draws a :class:`~app.design.model.connection_drawing.DrawingSet` and decides
nothing about it: what is on each sheet, every cross-reference and every
printed value come from the layout (``app.design.model.connection_drawing``),
which takes them from the saved model alone. Symbols are drawn from the
template's symbol data; where there is none, a dashed placeholder box says
"symbol not defined".

A3 landscape. Sheet 1 is the cover; the drawing sheets follow, then the
open-items, full-text, sheet-index and findings sheets. Every text, point,
stub and lead of a drawing sheet is drawn exactly where the layout put it;
nothing is shrunk or cut here. Every sheet has a frame, a zone
grid, a title block naming the project, revision, sheet "n of N", board and
the full snapshot hash, and the export's mark at its top and foot with
:data:`DRAWING_LABEL` beside it, in the schedules' red.

A connection drawing is review content only: it is not an execution
document, and it states or checks no rating, cross-section, colour,
numbering convention, electrical compatibility or standard.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.design.marking import ExportMark, finding_lines, pdf_page_stamp
from app.design.model import connection_drawing as layout
from app.design.model.entities import SymbolDef
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
    "to X:1 (sheet n, zone 3B) via wire: the far end of a connection and where it is drawn.",
    "wire <id>: no. / mm² / colour: each value with its source [user] [data], "
    "'<value> assumed (<assumption>)', or 'not set' where the saved design holds none.",
    "UNCONNECTED: a terminal in no net (required, requiredness unresolved, or a strip point).",
    "UNREALISED: a net whose own connections do not join all its members.",
    "WIRE RECORD MISSING: a connection via wire with no wire record.",
    f"Dashed box '{layout.SYMBOL_NOT_DEFINED}': the template defines no symbol data.",
    "Zones: columns 1-8 across, rows A-F down; a terminal's zone is read where its point is "
    "drawn, a block's where its title starts.",
    "B1/X:1: a far end on another board is named with its board.",
    "Bar joining a strip point's two sides, 'bridge between the sides: <via> per "
    "connection pattern <ref>' (or 'per model' where no pattern declared it): a connection "
    "the model records between the point's sides; nothing more is asserted.",
    "Leads (thin solid lines, all one width) run from a symbol's pins to their points without "
    "crossing, at least 2 mm apart; where that is not possible no lead is drawn and the block "
    "says so. A pin's marking is repeated at the symbol where it fits; the block names any "
    "shown at the rows only.",
    "Dotted stub: the terminal has no pin on the symbol (or no symbol is defined); its row "
    "carries its marking.",
    "Dash-dot stub: the terminal has a pin on the symbol, but no lead is drawn (the block's "
    "first part says why); its row carries its marking.",
    "A block continued in another column or on another sheet is drawn with its symbol on its "
    "first part only.",
    "Thick bar beside two points: a bridge between a strip point's two sides.",
    "...[n]: a value too long for its room, printed in full as [n] on the full-text sheets.",
    f"Text is never smaller than {layout.BODY_PT:g} pt ({layout.SECONDARY_PT:g} pt for grey "
    "detail lines and pin markings at a symbol): a product readability target, not a "
    "drawing standard.",
)

_RED = colors.HexColor("#b00020")
_GREY = colors.HexColor("#555555")

_PAGE: tuple[float, float] = layout.PAGE
_MARGIN = layout.MARGIN
_BAND = layout.BAND
_AREA_LEFT = layout.AREA_LEFT
_AREA_RIGHT = layout.AREA_RIGHT
_AREA_TOP = layout.AREA_TOP
_AREA_BOTTOM = layout.AREA_BOTTOM
_ZONE = layout.ZONE
_TITLE_LEFT = _PAGE[0] - _MARGIN - 180 * mm
_TITLE_BOTTOM = 15 * mm
_TITLE_TOP = _AREA_BOTTOM - _BAND - 1 * mm
#: Text sheets: lines at the body size, kept inside the inner frame with a margin.
_TEXT_MARGIN = 4 * mm
_TEXT_LINE = 3.2 * mm
_TEXT_FIRST = _AREA_TOP - 12 * mm
_TEXT_LAST = _AREA_BOTTOM + _TEXT_MARGIN
_TEXT_LINES = int((_TEXT_FIRST - _TEXT_LAST) // _TEXT_LINE) + 1
_TEXT_ROOM = _AREA_RIGHT - _AREA_LEFT - 2 * _TEXT_MARGIN
_INDENT = 4 * mm
#: Text-sheet entries printed bold.
_LOUD = ("[BLOCKING]", "UNREALISED", "UNCONNECTED")


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
    size: float = layout.BODY_PT,
    *,
    bold: bool = False,
    right: bool = False,
) -> None:
    """Draws a text as given: never shrunk, never cut (the layout wraps it)."""
    font, drawn = font_for(text, bold=bold, right_to_left=has_rtl(text))
    canvas.setFont(font, size)
    if right:
        canvas.drawRightString(x, y, drawn)
    else:
        canvas.drawString(x, y, drawn)


def _fit(text: str, room: float, size: float, bold: bool, note: str) -> str:
    """``text`` if it fits; otherwise cut with an ellipsis and ``note`` saying where it is in full."""
    if layout.text_width(text, size, bold) <= room:
        return text
    tail = f"{layout.ELLIPSIS} {note}"
    while text and layout.text_width(text + tail, size, bold) > room:
        text = text[:-1]
    return text + tail


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
        step = layout.ZONE_ROW
        for n in range(layout.ZONE_ROWS):
            y = _AREA_TOP - n * step
            if n:
                canvas.line(_MARGIN, y, _AREA_LEFT, y)
                canvas.line(_AREA_RIGHT, y, width - _MARGIN, y)
            letter = layout.ZONE_LETTERS[n]
            _text(canvas, _MARGIN + 1.2 * mm, y - step / 2, letter, 6)
            _text(canvas, _AREA_RIGHT + 1.2 * mm, y - step / 2, letter, 6)
    # Title block: every line at the body size or larger.
    canvas.setLineWidth(0.6)
    right_edge = width - _MARGIN
    canvas.rect(_TITLE_LEFT, _TITLE_BOTTOM, right_edge - _TITLE_LEFT, _TITLE_TOP - _TITLE_BOTTOM)
    x = _TITLE_LEFT + 2 * mm
    room = right_edge - x - 2 * mm
    y = _TITLE_TOP - 4 * mm
    _text(canvas, x, y, "Connection drawing (review only)", 8, bold=True)
    _text(canvas, right_edge - 2 * mm, y, f"Sheet {number} of {total}", 8, bold=True, right=True)
    y -= 4 * mm
    project = layout.wrap(f"Project: {header.project}", room, 6.5)
    if len(project) > 2:
        project = [
            project[0],
            _fit(" ".join(project[1:]), room, 6.5, False, "(in full on the cover)"),
        ]
    lines = [*project]
    lines.append(
        f"Revision {header.revision}; schema {header.schema}; rules {header.rules_version}"
    )
    lines.append(
        _fit(
            f"Renderer {header.renderer_version}; board {board or '-'}",
            room,
            layout.BODY_PT,
            False,
            "(see the sheet index)",
        )
    )
    lines.append(f"Snapshot SHA-256: {header.design_sha256 or layout.NOT_SET}")
    for n, text in enumerate(lines):
        _text(canvas, x, y, text, 6.5 if n < len(project) else layout.BODY_PT)
        y -= 3.4 * mm
    canvas.saveState()
    canvas.setFillColor(_RED)
    _text(canvas, x, _TITLE_BOTTOM + 1.6 * mm, "REVIEW ONLY — NOT FOR EXECUTION", 7, bold=True)
    canvas.restoreState()


def _path(canvas: Canvas, points: Sequence[tuple[float, float]]) -> None:
    path = canvas.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    canvas.drawPath(path, stroke=1, fill=0)


def _symbol(canvas: Canvas, symbol: SymbolDef, frame: layout.SymbolFrame) -> None:
    """A symbol from its data, clipped to its own box."""
    x0, y0, x1, y1 = frame.box(symbol)
    scale = frame.scale

    def at(x: int, y: int) -> tuple[float, float]:
        return x0 + x * scale, y1 - y * scale

    canvas.saveState()
    clip = canvas.beginPath()
    clip.rect(x0, y0, x1 - x0, y1 - y0)
    canvas.clipPath(clip, stroke=0, fill=0)
    canvas.setLineWidth(0.5)
    for shape in symbol.shapes:
        p = shape.points
        if shape.kind == "line":
            (lx1, ly1), (lx2, ly2) = at(p[0], p[1]), at(p[2], p[3])
            canvas.line(lx1, ly1, lx2, ly2)
        elif shape.kind == "rect":
            rx, ry = at(p[0], p[1] + p[3])
            canvas.rect(rx, ry, p[2] * scale, p[3] * scale)
        elif shape.kind == "circle":
            cx, cy = at(p[0], p[1])
            canvas.circle(cx, cy, p[2] * scale)
        else:
            tx, ty = at(p[0], p[1])
            canvas.setFillColor(_GREY)  # symbol text is secondary detail
            _text(canvas, tx, ty, shape.text or "", layout.SECONDARY_PT)
    canvas.restoreState()


def _part(canvas: Canvas, part: layout.BlockPart) -> None:
    """A block part, exactly as laid out."""
    canvas.setLineWidth(0.3)
    if part.placeholder is not None:
        canvas.saveState()
        canvas.setDash(2, 2)
        canvas.rect(*part.placeholder)
        canvas.restoreState()
    if part.symbol is not None and part.frame is not None:
        _symbol(canvas, part.symbol, part.frame)
    canvas.setLineWidth(0.3)
    for lead in part.leads:
        _path(canvas, lead.points)
    for stub in part.stubs:
        _path(canvas, stub)
        x, y = stub[-1]
        canvas.line(x - 1.5 * mm, y + 0.8 * mm, x, y)
        canvas.line(x - 1.5 * mm, y - 0.8 * mm, x, y)
    for bus in part.buses:
        _path(canvas, bus)
    canvas.setLineWidth(1.0)
    for bridge in part.bridges:
        _path(canvas, bridge)
    canvas.setLineWidth(0.3)
    canvas.saveState()
    canvas.setDash(1, 1.5)
    for dotted in part.dotted:
        _path(canvas, dotted)
    canvas.setDash([3, 1, 0.6, 1])
    for pinned in part.pinned:
        _path(canvas, pinned)
    canvas.restoreState()
    for x, y in part.points:
        canvas.circle(x, y, 0.7 * mm, stroke=1, fill=1)
    for text in part.texts:
        canvas.saveState()
        if text.tone == "loud":
            canvas.setFillColor(_RED)
        elif text.tone == "grey":
            canvas.setFillColor(_GREY)
        _text(canvas, text.x, text.y, text.text, text.size, bold=text.bold, right=text.right)
        canvas.restoreState()


def _items(texts: Sequence[str]) -> list[list[str]]:
    """Each item wrapped, whole, to the text sheets' width; continuation lines indented."""
    out: list[list[str]] = []
    for text in texts:
        if not text:
            out.append([""])
            continue
        # A loud entry's first line is printed bold, so it is measured bold.
        loud = text.startswith(_LOUD)
        first = layout.wrap(text, _TEXT_ROOM, layout.BODY_PT, bold=loud)
        head, rest = first[0], " ".join(first[1:])
        lines = [head]
        if rest:
            lines += [
                "    " + line for line in layout.wrap(rest, _TEXT_ROOM - _INDENT, layout.BODY_PT)
            ]
        out.append(lines)
    return out


def _structural_items(structural: Sequence[V2Finding], drawing: layout.DrawingSet) -> list[str]:
    if not structural:
        return ["No structural findings."]
    order = {"blocking": 0, "warning": 1}
    items = []
    for found in sorted(structural, key=lambda f: (order.get(f.severity, 2), f.code)):
        ids = ", ".join(f"{k}={v}" for k, v in sorted(found.location.items()))
        at = drawing.locate(found.location)
        effects = [
            "blocks approval" if found.blocks_approval else "does not block approval",
            "blocks execution" if found.blocks_execution else "",
            "unresolved" if found.unresolved else "",
            "engineering confirmation required" if found.requires_engineering_confirmation else "",
        ]
        items.append(
            f"[{found.severity.upper()}] {found.code}: {found.message}"
            + (f" — drawn at {at}" if at else "")
            + f" (ids: {ids}; "
            + "; ".join(e for e in effects if e)
            + ")"
        )
    return items


def _other_findings(mark: ExportMark) -> list[str]:
    items: list[str] = []
    for line in finding_lines(mark):
        if line.startswith("    ") and items:
            items[-1] += " " + line.strip()
        else:
            items.append(line)
    return items


def _marked_cuts(items: Sequence[str]) -> list[str]:
    """Each finding a message of which quotes a shortened value ("…"), marked ``[Fn]``.

    A finding's message shortens a long id or value; the marker refers to a
    line under it saying where the value is in full (its ids, and the
    drawing sheets), so nothing is cut silently.
    """
    out: list[str] = []
    n = 0
    for item in items:
        if layout.ELLIPSIS not in item:
            out.append(item)
            continue
        n += 1
        out.append(item.replace(layout.ELLIPSIS, f"{layout.ELLIPSIS}[F{n}]"))
        out.append(
            f"[F{n}] a value this finding's message shortens is printed in full in its ids "
            "above and on the drawing sheets."
        )
    return out


def _text_pages(title: str, items: Sequence[str]) -> list[tuple[str, list[str]]]:
    """Text sheets holding whole items: an item never splits across sheets.

    Only an item taller than a whole sheet continues, and says so.
    """
    pages: list[list[str]] = [[]]
    for lines in _items(items or ["(none)"]):
        if pages[-1] and len(pages[-1]) + len(lines) > _TEXT_LINES:
            pages.append([])
        while len(lines) > _TEXT_LINES:
            pages[-1] = [*lines[: _TEXT_LINES - 1], "    (this item continues on the next sheet)"]
            lines = lines[_TEXT_LINES - 1 :]
            pages.append([])
        pages[-1].extend(lines)
    return [(title if n == 0 else f"{title} (continued)", page) for n, page in enumerate(pages)]


def _draw_text_page(canvas: Canvas, title: str, lines: Sequence[str]) -> None:
    x = _AREA_LEFT + _TEXT_MARGIN
    _text(canvas, x, _AREA_TOP - 6 * mm, title, 10, bold=True)
    y = _TEXT_FIRST
    for line in lines:
        loud = line.startswith(_LOUD)
        _text(canvas, x, y, line, layout.BODY_PT, bold=loud)
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
    open_items = [
        f"{item.kind}: {item.text}" + (f" ({layout.where(item.place)})" if item.place else "")
        for item in drawing.open_items
    ] or ["No open items."]
    full_texts = [f"[{n}] {text}" for n, text in enumerate(drawing.full_texts, start=1)]
    index_items = [
        f"Sheet {sheet.number}: board{'s' if len(sheet.boards) > 1 else ''} "
        f"{', '.join(sheet.boards)}: "
        + ", ".join(
            part.title + (f" (part {part.part})" if part.part > 1 else "") for part in sheet.parts
        )
        for sheet in drawing.sheets
    ] or ["No drawing sheets: the saved design holds no component or terminal strip."]
    finding_items = _marked_cuts(
        [
            "Structural findings of the saved model:",
            *_structural_items(structural, drawing),
            "",
            "The design's other findings:",
            *_other_findings(mark),
        ]
    )
    text_pages = _text_pages("Open items: shown on the sheets and listed here", open_items)
    if full_texts:
        text_pages += _text_pages("Full texts of values cut on the drawing sheets", full_texts)
    text_pages += _text_pages("Sheet index", index_items) + _text_pages("Findings", finding_items)
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
        f"Open items, full texts, sheet index and findings: sheets {first_text} to {total}",
        (
            "Boards may share a drawing sheet (open decision D-9); each block's title names its board."
            if drawing.boards_share_sheets
            else "Each board starts a new drawing sheet (open decision D-9)."
        ),
    ]
    _draw_text_page(
        canvas, "Connection drawing (review only)", [ln for item in _items(cover) for ln in item]
    )
    canvas.showPage()
    for sheet in drawing.sheets:
        _sheet_frame(canvas, header, mark, sheet.number, total, ", ".join(sheet.boards), grid=True)
        for part in sheet.parts:
            _part(canvas, part)
        canvas.showPage()
    for n, (title, lines) in enumerate(text_pages):
        _sheet_frame(canvas, header, mark, first_text + n, total, "", grid=False)
        _draw_text_page(canvas, title, lines)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
