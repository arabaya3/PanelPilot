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
from itertools import pairwise
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.design.marking import ExportMark, finding_lines, pdf_page_stamp
from app.design.model import connection_drawing as layout
from app.design.model import connection_routing as routing
from app.design.model import links as link_rows
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

#: Added to the legend of a drawing with board-to-board links (``pp-design/3``).
LINK_END_LEGEND = (
    "LINK END: a terminal with no board-local wire (in no net) that ends a board-to-board "
    "feeder link; the note names the far board and terminal. The link is not a wire or net "
    "of either board's model. A component terminal's board-local requirement (required, or "
    "requiredness unresolved) is not met by the link and is listed separately as UNCONNECTED."
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
    "B1/X:1: a far end on another board, and every far end on a sheet showing more than one "
    "board, is named with its board.",
    "Bar joining a strip point's two sides, 'bridge between the sides: <via> per "
    "connection pattern <ref>' (or 'per model' where no pattern declared it): a connection "
    "the model records between the point's sides; nothing more is asserted.",
    "Leads (thin solid lines, all one width) run from a symbol's pins to their points without "
    "crossing, at least 2 mm apart; where that is not possible no lead is drawn and the block "
    "says so. A pin's marking is repeated at the symbol where it fits; the block names any "
    "shown at the rows only.",
    "Dotted stub (8 mm, round dots): the terminal has no pin on the symbol (or no symbol is "
    "defined); its row carries its marking.",
    "Dash-dot stub (8 mm, long dash and dot): the terminal has a pin on the symbol, but no lead "
    "is drawn (the block's first part says why); its row carries its marking.",
    "Filled dot: a terminal point; larger filled dot: a junction, a terminal with two or more "
    "connections (lines meet only there).",
    "Routed sheets (after the cross-reference sheets): line paths are schematic. Each line is "
    "exactly one saved connection between its two terminal points; it is not the physical cable "
    "path, routing, length or landing order, and no line is drawn for a net membership alone.",
    f"Routed lines: solid {routing.LINE_WIDTH:g} pt for {' and '.join(routing.SOLID_VIAS)} "
    f"connections; dashed {routing.LINE_WIDTH:g} pt for {', '.join(routing.DASHED_VIAS)} "
    "connections.",
    "w-xxxxxx / x-xxxxxx beside a line: its wire id (or, with no wire record, its connection id), "
    "shortened to a prefix no other id shares; the cross-reference sheets give it in full.",
    "Hop: where two lines cross without a connection, the horizontal one bridges the vertical "
    "one; a crossing never has a dot.",
    "Flag at the frame edge, 'to <board>/<label> (sheet n, zone XY)': the line continues on "
    "that sheet, which has the matching flag.",
    "Short stub with an arrowhead and '<label> not routed' on a routed sheet: no route was found "
    "within the fixed limits; its cross-references stay and it is listed as an unrouted open "
    "item with the reason.",
    "'symbol unreviewed: its drawing asserts nothing': a symbol is only data until its template "
    "is reviewed.",
    "A block continued in another column or on another sheet is drawn with its symbol on its "
    "first part only.",
    "Thick bar beside two points: a bridge between a strip point's two sides.",
    "...[n]: a value too long for its room, printed in full as [n] on the full-text sheets.",
    f"Text is never smaller than {layout.BODY_PT:g} pt on the cross-reference sheets (grey "
    f"wire and net detail lines {layout.DETAIL_PT:g} pt; pin markings and a symbol's own text "
    f"at a symbol {layout.SECONDARY_PT:g} pt), and "
    f"{routing.BODY_PT:g} pt ({routing.SECONDARY_PT:g} pt grey) on the routed sheets: a "
    "product readability target at A3, not a drawing standard.",
)

#: Stub patterns (PDF dash arrays, in points): clearly different when printed.
DOTTED = (0.6, 2.4)
DASH_DOT = (6.0, 2.0, 0.6, 2.0)
#: The "symbol not defined" placeholder outline: far enough from the dotted stub to tell
#: apart when an A3 sheet is printed fit-to-page on A4.
PLACEHOLDER_DASH = (3.0, 2.0)
#: Routed lines of the dashed vias (connection_routing.DASHED_VIAS).
LINK_DASH = (5.0, 2.0)

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
_LOUD = ("[BLOCKING]", "UNREALISED", "UNCONNECTED", layout.LINK_END_TAG)


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
        canvas.setDash(list(PLACEHOLDER_DASH))
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
    canvas.setDash(list(DOTTED))
    for dotted in part.dotted:
        _path(canvas, dotted)
    canvas.setDash(list(DASH_DOT))
    for pinned in part.pinned:
        _path(canvas, pinned)
    canvas.restoreState()
    junctions = set(part.junctions)
    for x, y in part.points:
        r = routing.JUNCTION_R if (x, y) in junctions else routing.DOT_R
        canvas.circle(x, y, r, stroke=0, fill=1)
    for text in part.texts:
        canvas.saveState()
        if text.tone == "loud":
            canvas.setFillColor(_RED)
        elif text.tone == "grey":
            canvas.setFillColor(_GREY)
        _text(canvas, text.x, text.y, text.text, text.size, bold=text.bold, right=text.right)
        canvas.restoreState()


def _routed(canvas: Canvas, sheet: routing.RoutedSheet) -> None:
    """A routed sheet, exactly as laid out: boxes, lines, stubs, dots, then texts."""
    canvas.saveState()
    canvas.setLineWidth(0.5)
    for box in sheet.boxes:
        canvas.rect(*box.rect)
        if box.placeholder is not None:
            canvas.saveState()
            canvas.setLineWidth(0.3)
            canvas.setDash(list(PLACEHOLDER_DASH))
            canvas.rect(*box.placeholder)
            canvas.restoreState()
    canvas.setLineWidth(routing.LINE_WIDTH)
    canvas.setLineCap(0)
    for line in sheet.lines:
        canvas.saveState()
        if line.dashed:
            canvas.setDash(list(LINK_DASH))
        path = canvas.beginPath()
        path.moveTo(*line.start)
        x, y = line.start
        for op, nx, ny in line.path:
            if op == "L":
                path.lineTo(nx, ny)
            else:
                rise = 1.0 * mm
                path.curveTo(x, y + rise, nx, ny + rise, nx, ny)
            x, y = nx, ny
        canvas.drawPath(path, stroke=1, fill=0)
        canvas.restoreState()
        if line.marker:
            flag = canvas.beginPath()
            flag.moveTo(*line.marker[0])
            for point in line.marker[1:]:
                flag.lineTo(*point)
            flag.close()
            canvas.drawPath(flag, stroke=0, fill=1)
    canvas.setLineWidth(0.3)
    for stub in sheet.stubs:
        _path(canvas, stub.points)
        for a, b in stub.arrow:
            canvas.line(*a, *b)
    for (x, y), r in sheet.dots:
        canvas.circle(x, y, r, stroke=0, fill=1)
    canvas.restoreState()
    for text in sheet.texts:
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


#: How many links one link sheet holds (M7a, ``pp-design/3``).
LINKS_PER_SHEET = 4

#: What every link sheet says of itself.
LINK_SHEET_NOTE = (
    "Board-to-board links, as the user entered them. A continuation flag stands at each end; "
    "its far-board text comes from the link. A solid flag with a dot is a modelled terminal; "
    'a dashed open flag reads "terminal NOT SET" and is not a connected terminal. A quoted '
    "label is the user's text, not a modelled terminal. Attributes are the user's or "
    '"not given"; no value is copied from the design, and none changes the calculation.'
)


def _flag(
    canvas: Canvas, x: float, y: float, width: float, *, pointing_right: bool, resolved: bool
) -> None:
    """A continuation flag; dashed and open on its terminal side when the end is unresolved."""
    h = 7 * mm
    tip = 4 * mm
    if pointing_right:
        pts = [
            (x, y),
            (x + width - tip, y),
            (x + width, y + h / 2),
            (x + width - tip, y + h),
            (x, y + h),
        ]
    else:
        pts = [(x + width, y), (x + tip, y), (x, y + h / 2), (x + tip, y + h), (x + width, y + h)]
    canvas.saveState()
    canvas.setLineWidth(0.6)
    if resolved:
        path = canvas.beginPath()
        path.moveTo(*pts[0])
        for point in pts[1:]:
            path.lineTo(*point)
        path.close()
        canvas.drawPath(path, stroke=1, fill=0)
        dot_x = x if pointing_right else x + width
        canvas.circle(dot_x, y + h / 2, 0.9 * mm, stroke=1, fill=1)
    else:
        canvas.setDash(*PLACEHOLDER_DASH)
        canvas.setStrokeColor(_RED)
        # Open on the terminal side: no edge where a terminal would connect.
        for a, b in pairwise(pts):
            canvas.line(a[0], a[1], b[0], b[1])
    canvas.restoreState()


def _link_block(
    canvas: Canvas, row: link_rows.LinkRow, top: float, drawing: layout.DrawingSet
) -> None:
    """One link: a flag at each end, the link between them, its attributes and differences."""
    left = _AREA_LEFT + 8 * mm
    right = _AREA_RIGHT - 8 * mm
    flag_w = 95 * mm
    y = top - 14 * mm
    _text(
        canvas,
        left,
        top - 5 * mm,
        f"Link {row.link_id}: {row.supply_board} to {row.fed_board} "
        f"(feeder circuit {row.circuit_id}; {row.carrier})",
        layout.BODY_PT + 1,
        bold=True,
    )
    ends = (
        (row.ends[0], left, True, row.fed_board),
        (row.ends[1], right - flag_w, False, row.supply_board),
    )
    for end, x, pointing_right, far in ends:
        _flag(canvas, x, y, flag_w, pointing_right=pointing_right, resolved=end.resolved)
        inner = x + (3 * mm if pointing_right else 6 * mm)
        room = flag_w - 9 * mm
        if end.resolved:
            text = _fit(
                f"{end.board} {end.terminal}", room, layout.BODY_PT, False, "(in the wire schedule)"
            )
            _text(canvas, inner, y + 2.5 * mm, text)
        else:
            canvas.saveState()
            canvas.setFillColor(_RED)
            _text(canvas, inner, y + 2.5 * mm, f"{end.board} {end.terminal}", bold=True)
            canvas.restoreState()
        lines = [f"{end.role} end; continues to {far}"]
        if end.key is not None and end.key in drawing.places:
            lines.append(f"terminal drawn at {layout.where(drawing.places[end.key])}")
        if end.label is not None:
            lines += layout.wrap(end.label, flag_w, layout.BODY_PT)
        ly = y - 3.4 * mm
        for line in lines[:4]:
            _text(canvas, x, ly, line)
            ly -= _TEXT_LINE
    canvas.saveState()
    canvas.setDash(*LINK_DASH)
    canvas.setLineWidth(0.4)
    mid = y + 3.5 * mm
    canvas.line(left + flag_w + 2 * mm, mid, right - flag_w - 2 * mm, mid)
    canvas.restoreState()
    attrs = "; ".join(f"{link_rows.ATTRIBUTE_NAMES[n]}: {t}" for n, t in row.attributes)
    diffs = [link_rows.difference_text(*d) for d in row.differences]
    ty = y - 3.4 * mm - 5 * _TEXT_LINE
    for line in layout.wrap(attrs, _TEXT_ROOM, layout.BODY_PT)[:3]:
        _text(canvas, left, ty, line)
        ty -= _TEXT_LINE
    for diff in diffs:
        for line in layout.wrap(f"DIFFERS: {diff}", _TEXT_ROOM, layout.BODY_PT)[:2]:
            _text(canvas, left, ty, line, bold=True)
            ty -= _TEXT_LINE


def _link_sheet(
    canvas: Canvas, rows: Sequence[link_rows.LinkRow], drawing: layout.DrawingSet
) -> None:
    x = _AREA_LEFT + _TEXT_MARGIN
    _text(canvas, x, _AREA_TOP - 6 * mm, "Board-to-board links", 10, bold=True)
    for n, line in enumerate(layout.wrap(LINK_SHEET_NOTE, _TEXT_ROOM, layout.BODY_PT)[:3]):
        _text(canvas, x, _AREA_TOP - 10 * mm - n * _TEXT_LINE, line)
    band = (_AREA_TOP - 22 * mm - _AREA_BOTTOM) / LINKS_PER_SHEET
    for n, row in enumerate(rows):
        _link_block(canvas, row, _AREA_TOP - 20 * mm - n * band, drawing)


def _routed_counts(routed: routing.RoutedSet) -> list[str]:
    reasons: dict[str, int] = {}
    for item in routed.unrouted:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    by = "; ".join(f"{n}: {reason}" for reason, n in sorted(reasons.items()))
    return [
        f"Connections drawn as lines on one sheet: {len(routed.routed)}; continued across "
        f"sheets with matching flags: {len(routed.continued)}; not routed: "
        f"{len(routed.unrouted)}" + (f" ({by})" if by else ""),
        routing.SCHEMATIC_NOTE,
    ]


def render_connection_drawing(
    drawing: layout.DrawingSet,
    header: DrawingHeader,
    mark: ExportMark,
    structural: Sequence[V2Finding],
    links: Sequence[link_rows.LinkRow] = (),
) -> bytes:
    """The connection drawing as a PDF.

    Args:
        drawing: ``connection_drawing.drawing_set`` of the saved model.
        header: What the cover and every title block name.
        mark: The export's mark (label, identity, the design's other findings).
        structural: Every structural finding, with its effects.
        links: A ``pp-design/3`` revision's links (``links.rows``), drawn on
            link sheets after the routed sheets; none for v2, whose drawing
            is then unchanged.

    Returns:
        The PDF's bytes.
    """
    routed = drawing.routed
    routed_sheets = routed.sheets if routed is not None else ()
    last_drawing = drawing.sheets[-1].number if drawing.sheets else layout.FIRST_SHEET - 1
    last_m4 = last_drawing
    if routed_sheets:
        last_drawing = routed_sheets[-1].number
    last_routed = last_drawing
    link_pages = [
        list(links[n : n + LINKS_PER_SHEET]) for n in range(0, len(links), LINKS_PER_SHEET)
    ]
    last_drawing += len(link_pages)
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
    index_items += [
        f"Sheet {sheet.number}: routed sheet, board {sheet.board_id}: {len(sheet.lines)} line(s)"
        for sheet in routed_sheets
    ]
    index_items += [
        f"Sheet {last_routed + 1 + n}: board-to-board links: "
        + ", ".join(f"{r.supply_board} to {r.fed_board}" for r in page)
        for n, page in enumerate(link_pages)
    ]
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
        routing.PRINT_NOTE,
        "",
        "Legend:",
        *(f"  {item}" for item in LEGEND),
        *([f"  {LINK_END_LEGEND}"] if links else []),
        "",
        (
            f"Cross-reference sheets: {layout.FIRST_SHEET} to {last_m4}"
            if drawing.sheets
            else "Drawing sheets: none"
        ),
        (
            f"Routed sheets: {routed_sheets[0].number} to {last_routed}"
            if routed_sheets
            else "Routed sheets: none"
        ),
        *(_routed_counts(routed) if routed is not None else []),
        *(
            [
                f"Board-to-board link sheets: {last_routed + 1} to {last_drawing} ({len(links)} link(s))"
            ]
            if link_pages
            else []
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
    for sheet in routed_sheets:
        _sheet_frame(canvas, header, mark, sheet.number, total, sheet.board_id, grid=True)
        _routed(canvas, sheet)
        canvas.showPage()
    for n, page in enumerate(link_pages):
        _sheet_frame(canvas, header, mark, last_routed + 1 + n, total, "", grid=True)
        _link_sheet(canvas, page, drawing)
        canvas.showPage()
    for n, (title, lines) in enumerate(text_pages):
        _sheet_frame(canvas, header, mark, first_text + n, total, "", grid=False)
        _draw_text_page(canvas, title, lines)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
