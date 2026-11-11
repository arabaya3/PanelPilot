"""The layout of a review-only connection drawing of a ``pp-design/2`` model (M4, M5-0).

Pure: a saved model (as read) in, a :class:`DrawingSet` out. Nothing is
rendered here (``app.design.connection_drawing_pdf`` draws a ``DrawingSet``),
nothing is read from a database or a client, and nothing is specific to a
device, a panel family or a customer: every block, row and cross-reference is
derived from the model's components, strips, terminals, nets, connections and
wires, in the order of the review schedules.

The drawing's terminals and wires *are* the schedules' rows:
:attr:`DrawingSet.terminal_rows` is ``schedules.terminal_schedule(model)`` and
:attr:`DrawingSet.wire_rows` is ``schedules.wire_schedule(model)``, so a
drawing and the two schedules of one model cannot disagree on a terminal, a
net or a wire. The texts printed for each are made here (:func:`cell_text`,
the ``*_text`` helpers) from the model's values only. A value the model does
not hold is printed as :data:`NOT_SET`; nothing is filled in or guessed.

Geometry is exact and decided here, once: every printed text, point, stub,
lead and symbol frame of a drawing sheet carries its page coordinates (PDF
points, origin bottom left), and the renderer draws exactly those. A sheet's
drawing area is :data:`COLUMNS` block columns of :data:`SLOTS` slots; a slot
holds two text lines. A block is a header, then one or more slots per line
of its rows: a text too long for its room wraps onto more lines (and so more
slots), never below the readability minimum (:data:`BODY_PT`,
:data:`SECONDARY_PT`). A block that does not fit in a column continues in the
next column or on the next sheet, and says so. Zones are computed from where
a thing is drawn (:func:`zone_at`), not from slot numbers.

Text widths are measured with the fonts the PDF uses (``pdf_fonts``), so the
wrapping decided here is the wrapping printed.
"""

from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from app.design.model import entities, schedules
from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl

#: What is printed where the model holds no value.
NOT_SET = "not set"

#: What is printed where a template defines no symbol.
SYMBOL_NOT_DEFINED = "symbol not defined"

#: The first drawing sheet's number: sheet 1 is the cover.
FIRST_SHEET = 2

# --------------------------------------------------------------------------- #
# Readability minimum: a product target, not a drawing standard (see the M4
# architecture document, "Text size"). Nothing is ever shrunk below it.
# --------------------------------------------------------------------------- #

#: Body text and labels: markings, cross-references, notes, block headers,
#: bridge lines and every line of the text sheets.
BODY_PT = 6.0
#: Secondary text only: the grey wire-detail and net lines under a
#: cross-reference or a marking, and pin markings repeated at a symbol.
SECONDARY_PT = 5.0
#: Grey detail lines under a row: wire id and values, the net (M5: raised from 5 pt
#: after the print-size review; pin markings at a symbol stay at SECONDARY_PT).
DETAIL_PT = 6.0
#: Block titles.
TITLE_PT = 7.5

# --------------------------------------------------------------------------- #
# Sheet geometry, in PDF points (A3 landscape)
# --------------------------------------------------------------------------- #

MM = 72 / 25.4
PAGE = (420 * MM, 297 * MM)
MARGIN = 12 * MM
BAND = 4 * MM
AREA_LEFT = MARGIN + BAND
AREA_RIGHT = PAGE[0] - MARGIN - BAND
AREA_TOP = PAGE[1] - 24 * MM

#: Block columns per sheet, and slots per column; a slot is two text lines.
COLUMNS = 3
SLOTS = 36
SLOT = 6.2 * MM
HALF = SLOT / 2
#: Kept clear inside the drawing area's frame, above the first slot and below the last.
PAD = 1.5 * MM
AREA_BOTTOM = AREA_TOP - SLOTS * SLOT - 2 * PAD
COLUMN = (AREA_RIGHT - AREA_LEFT) / COLUMNS

#: The zone grid: numbered columns across, lettered rows down.
ZONE_COLUMNS = 8
ZONE_ROWS = 6
ZONE_LETTERS = "ABCDEF"
ZONE = (AREA_RIGHT - AREA_LEFT) / ZONE_COLUMNS
ZONE_ROW = (AREA_TOP - AREA_BOTTOM) / ZONE_ROWS

#: Offsets in a block column, from its left edge: the symbol band and its
#: gutter of lead lanes, the terminals' points, their marking column and the
#: cross-reference text.
SYMBOL_LEFT = 2 * MM
POINT = 41 * MM
#: The lead lanes end here; a terminal's dotted or dash-dot stub fills the 9 mm
#: from here to its point (M5: at least 8 mm, so the two patterns tell apart).
GUTTER_END = POINT - 9 * MM
STUB = 8 * MM
MARK_LEFT = POINT + 2 * MM
MARK_RIGHT = POINT + 21 * MM
TEXT_LEFT = MARK_RIGHT + 4 * MM
TEXT_RIGHT = COLUMN - 2 * MM
TITLE_LEFT = 2 * MM
#: Room kept right of a block title for "continues on sheet n, zone 1A".
CONTINUES_ROOM = 42 * MM

#: Clearance kept round a symbol, and the distance between parallel leads
#: (never less: a symbol whose leads would need closer lanes is drawn with
#: no leads).
CLEAR = 1.5 * MM
LANE = 2.0 * MM
#: The smallest room a symbol with leads is given, and its largest grid unit.
SYMBOL_MIN = 6 * MM
SYMBOL_UNIT_MAX = 3 * MM

#: D-9 (open): whether boards may share a sheet. The default lets a board
#: follow the board before it on the same sheet and column, after a blank
#: slot; every block names its board in its title. ``False`` starts each
#: board on a new sheet (M4).
BOARDS_SHARE_SHEETS = True


def zone_at(x: float, y: float) -> str:
    """The zone of a page point, by the printed grid, e.g. ``"3B"``."""
    number = min(max(int((x - AREA_LEFT) // ZONE), 0), ZONE_COLUMNS - 1) + 1
    letter = min(max(int((AREA_TOP - y) // ZONE_ROW), 0), ZONE_ROWS - 1)
    return f"{number}{ZONE_LETTERS[letter]}"


def text_width(text: str, size: float, bold: bool = False) -> float:
    """A text's printed width, in the font the PDF draws it with."""
    font, drawn = font_for(text, bold=bold, right_to_left=has_rtl(text))
    return float(stringWidth(drawn, font, size))


@dataclass(frozen=True)
class Place:
    """Where something is drawn: sheet, block column, slot, and its page point.

    ``x``/``y`` is the point the zone is read at: a terminal's connection
    point, or the start of a block header's title.
    """

    sheet: int
    column: int
    slot: int
    x: float = 0.0
    y: float = 0.0

    @property
    def zone(self) -> str:
        """The zone of this place's point, e.g. ``"2B"``."""
        return zone_at(self.x, self.y)

    @property
    def header_zone(self) -> str:
        """The zone of a header's place: its title's point (same as :attr:`zone`)."""
        return self.zone

    def reference(self, here: int, *, header: bool = False) -> str:
        """How a cross-reference to this place reads from sheet ``here``."""
        del header
        if self.sheet == here:
            return f"this sheet, zone {self.zone}"
        return f"sheet {self.sheet}, zone {self.zone}"


@dataclass(frozen=True)
class End:
    """One end of a connection, as drawn at the terminal it starts from.

    Attributes:
        connection_id: The connection.
        via: How it is made (data).
        net_id: Its net.
        signal: The net's signal label (data).
        far_key: The other end's ``TerminalRef.key``.
        far_label: The other end's label: the schedules' label, with a strip
            point's side and level.
        far_place: Where the other end is drawn.
        wire: The wire schedule's row, for a connection with a wire record.
        wire_missing: ``True`` for a ``via: wire`` connection with no wire record.
        core: The cable core, for ``via: cable_core``.
        far_board: The other end's board.
        qualified: Whether the far end is named with its board: it is on another
            board, or the end's sheet shows more than one board; its label is
            printed with its board (``<board>/<label>``).
        pattern_ref: The pattern that declared the connection, or ``None``.
        bridge: Whether it joins a strip point's two sides (drawn with a
            bridge mark).
    """

    connection_id: str
    via: str
    net_id: str
    signal: str
    far_key: tuple[str, str, str]
    far_label: str
    far_place: Place
    wire: schedules.WireRow | None
    wire_missing: bool
    core: entities.CableCore | None
    far_board: str = ""
    qualified: bool = False
    pattern_ref: str | None = None
    bridge: bool = False


@dataclass(frozen=True)
class Row:
    """One terminal row of a block: the terminal schedule's row, its ends and notes.

    ``notes`` are what the row must say before its ends: unconnected,
    unrealised, alone in its net. ``place`` is its connection point.
    """

    terminal: schedules.TerminalRow
    key: tuple[str, str, str]
    label: str
    place: Place
    notes: tuple[str, ...]
    ends: tuple[End, ...]


@dataclass(frozen=True)
class Text:
    """One printed text line, exactly where it is printed.

    ``x``/``y`` is the baseline start (its end when ``right``). ``role`` says
    what it is (``title``, ``header``, ``continues``, ``marking``, ``net``,
    ``far``, ``detail``, ``note``, ``member``, ``pin``,
    ``placeholder``); ``key`` and ``index`` say which row and which end or
    note. A value wrapped over several lines is several texts with the same
    role, key and index, in reading order. ``tone`` is ``plain``, ``grey`` or
    ``loud`` (red).
    """

    x: float
    y: float
    text: str
    size: float
    bold: bool = False
    tone: str = "plain"
    right: bool = False
    role: str = ""
    key: tuple[str, str, str] | None = None
    index: int = -1

    @property
    def width(self) -> float:
        """Its printed width."""
        return text_width(self.text, self.size, self.bold)


@dataclass(frozen=True)
class Line:
    """One line of a block part: a row's note or one of its ends.

    ``slot`` is its first slot in its column and ``span`` how many it takes
    (a wrapped text takes more than one).
    """

    row: Row
    kind: str  # "note", "end"
    index: int
    first: bool
    slot: int = 0
    span: int = 1


Point = tuple[float, float]
Path = tuple[Point, ...]


@dataclass(frozen=True)
class Lead:
    """An orthogonal lead from a symbol anchor to its terminal's point."""

    terminal: str
    points: Path
    box: tuple[float, float, float, float]  # x0, y0 (bottom), x1, y1 (top)


@dataclass(frozen=True)
class SymbolFrame:
    """Where a symbol is drawn: its top-left corner and points per grid unit."""

    x0: float
    y1: float
    scale: float

    def box(self, symbol: entities.SymbolDef) -> tuple[float, float, float, float]:
        """Its drawn box: x0, y0 (bottom), x1, y1 (top)."""
        return (
            self.x0,
            self.y1 - symbol.height * self.scale,
            self.x0 + symbol.width * self.scale,
            self.y1,
        )


@dataclass(frozen=True)
class BlockPart:
    """The part of a block drawn in one column of one sheet.

    Attributes:
        owner_kind: ``"component"``, ``"strip"`` or ``"no_terminals"`` (one
            board's list of components whose templates define no terminals
            and no symbol).
        owner_id: Its id (for that list, ``no-terminals:<board>``).
        title: The owner's board, name and kind.
        notes: What the header says beyond the title.
        template_ref: The template, or ``None``.
        symbol: The template's symbol, or ``None`` (a placeholder is drawn).
        place: The header's place.
        lines: The lines after the header.
        part: 1 for the first part; more for a continuation.
        continued_from: The previous part's place, or ``None``.
        continues_on: The next part's place, or ``None``.
        board_id: The owner's board.
        members: For a no-terminals list, its component ids.
        texts: Every text the part prints, at its page point.
        points: Each terminal's connection point.
        stubs: Each end's stub, from the point to its text.
        buses: Vertical joins of one terminal's several ends.
        bridges: Bridge marks: a bar joining the stubs of a strip point's two
            sides where a connection joins them.
        dotted: Dotted stubs of terminals with no pin on the symbol.
        pinned: Dash-dot stubs of terminals with a pin on the symbol but no
            lead drawn (the block's first part says why).
        leads: Routed leads from the symbol's anchors.
        frame: Where the symbol is drawn, or ``None``.
        placeholder: The dashed "symbol not defined" box (x, y, w, h), or ``None``.
        top: The part's top; ``bottom`` its bottom; ``left`` and ``right``
            its edges (a block may take several block columns).
    """

    owner_kind: str
    owner_id: str
    title: str
    notes: tuple[str, ...]
    template_ref: str | None
    symbol: entities.SymbolDef | None
    place: Place
    lines: tuple[Line, ...]
    part: int
    continued_from: Place | None
    continues_on: Place | None
    board_id: str = ""
    members: tuple[str, ...] = ()
    texts: tuple[Text, ...] = ()
    points: tuple[Point, ...] = ()
    stubs: tuple[Path, ...] = ()
    buses: tuple[Path, ...] = ()
    bridges: tuple[Path, ...] = ()
    dotted: tuple[Path, ...] = ()
    pinned: tuple[Path, ...] = ()
    leads: tuple[Lead, ...] = ()
    frame: SymbolFrame | None = None
    placeholder: tuple[float, float, float, float] | None = None
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0
    pins_dropped: tuple[str, ...] = ()
    #: The points of terminals with two or more connections: drawn as junction dots.
    junctions: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Sheet:
    """A drawing sheet: its number, its first board, every board on it, and its parts."""

    number: int
    board_id: str
    parts: tuple[BlockPart, ...]
    boards: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenItem:
    """Something the drawing shows as not resolved, listed again on its own sheets.

    ``kind`` is one of :data:`OPEN_KINDS`; ``place`` is where it is drawn, or
    ``None`` for an item about a whole definition. A grouped item names every
    member in ``members`` and in its text.
    """

    kind: str
    board_id: str
    subject: str
    text: str
    place: Place | None
    members: tuple[str, ...] = ()


#: What a modelled link end's terminal note starts with when the terminal has
#: no board-local wire (no net): it takes part in a board-to-board feeder
#: link, so it is not simply "unconnected". The link is no wire or net of the
#: board's own model, and none is implied.
LINK_END_TAG = "LINK END"


#: The kinds of open item, in the order they are listed.
OPEN_KINDS = (
    "unrealised_net",
    "unconnected_required",
    "unconnected_unresolved",
    "unconnected_not_required",
    "unconnected_strip_point",
    "strip_point_link_end",
    "terminal_link_end",
    "wire_record_missing",
    "wire_attribute_unset",
    "wire_attribute_assumed",
    "designation_not_set",
    "symbol_not_defined",
    "leads_not_drawn",
    "no_terminals_defined",
    "unrouted",
)


@dataclass(frozen=True)
class DrawingSet:
    """A whole connection drawing, laid out. See the module docstring.

    Attributes:
        sheets: The drawing sheets, numbered from :data:`FIRST_SHEET`.
        terminal_rows: ``schedules.terminal_schedule(model)``.
        wire_rows: ``schedules.wire_schedule(model)``.
        rows: Every drawn row, in terminal-schedule order.
        places: Each terminal's place, by ``TerminalRef.key``.
        open_items: Everything unresolved, by :data:`OPEN_KINDS`, then place.
        connections: Every connection id the model holds, each drawn at both ends.
        nets: Every net id the model holds.
        full_texts: Each value cut on a drawing sheet, in full: ``[n]`` on a
            sheet refers to the n-th, listed on the full-text sheets.
        owners: Each component's and strip's printed name
            (``<board>/<designation>``) and header place, by ``(kind, id)``.
        boards_share_sheets: Whether boards could share a sheet (D-9).
        pins_off: ``(owner id, terminal id)`` of each pin marking that does not
            fit at its symbol, so it is printed at its row only (the header
            names it).
    """

    sheets: tuple[Sheet, ...]
    terminal_rows: tuple[schedules.TerminalRow, ...]
    wire_rows: tuple[schedules.WireRow, ...]
    rows: tuple[Row, ...]
    places: dict[tuple[str, str, str], Place]
    open_items: tuple[OpenItem, ...]
    connections: tuple[str, ...]
    nets: tuple[str, ...]
    full_texts: tuple[str, ...] = ()
    owners: dict[tuple[str, str], tuple[str, Place]] = field(default_factory=dict)
    boards_share_sheets: bool = BOARDS_SHARE_SHEETS
    pins_off: frozenset[tuple[str, str]] = frozenset()
    #: The routed sheets (``connection_routing.RoutedSet``), drawn after these sheets.
    routed: Any = None
    #: The sheet each row end's line is printed on, by (row index, end index):
    #: a row continued on another sheet reads its references from there.
    line_sheets: dict[tuple[int, int], int] = field(default_factory=dict)

    def terminal_keys(self) -> set[tuple[str, str, str]]:
        """The ``TerminalRef.key`` of every drawn terminal row."""
        return {row.key for row in self.rows}

    def wire_ids(self) -> set[str]:
        """Every wire drawn (at both ends of its connection)."""
        return {end.wire.wire_id for row in self.rows for end in row.ends if end.wire is not None}

    def net_ids(self) -> set[str]:
        """Every net a drawn row or end names."""
        found = {row.terminal.net_id for row in self.rows if row.terminal.net_id is not None}
        return found | {end.net_id for row in self.rows for end in row.ends}

    def connection_ids(self) -> set[str]:
        """Every connection drawn at an end."""
        return {end.connection_id for row in self.rows for end in row.ends}

    def locate(self, location: Mapping[str, str]) -> str | None:
        """Where a finding's location is drawn, e.g. ``B1/K9:3 (sheet 4, zone 2A)``.

        ``None`` when the location names nothing drawn (a template, a pattern).
        """
        rows = {row.key: row for row in self.rows}
        component, strip = location.get("component"), location.get("strip")
        terminal = location.get("terminal")
        if component is not None and terminal is not None:
            row = rows.get(("component", component, terminal))
            if row is not None:
                return f"{row.terminal.board_id}/{row.label} ({where(row.place)})"
        for kind, owner in (("component", component), ("strip", strip)):
            if owner is not None and (kind, owner) in self.owners:
                name, place = self.owners[(kind, owner)]
                return f"{name} ({where(place)})"
        wire = location.get("wire")
        if wire is not None:
            found = next((w for w in self.wire_rows if w.wire_id == wire), None)
            if found is not None and found.start_ref.key in rows:
                row = rows[found.start_ref.key]
                return f"wire at {row.terminal.board_id}/{row.label} ({where(row.place)})"
        net = location.get("net")
        if net is not None:
            members = [r for r in self.rows if r.terminal.net_id == net]
            if members:
                row = members[0]
                return (
                    f"net {row.terminal.signal} at {row.terminal.board_id}/{row.label} "
                    f"({where(row.place)})"
                )
        return None


def where(place: Place) -> str:
    """A place as printed in a list: ``sheet 4, zone 2B``."""
    return f"sheet {place.sheet}, zone {place.zone}"


# --------------------------------------------------------------------------- #
# Texts: what is printed, from the model's values only
# --------------------------------------------------------------------------- #


def cell_text(cell: schedules.AttributeCell) -> str:
    """A wire attribute as printed: ``"12 [user]"``, ``"not set"``, or the assumption.

    Args:
        cell: A wire schedule cell.

    Returns:
        The value and its source, ``"not set"`` when the model holds none, or
        ``"<value> assumed (<assumption id>)"``.
    """
    if cell.value is None:
        return NOT_SET
    if cell.source == "assumption":
        return f"{cell.value} assumed ({cell.assumption_id or NOT_SET})"
    return f"{cell.value} [{cell.source}]"


def wire_text(wire: schedules.WireRow) -> str:
    """A wire's id (the wire schedule's key) and its three attributes (:func:`cell_text`)."""
    return (
        f"wire {wire.wire_id}: no. {cell_text(wire.number)}; "
        f"mm² {cell_text(wire.cross_section_mm2)}; colour {cell_text(wire.colour)}"
    )


def far_name(end: End) -> str:
    """The far end's label as printed, with its board where that is needed.

    That is when it is on another board, or when the sheet shows more than one board.
    """
    return f"{end.far_board}/{end.far_label}" if end.qualified else end.far_label


def end_texts(end: End, here: int) -> tuple[str, str]:
    """The two texts an end is printed as, read from sheet ``here``.

    The first names the far end and where it is; the second the wire (or the
    cable core, or that the connection has no wire record because of its
    ``via``). A missing wire record's connection id is in the open items.
    """
    first = f"to {far_name(end)} ({end.far_place.reference(here)}) via {end.via}"
    if end.wire is not None:
        second = wire_text(end.wire)
    elif end.wire_missing:
        second = "WIRE RECORD MISSING (connection listed in the open items)"
    elif end.bridge:
        second = bridge_text(end)
    elif end.core is not None:
        label = end.core.label if end.core.label is not None else NOT_SET
        second = f"cable {end.core.cable_id} core {end.core.index}, core label {label}"
    else:
        second = f"no wire record (via {end.via})"
    return first, second


def bridge_text(end: End) -> str:
    """A strip point's bridge between its two sides, printed beside its bridge mark.

    It asserts only what the model records: the connection's ``via``, and
    the connection pattern that declared it (``per connection pattern <ref>``),
    or ``per model`` when none did.
    """
    source = (
        f"per connection pattern {end.pattern_ref}" if end.pattern_ref is not None else "per model"
    )
    return f"bridge between the sides: {end.via} {source}"


def row_marking(row: schedules.TerminalRow) -> str:
    """A row's terminal as printed beside its point: marking, and strip side and level."""
    if row.side is None:
        return row.marking
    return f"{row.marking} {row.side} L{row.level}"


def row_net(row: schedules.TerminalRow) -> str:
    """A row's net as printed under its marking."""
    return f"net {row.signal}" if row.signal is not None else "net: none"


# --------------------------------------------------------------------------- #
# Wrapping: in full, onto more lines; a token too wide on its own is cut with
# a reference to its full text
# --------------------------------------------------------------------------- #

_BREAK_AFTER = "-/_.:,;)"
ELLIPSIS = "…"


class FullTexts:
    """The values cut on the drawing sheets, numbered in the order first cut."""

    def __init__(self) -> None:
        """An empty list."""
        self.texts: list[str] = []

    def number(self, full: str) -> int:
        """The reference number of a full text, adding it when new."""
        if full not in self.texts:
            self.texts.append(full)
        return self.texts.index(full) + 1


def _pieces(token: str) -> list[str]:
    out: list[str] = []
    current = ""
    for ch in token:
        current += ch
        if ch in _BREAK_AFTER:
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def wrap(
    text: str,
    room: float,
    size: float,
    bold: bool = False,
    cuts: FullTexts | None = None,
    measure: Callable[[str, float, bool], float] = text_width,
) -> list[str]:
    """``text`` in lines no wider than ``room`` at ``size``, nothing dropped.

    Words wrap at spaces; a word wider than the room breaks after a hyphen,
    slash, underscore, dot, colon, comma or closing bracket. A piece with no
    such break that is still too wide is cut with an ellipsis and ``[n]``,
    where ``n`` numbers ``text`` in full in ``cuts`` (printed on the
    drawing's full-text list); with no ``cuts`` it is broken between
    characters instead, so it is still printed in full.
    """

    def fits(value: str) -> bool:
        return measure(value, size, bold) <= room

    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        sep = " " if current else ""
        if fits(current + sep + word):
            current += sep + word
            continue
        if current and fits(word):
            lines.append(current)
            current = word
            continue
        joiner = sep
        for piece in _pieces(word):
            if fits(current + joiner + piece):
                current += joiner + piece
                joiner = ""
                continue
            joiner = ""
            if current:
                lines.append(current)
                current = ""
            if fits(piece):
                current = piece
                continue
            if cuts is not None:
                marker = f"{ELLIPSIS}[{cuts.number(text)}]"
                head = piece
                while head and not fits(head + marker):
                    head = head[:-1]
                lines.append(head + marker)
                continue
            for ch in piece:
                if current and not fits(current + ch):
                    lines.append(current)
                    current = ""
                current += ch
    if current or not lines:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------- #
# Nets, rows and labels
# --------------------------------------------------------------------------- #

Key = tuple[str, str, str]


def _groups(model: entities.DesignModelV2) -> dict[str, list[set[Key]]]:
    """Each net's members grouped by its own connections (graph components)."""
    parent: dict[Key, Key] = {}

    def find(key: Key) -> Key:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for net in model.nets:
        for member in net.members:
            parent.setdefault(member.key, member.key)
    for connection in model.connections:
        for ref in (connection.start, connection.end):
            parent.setdefault(ref.key, ref.key)
        a, b = find(connection.start.key), find(connection.end.key)
        if a != b:
            parent[a] = b
    out: dict[str, list[set[Key]]] = {}
    for net in model.nets:
        by_root: dict[Key, set[Key]] = defaultdict(set)
        for member in net.members:
            by_root[find(member.key)].add(member.key)
        out[net.id] = sorted(by_root.values(), key=lambda group: sorted(group))
    return out


def _row_key(row: schedules.TerminalRow) -> Key:
    if row.owner_kind == "component":
        return entities.TerminalRef(component=row.owner_id, terminal=row.terminal_id).key
    return entities.TerminalRef(point=row.terminal_id, side=row.side).key


def _label(row: schedules.TerminalRow) -> str:
    """The schedules' label, with a strip point's side and level (labels alone may repeat)."""
    name = row.designation.product if row.designation is not None else row.owner_id
    return f"{name}:{row_marking(row)}"


def _notes(
    row: schedules.TerminalRow,
    key: Key,
    groups: dict[str, list[set[Key]]],
    has_ends: bool,
    link_end: str | None = None,
) -> list[tuple[str, str]]:
    """``(open kind, text)`` for each note a row carries; kind ``""`` for a plain note.

    ``link_end`` is what board-to-board links the terminal ends
    (``links.link_end_notes``): a point side in no net that ends one has no
    board-local wire but is not unconnected, and says which link it ends
    (kind ``strip_point_link_end``). A component terminal in no net that ends
    one leads with the same link note (kind ``terminal_link_end``) and keeps
    any unmet board-local requirement as a separate note and open item
    (``unconnected_required`` / ``unconnected_unresolved``): the link does
    not satisfy it.
    """
    notes: list[tuple[str, str]] = []
    if row.net_id is None:
        if link_end is not None:
            # The link participation leads; the link is no board-local wire.
            notes.append(
                (
                    (
                        "strip_point_link_end"
                        if row.owner_kind != "component"
                        else "terminal_link_end"
                    ),
                    f"{LINK_END_TAG}: no board-local wire (in no net); {link_end}",
                )
            )
            if row.owner_kind != "component":
                return notes
            # A component terminal's board-local requirement is its own
            # clause: the feeder link does not meet it, so a required or
            # unresolved terminal stays an open item of its own.
            if row.required is True:
                notes.append(
                    (
                        "unconnected_required",
                        "UNCONNECTED: required (documented); the board-local requirement "
                        "is not met: the feeder link is not a board-local wire",
                    )
                )
            elif row.required is None:
                notes.append(
                    (
                        "unconnected_unresolved",
                        "UNCONNECTED: requiredness unresolved; whether a board-local wire "
                        "is needed is not known: the feeder link is not a board-local wire",
                    )
                )
            else:
                notes.append(("", "board-local connection not required (documented)"))
            return notes
        if row.owner_kind != "component":
            notes.append(("unconnected_strip_point", "UNCONNECTED: this point side is in no net"))
        elif row.required is True:
            notes.append(("unconnected_required", "UNCONNECTED: required (documented)"))
        elif row.required is None:
            notes.append(("unconnected_unresolved", "UNCONNECTED: requiredness unresolved"))
        else:
            notes.append(("unconnected_not_required", "unconnected: not required (documented)"))
        return notes
    net_groups = groups.get(row.net_id, [])
    if len(net_groups) > 1:
        index = next(i for i, group in enumerate(net_groups) if key in group) + 1
        notes.append(
            (
                "unrealised_net",
                f"UNREALISED: net {row.signal} ({row.net_id}) is not joined by its "
                f"connections; this terminal is in group {index} of {len(net_groups)}",
            )
        )
    elif not has_ends:
        notes.append(("", f"in net {row.signal} ({row.net_id}) alone: no connection"))
    return notes


# --------------------------------------------------------------------------- #
# Lead routing
# --------------------------------------------------------------------------- #


def anchor_edge(anchor: entities.SymbolAnchor, symbol: entities.SymbolDef) -> str:
    """The edge an anchor sits on (a corner counts as right, then top, then bottom)."""
    if anchor.x == symbol.width:
        return "right"
    if anchor.y == 0:
        return "top"
    if anchor.y == symbol.height:
        return "bottom"
    return "left"


def exit_order(
    anchors: Sequence[entities.SymbolAnchor], symbol: entities.SymbolDef, cut: int
) -> list[str]:
    """The terminals in the order their leads reach the gutter, top to bottom.

    Clockwise round the symbol from a cut on its left edge: the left-edge
    anchors above the cut (round the top, the lowest outermost), the top edge
    left to right, the right edge top to bottom, the bottom edge right to
    left, then the left-edge anchors below the cut (round the bottom).
    ``cut`` is how many left-edge anchors, counted from the bottom, go down.
    Leads in this order, to rows in the same order, cannot cross.
    """
    by: dict[str, list[entities.SymbolAnchor]] = defaultdict(list)
    for anchor in anchors:
        by[anchor_edge(anchor, symbol)].append(anchor)
    left = sorted(by["left"], key=lambda a: -a.y)  # bottom to top
    ordered = (
        left[cut:]
        + sorted(by["top"], key=lambda a: a.x)
        + sorted(by["right"], key=lambda a: a.y)
        + sorted(by["bottom"], key=lambda a: -a.x)
        + left[:cut]
    )
    return [a.terminal for a in ordered]


def _pin_order(symbol: entities.SymbolDef, wanted: Sequence[str]) -> tuple[list[str], int]:
    """An order of the anchored terminals whose leads cannot cross, and its cut.

    The schedule's order is kept when some cut gives it; otherwise the order
    (over every cut) that keeps the most terminals where the schedule has them.
    """
    anchors = [a for a in symbol.anchors if a.terminal in wanted]
    lefts = sum(1 for a in anchors if anchor_edge(a, symbol) == "left")
    best: tuple[int, int] | None = None
    for cut in range(lefts + 1):
        order = exit_order(anchors, symbol, cut)
        if order == list(wanted):
            return order, cut
        score = sum(1 for a, b in zip(order, wanted, strict=True) if a == b)
        if best is None or score > best[0]:
            best = (score, cut)
    assert best is not None
    return exit_order(anchors, symbol, best[1]), best[1]


def _sides(
    symbol: entities.SymbolDef, anchors: Sequence[entities.SymbolAnchor], cut: int
) -> tuple[list[str], list[str], list[str], list[str]]:
    """The leads passing above and below the symbol, and the left anchors going up and down."""
    order = exit_order(anchors, symbol, cut)
    edges = {a.terminal: anchor_edge(a, symbol) for a in anchors}
    lefts = sorted((a for a in anchors if edges[a.terminal] == "left"), key=lambda a: -a.y)
    down_left = [a.terminal for a in lefts[:cut]]
    up_left = [a.terminal for a in lefts[cut:]]
    ups = [t for t in order if edges[t] == "top" or t in up_left]
    downs = [t for t in order if edges[t] == "bottom" or t in down_left]
    return ups, downs, up_left, down_left


def _lanes(
    order: Sequence[str], exits: Mapping[str, float], rows_y: Mapping[str, float], lane: float
) -> dict[str, int]:
    """A lane per lead such that no two leads cross (see :func:`route`)."""

    def span(t: str) -> tuple[float, float]:
        low, high = sorted((exits[t], rows_y[t]))
        return low - lane, high + lane

    def overlap(a: str, b: str) -> bool:
        (a0, a1), (b0, b1) = span(a), span(b)
        return a0 < b1 and b0 < a1

    lanes: dict[str, int] = {}
    descending = [t for t in order if rows_y[t] <= exits[t]]
    ascending = [t for t in order if rows_y[t] > exits[t]]
    done: list[str] = []
    for t in reversed(descending):  # bottom to top: an upper lead lies right of a lower one
        lanes[t] = 1 + max((lanes[o] for o in done if overlap(o, t)), default=-1)
        done.append(t)
    done = []
    for t in ascending:  # top to bottom: a lower lead lies right of an upper one
        lanes[t] = 1 + max((lanes[o] for o in done if overlap(o, t)), default=-1)
        done.append(t)

    # A lead running down and one running up never overlap where both are in
    # the gutter (their spans are disjoint), so they may share a lane; only a
    # true overlap (no spacing margin) would need another lane.
    def touch(a: str, b: str) -> bool:
        (a0, a1), (b0, b1) = sorted((exits[a], rows_y[a])), sorted((exits[b], rows_y[b]))
        return a0 <= b1 + 0.5 and b0 <= a1 + 0.5

    for a in descending:
        for b in ascending:
            if lanes[a] == lanes[b] and touch(a, b):
                lanes[b] = max(lanes.values()) + 1
    return lanes


def route(
    symbol: entities.SymbolDef,
    cut: int,
    left: float,
    top: float,
    bottom: float,
    rows_y: Mapping[str, float],
    point_x: float,
    spacing: float = LANE,
) -> tuple[SymbolFrame, list[Lead]] | None:
    """The symbol's frame in a block body, and a lead per anchored row that crosses none.

    ``rows_y`` (each anchored terminal's point height) must be in the order
    :func:`exit_order` gives for ``cut``; otherwise, or when there is no room
    beside the symbol, ``None``.

    A lead leaves its anchor perpendicular to its edge, passes round the
    symbol (above it from the top edge, below it from the bottom edge, round
    the top or bottom from the left edge, each further out than the leads it
    passes), runs in its own lane of the gutter right of the symbol, and
    enters its point from the left. A lane is shared only by leads whose runs
    do not overlap; of two overlapping leads running down from their exits
    the upper lies to the right, of two running up the lower does, so no
    lead crosses another and none enters the symbol's box.
    """
    anchors = [a for a in symbol.anchors if a.terminal in rows_y]
    order = exit_order(anchors, symbol, cut)
    if order != sorted(rows_y, key=lambda t: -rows_y[t]):
        return None
    edges = {a.terminal: anchor_edge(a, symbol) for a in anchors}
    by_terminal = {a.terminal: a for a in anchors}
    ups, downs, up_left, down_left = _sides(symbol, anchors, cut)
    x0 = left + SYMBOL_LEFT + max(len(up_left), len(down_left)) * spacing + CLEAR
    band_top = top - CLEAR - len(ups) * spacing
    band_bottom = bottom + CLEAR + len(downs) * spacing
    if band_top <= band_bottom:
        return None

    def attempt(
        lanes_room: int,
    ) -> tuple[float, float, float, dict[str, float], dict[str, int]] | None:
        width_room = left + GUTTER_END - CLEAR - lanes_room * spacing - x0
        if width_room <= 0:
            return None
        scale = min(
            (band_top - band_bottom) / symbol.height, width_room / symbol.width, SYMBOL_UNIT_MAX
        )
        y1 = (band_top + band_bottom + symbol.height * scale) / 2
        y0 = y1 - symbol.height * scale
        exits: dict[str, float] = {}
        for n, t in enumerate(ups):
            exits[t] = y1 + CLEAR + (len(ups) - 1 - n) * spacing
        for n, t in enumerate(downs):
            exits[t] = y0 - CLEAR - n * spacing
        for t in order:
            if edges[t] == "right":
                exits[t] = y1 - by_terminal[t].y * scale
        lane = _lanes(order, exits, rows_y, spacing)
        if max(lane.values(), default=-1) + 1 > lanes_room:
            return None
        return scale, y1, y0, exits, lane

    found = attempt(len(order))
    if found is None:
        return None
    tighter = attempt(max(found[4].values(), default=-1) + 1)
    scale, y1, y0, exits, lane = tighter if tighter is not None else found
    x1 = x0 + symbol.width * scale
    gutter = x1 + CLEAR
    box = (x0, y0, x1, y1)
    leads: list[Lead] = []
    for t in order:
        anchor = by_terminal[t]
        ax, ay = x0 + anchor.x * scale, y1 - anchor.y * scale
        lx = gutter + lane[t] * spacing
        e = exits[t]
        points: list[Point] = [(ax, ay)]
        edge = edges[t]
        if edge == "right":
            points.append((lx, ay))
        elif edge in ("top", "bottom"):
            points += [(ax, e), (lx, e)]
        else:
            if t in up_left:
                out = CLEAR + (len(up_left) - 1 - up_left.index(t)) * spacing
            else:
                out = CLEAR + down_left.index(t) * spacing
            points += [(x0 - out, ay), (x0 - out, e), (lx, e)]
        points += [(lx, rows_y[t]), (point_x, rows_y[t])]
        cleaned: list[Point] = []
        for p in points:
            if not cleaned or cleaned[-1] != p:
                cleaned.append(p)
        leads.append(Lead(t, tuple(cleaned), box))
    return SymbolFrame(x0, y1, scale), leads


def _plain_frame(symbol: entities.SymbolDef, left: float, top: float, bottom: float) -> SymbolFrame:
    """A symbol drawn with no leads, centred in its band."""
    room_w = GUTTER_END - SYMBOL_LEFT - 2 * CLEAR
    room_h = max(top - bottom - 2 * CLEAR, MM)
    scale = min(room_w / symbol.width, room_h / symbol.height, SYMBOL_UNIT_MAX)
    y1 = (top + bottom + symbol.height * scale) / 2
    return SymbolFrame(left + SYMBOL_LEFT + CLEAR, y1, scale)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

#: A text line before placement: text, size, bold, tone, role, index.
_Half = tuple[str, float, bool, str, str, int]


@dataclass
class _Spec:
    """One line of a block before placement, with its texts by half-slot."""

    rows: tuple[int, ...]  # the row's index; none for a list member
    kind: str
    index: int
    first: bool  # holds the first line of its (first) row
    halves: list[_Half]


@dataclass
class _Block:
    board: str
    kind: str
    owner_id: str
    name: str
    notes: list[str]
    template_ref: str | None
    symbol: entities.SymbolDef | None
    specs: list[_Spec] = field(default_factory=list)
    cut: int = 0
    routed: bool = False
    anchored: frozenset[str] = frozenset()
    members: tuple[str, ...] = ()
    min_body: int = 1
    pins_off: frozenset[str] = frozenset()
    cols: int = 1
    mark_w: float = MARK_RIGHT - MARK_LEFT

    @property
    def title(self) -> str:
        if self.kind == "no_terminals":
            return (
                f"Board {self.board} — {len(self.members)} components with no terminals "
                "and no symbol"
            )
        return f"{self.name} ({self.kind}), board {self.board}"

    @property
    def text_left(self) -> float:
        return MARK_LEFT + self.mark_w + 4 * MM

    @property
    def text_right(self) -> float:
        return self.cols * COLUMN - 2 * MM


@dataclass
class _Placed:
    block: _Block
    part: int
    sheet: int
    column: int
    header_slot: int
    header: list[_Half]
    lines: list[tuple[_Spec, int, int]] = field(default_factory=list)  # spec, slot, span
    body_end: int = 0


def drawing_set(
    model: entities.DesignModelV2,
    *,
    boards_share_sheets: bool = BOARDS_SHARE_SHEETS,
    link_ends: Mapping[Key, str] | None = None,
) -> DrawingSet:
    """The connection drawing of a model, laid out.

    Args:
        model: A v2 model, as read (anchored).
        boards_share_sheets: D-9: whether a board may continue on the sheet
            of the board before it (always from a new block column).
        link_ends: A ``pp-design/3`` revision's link ends
            (``links.link_end_notes``): a terminal ending a board-to-board
            link says so instead of "unconnected". None for v2.

    Returns:
        The drawing set: see the module docstring.
    """
    places: dict[Key, Place] | None = None
    pins_off: frozenset[tuple[str, str]] = frozenset()
    result: DrawingSet | None = None
    # A cross-reference's text depends on where its far end is drawn, and
    # where things are drawn depends on how long the texts are: lay out
    # again with the places found until they no longer change.
    line_sheets: dict[tuple[int, int], int] = {}
    shown: dict[int, frozenset[str]] | None = None
    for _round in range(8):
        result = _lay_out(
            model, places, boards_share_sheets, pins_off, line_sheets, shown, link_ends or {}
        )
        found = {
            sheet.number: frozenset((sheet.board_id, *sheet.boards)) for sheet in result.sheets
        }
        if (
            result.places == places
            and result.pins_off == pins_off
            and result.line_sheets == line_sheets
            and found == shown
        ):
            break
        places, pins_off, line_sheets = result.places, result.pins_off, result.line_sheets
        shown = found
    assert result is not None
    from app.design.model import connection_routing  # the routing reads this module

    routed = connection_routing.routed_set(model, result)
    rank = {kind: n for n, kind in enumerate(OPEN_KINDS)}
    items = sorted(
        (*result.open_items, *routed.open_items),
        key=lambda item: (
            rank[item.kind],
            item.board_id,
            (item.place.sheet, item.place.column, item.place.slot) if item.place else (0, 0, 0),
            item.text,
        ),
    )
    return dataclasses.replace(
        result, routed=routed, open_items=tuple(items), full_texts=routed.full_texts
    )


def _column_left(column: int) -> float:
    return AREA_LEFT + column * COLUMN


def _slot_top(slot: int) -> float:
    return AREA_TOP - PAD - slot * SLOT


def _baseline(top: float, half: int) -> float:
    """The baseline of the ``half``-th text line below a slot top."""
    return top - (half + 1) * HALF + 0.9 * MM


def _header_point(column: int, slot: int) -> Point:
    """A block header's place: the middle of its title's first line."""
    return _column_left(column) + TITLE_LEFT, _baseline(_slot_top(slot), 0) + 0.35 * TITLE_PT


def _lay_out(
    model: entities.DesignModelV2,
    previous: dict[Key, Place] | None,
    share: bool,
    pins_off: frozenset[tuple[str, str]] = frozenset(),
    line_sheets: dict[tuple[int, int], int] | None = None,
    shown: dict[int, frozenset[str]] | None = None,
    link_ends: Mapping[Key, str] | None = None,
) -> DrawingSet:
    terminal_rows = schedules.terminal_schedule(model)
    wire_rows = schedules.wire_schedule(model)
    wires_by_connection = {row.connection_id: row for row in wire_rows}
    templates = {t.ref: t for t in model.templates}
    groups = _groups(model)
    keys = [_row_key(row) for row in terminal_rows]
    labels = {key: _label(row) for key, row in zip(keys, terminal_rows, strict=True)}
    boards = {key: row.board_id for key, row in zip(keys, terminal_rows, strict=True)}
    ends_of: dict[Key, list[tuple[entities.Connection, entities.TerminalRef]]] = defaultdict(list)
    for connection in sorted(model.connections, key=lambda c: c.id):
        ends_of[connection.start.key].append((connection, connection.end))
        ends_of[connection.end.key].append((connection, connection.start))
    nets = {n.id: n for n in model.nets}
    cuts = FullTexts()
    # Before any place is known, a far end is taken to be on a far sheet.
    estimate = Place(10**3, 0, 0, AREA_RIGHT - 1, AREA_BOTTOM + 1)
    known = previous or {}
    # On a sheet showing more than one board (as the last round laid it out),
    # every far end is named with its board.
    several = share and len(set(boards.values())) > 1

    def mixed(key: Key) -> bool:
        place = known.get(key)
        if place is None or shown is None:
            return several  # before any place is known
        return len(shown.get(place.sheet, frozenset())) > 1

    all_ends: list[list[End]] = []
    for index, key in enumerate(keys):
        found: list[End] = []
        for connection, far in ends_of.get(key, []):
            net = nets[connection.net_id]
            wire = wires_by_connection.get(connection.id)
            far_board = boards.get(far.key, "")
            found.append(
                End(
                    connection_id=connection.id,
                    via=connection.via,
                    net_id=net.id,
                    signal=net.signal,
                    far_key=far.key,
                    far_label=labels.get(far.key, NOT_SET),
                    far_place=known.get(far.key, estimate),
                    wire=wire,
                    wire_missing=connection.via == "wire" and wire is None,
                    core=connection.core,
                    far_board=far_board,
                    qualified=far_board != terminal_rows[index].board_id or mixed(key),
                    pattern_ref=connection.pattern_ref,
                    bridge=key[0] == "point" and far.key[0] == "point" and far.key[1] == key[1],
                )
            )
        all_ends.append(found)
    row_notes = {
        index: _notes(
            row, keys[index], groups, bool(all_ends[index]), (link_ends or {}).get(keys[index])
        )
        for index, row in enumerate(terminal_rows)
    }

    blocks = _blocks(
        model, terminal_rows, keys, all_ends, row_notes, templates, known, cuts, line_sheets or {}
    )
    for block in blocks:
        off = frozenset(t for owner, t in pins_off if owner == block.owner_id)
        if off and block.kind == "component":
            block.pins_off = off
            terminals = {
                r.terminal_id: r.marking for r in terminal_rows if r.owner_id == block.owner_id
            }
            names = ", ".join(sorted(terminals.get(t, t) for t in off))
            block.notes.append(
                f"pin markings {names} shown at the rows only (no room at the symbol)"
            )
    placed, sheet_boards = _place(blocks, terminal_rows, share, cuts)
    drawn = _finish(
        model, placed, terminal_rows, wire_rows, keys, labels, all_ends, row_notes, groups, cuts,
        sheet_boards, share,
    )  # fmt: skip
    dropped = {
        (p.owner_id, t) for sheet in drawn.sheets for p in sheet.parts for t in p.pins_dropped
    }
    printed_on = {
        (spec.rows[0], spec.index): p.sheet
        for p in placed
        for spec, _slot, _span in p.lines
        if spec.kind == "end"
    }
    return dataclasses.replace(drawn, pins_off=pins_off | dropped, line_sheets=printed_on)


def _blocks(
    model: entities.DesignModelV2,
    terminal_rows: tuple[schedules.TerminalRow, ...],
    keys: list[Key],
    all_ends: list[list[End]],
    row_notes: dict[int, list[tuple[str, str]]],
    templates: dict[str, entities.ComponentTemplate],
    known: dict[Key, Place],
    cuts: FullTexts,
    line_sheets: dict[tuple[int, int], int],
) -> list[_Block]:
    # Owners in schedule order, each with its rows' indexes.
    owners: list[tuple[str, str, str, list[int]]] = []
    for index, row in enumerate(terminal_rows):
        if owners and owners[-1][1:3] == (row.owner_kind, row.owner_id):
            owners[-1][3].append(index)
        else:
            owners.append((row.board_id, row.owner_kind, row.owner_id, [index]))
    listed = {(kind, owner) for _b, kind, owner, _r in owners}
    for component in model.components:
        if ("component", component.id) not in listed:
            owners.append((component.board_id, "component", component.id, []))
    for strip in model.strips:
        if ("strip", strip.id) not in listed:
            owners.append((strip.board_id, "strip", strip.id, []))
    order = {"component": 0, "strip": 1}
    first_index = {(kind, owner): (rows[0] if rows else -1) for _b, kind, owner, rows in owners}
    owners.sort(
        key=lambda o: (
            o[0],
            order[o[1]],
            first_index[(o[1], o[2])] if first_index[(o[1], o[2])] >= 0 else 10**9,
            o[2],
        )
    )
    components = {c.id: c for c in model.components}
    strips = {s.id: s for s in model.strips}

    def halves(
        text: str, size: float, room: float, *, bold: bool = False, tone: str, role: str, n: int
    ) -> list[_Half]:
        return [(line, size, bold, tone, role, n) for line in wrap(text, room, size, bold, cuts)]

    def here_of(index: int, n: int = -1) -> int:
        if (index, n) in line_sheets:
            return line_sheets[(index, n)]
        return known[keys[index]].sheet if keys[index] in known else -1

    def end_spec(index: int, n: int, first: bool, text_room: float) -> _Spec:
        end = all_ends[index][n]
        a, b = end_texts(end, here_of(index, n))
        loud = end.wire_missing
        return _Spec(
            (index,),
            "end",
            n,
            first,
            halves(a, BODY_PT, text_room, tone="plain", role="far", n=n)
            + halves(
                b, DETAIL_PT, text_room, bold=loud, tone="loud" if loud else "grey",
                role="detail", n=n,
            ),
        )  # fmt: skip

    blocks: list[_Block] = []
    summaries: dict[str, _Block] = {}
    for board_id, kind, owner_id, indexes in owners:
        if kind == "component":
            designation = components[owner_id].designation
            template_ref: str | None = components[owner_id].template_ref
        else:
            designation = strips[owner_id].designation
            template_ref = strips[owner_id].template_ref
        template = templates.get(template_ref) if template_ref is not None else None
        symbol = template.symbol if template is not None else None
        name = designation.product if designation is not None else owner_id
        if kind == "component" and not indexes and symbol is None:
            # Listed once per board, never one placeholder block each (M5-0).
            summary = summaries.get(board_id)
            if summary is None:
                summary = _Block(
                    board_id, "no_terminals", f"no-terminals:{board_id}", "", [], None, None
                )
                summary.notes.append(
                    "their templates define no terminals and no symbol, so they have no rows; "
                    "each is listed here and in the open items"
                )
                summaries[board_id] = summary
                blocks.append(summary)
            summary.members = (*summary.members, owner_id)
            text = f"{name}: template {template_ref or 'none'}" + (
                "" if designation is not None else f"; designation {NOT_SET} (id {owner_id})"
            )
            n = len(summary.members) - 1
            lines = halves(text, BODY_PT, COLUMN - 8 * MM, tone="plain", role="member", n=n)
            last = summary.specs[-1] if summary.specs else None
            if last is not None and len(last.halves) + len(lines) <= 2:
                last.halves += lines
            else:
                summary.specs.append(_Spec((), "member", n, True, lines))
            continue
        notes: list[str] = [f"template {template_ref}" if template_ref else "no template"]
        if designation is None:
            notes.append(f"designation {NOT_SET} (id {owner_id})")
        if symbol is not None:
            notes.append(symbol_status(template))
        if not indexes:
            notes.append(
                "no terminals defined in its template" if kind == "component" else "no points"
            )
        block = _Block(board_id, kind, owner_id, name, notes, template_ref, symbol)
        _widen(block, [terminal_rows[i] for i in indexes], [
            end_texts(end, here_of(i, n))[0] for i in indexes for n, end in enumerate(all_ends[i])
        ])  # fmt: skip
        text_room = block.text_right - block.text_left
        ordered = list(indexes)
        if symbol is not None:
            block.min_body = 2
        if kind == "component" and symbol is not None and indexes:
            block.min_body = 3
            by_terminal = {terminal_rows[i].terminal_id: i for i in indexes}
            anchored = {a.terminal for a in symbol.anchors}
            wanted = [
                terminal_rows[i].terminal_id
                for i in indexes
                if terminal_rows[i].terminal_id in anchored
            ]
            if wanted:
                pins, cut = _pin_order(symbol, wanted)
                anchors = [a for a in symbol.anchors if a.terminal in pins]
                ups, downs, up_left, down_left = _sides(symbol, anchors, cut)
                width_need = (
                    SYMBOL_LEFT + max(len(up_left), len(down_left)) * LANE + 2 * CLEAR
                    + 3 * MM + len(pins) * LANE
                )  # fmt: skip
                points = [(a.x, a.y) for a in anchors]
                if len(points) == len(set(points)) and width_need <= GUTTER_END:
                    block.cut, block.routed, block.anchored = cut, True, frozenset(pins)
                    if pins != wanted:
                        block.notes.append("rows in the symbol's pin order, so no leads cross")
                        rest = [i for i in indexes if terminal_rows[i].terminal_id not in pins]
                        ordered = [by_terminal[t] for t in pins] + rest
                    need = 2 * CLEAR + (len(ups) + len(downs)) * LANE + SYMBOL_MIN
                    block.min_body = max(3, math.ceil(need / SLOT))
                elif len(points) != len(set(points)):
                    block.notes.append(
                        "leads not drawn: no crossing-free route; "
                        "pin markings are shown at the symbol where they fit"
                    )
                else:
                    block.notes.append(
                        "no leads drawn: they would need lanes closer than 2 mm; "
                        "each terminal's row carries its marking"
                    )
        for index in ordered:
            notes_here = row_notes[index]
            ends = all_ends[index]
            # A strip point's bridge (a connection between its two sides) is
            # its inner side's last line and its outer side's first, so the
            # two lines sit together and the bridge mark joins them.
            bridged = [n for n, end in enumerate(ends) if end.bridge]
            others = [n for n in range(len(ends)) if n not in bridged]
            outer = terminal_rows[index].side == "outer"
            order_ends = (bridged + others) if outer else (others + bridged)
            first = True
            if outer:
                for n in bridged:
                    block.specs.append(end_spec(index, n, first, text_room))
                    first = False
            for n, (_kind, note) in enumerate(notes_here):
                loud = note.startswith(("UNCONNECTED", "UNREALISED", LINK_END_TAG))
                lines = halves(
                    note,
                    BODY_PT,
                    text_room,
                    bold=loud,
                    tone="loud" if loud else "plain",
                    role="note",
                    n=n,
                )
                if loud:
                    lines = [("", BODY_PT, False, "plain", "", -1), *lines]  # room above a red line
                block.specs.append(_Spec((index,), "note", n, first, lines))
                first = False
            for n in order_ends:
                if outer and n in bridged:
                    continue
                block.specs.append(end_spec(index, n, first, text_room))
                first = False
        blocks.append(block)
    return blocks


def _widen(block: _Block, rows: Sequence[schedules.TerminalRow], refs: Sequence[str]) -> None:
    """How many block columns a block takes, and its marking column's width.

    A marking, a block title (name and kind) and each word of a
    cross-reference are printed on one line, never broken: a block whose
    longest such value does not fit one column takes two or three. Only a
    value too long even for a whole sheet's width wraps.
    """
    mark = max((text_width(row_marking(r), BODY_PT, True) for r in rows), default=0.0) + 0.5 * MM
    word = (
        max((text_width(w, BODY_PT) for ref in refs for w in ref.split(" ")), default=0.0)
        + 0.5 * MM
    )
    title = TITLE_LEFT + text_width(f"{block.name} ({block.kind}),", TITLE_PT, True)
    title += CONTINUES_ROOM + 2 * MM
    for cols in range(1, COLUMNS + 1):
        block.cols = cols
        block.mark_w = max(MARK_RIGHT - MARK_LEFT, min(mark, cols * COLUMN - 50 * MM - MARK_LEFT))
        if (
            title <= cols * COLUMN
            and word <= block.text_right - block.text_left
            and (mark <= block.mark_w)
        ):
            return


def _mark_lines(row: schedules.TerminalRow, cont: bool, room: float) -> list[_Half]:
    cuts = None
    marking = row_marking(row) + (" (cont.)" if cont else "")
    out: list[_Half] = [
        (t, BODY_PT, True, "plain", "marking", 0) for t in wrap(marking, room, BODY_PT, True, cuts)
    ]
    if True:  # the net is repeated on a continued row
        out += [
            (t, DETAIL_PT, False, "grey", "net", 0)
            for t in wrap(row_net(row), room, DETAIL_PT, False, cuts)
        ]
    return out


def _header(
    block: _Block, continued_from: Point | None, sheet: int | None, here: int, cuts: FullTexts
) -> list[_Half]:
    room = block.cols * COLUMN - TITLE_LEFT - 2 * MM
    notes = "; ".join(block.notes)
    title = block.title
    if continued_from is not None and block.kind != "no_terminals":
        # A continuation shows no symbol: its notes about the symbol are on the first part.
        notes = "; ".join(n for n in block.notes if not n.startswith(_SYMBOL_NOTES))
        title = _continued_title(block)
    if continued_from is not None and sheet is not None:
        before = Place(sheet, 0, 0, *continued_from)
        lead = "list started on" if block.kind == "no_terminals" else "continued from"
        notes = f"{lead} {before.reference(here)}; {notes}"
    out: list[_Half] = [
        (line, TITLE_PT, True, "plain", "title", 0)
        for line in wrap(title, room - CONTINUES_ROOM, TITLE_PT, True, cuts)
    ]
    out += [
        (line, BODY_PT, False, "plain", "header", 0)
        for line in wrap(notes, room, BODY_PT, False, cuts)
    ]
    return out


#: Header notes about a block's symbol, printed on its first part only.
_SYMBOL_NOTES = (
    "symbol ",
    "pin markings",
    "rows in the symbol",
    "no leads drawn",
    "leads not drawn",
)


def symbol_status(template: entities.ComponentTemplate | None) -> str:
    """A symbol's review status as printed at its block: never implies a reviewed meaning."""
    if template is None or template.symbol is None:
        return SYMBOL_NOT_DEFINED
    if template.review.status == "approved":
        return "symbol reviewed (template approved)"
    return f"symbol {template.review.status}: its drawing asserts nothing"


def _continued_title(block: _Block) -> str:
    return f"{block.name} ({block.kind}) continued, board {block.board}"


def _place(
    blocks: list[_Block],
    terminal_rows: tuple[schedules.TerminalRow, ...],
    share: bool,
    cuts: FullTexts,
) -> tuple[list[_Placed], dict[int, list[str]]]:
    placed: list[_Placed] = []
    owner_of = {id(spec): block for block in blocks for spec in block.specs}
    # Each column of the current sheet is filled down to ``fill[column]``; a
    # block taking several columns starts below the lowest of them, and the
    # next block goes on below it in the same column.
    sheet, column = FIRST_SHEET - 1, 0
    fill = [SLOTS] * COLUMNS
    sheet_boards: dict[int, list[str]] = defaultdict(list)
    board: str | None = None

    def new_column(force_sheet: bool, width: int = 1) -> None:
        nonlocal sheet, column, fill
        if force_sheet or column + 1 + width > COLUMNS:
            sheet += 1
            column = 0
            fill = [0] * COLUMNS
        else:
            column += 1

    def start(width: int) -> int:
        return max(fill[column : column + width])

    def span_of(spec: _Spec, segment_first: bool) -> int:
        need = len(spec.halves)
        if spec.kind != "member" and segment_first:
            block = owner_of[id(spec)]
            need = max(
                need, len(_mark_lines(terminal_rows[spec.rows[0]], not spec.first, block.mark_w))
            )
        return max(1, math.ceil(need / 2))

    def spans(specs: Sequence[_Spec]) -> list[int]:
        out: list[int] = []
        for n, spec in enumerate(specs):
            first = n == 0 or specs[n - 1].rows[-1:] != spec.rows[:1] or spec.kind == "member"
            out.append(span_of(spec, first))
        return out

    for block in blocks:
        if block.board != board:
            board = block.board
            if not share or sheet < FIRST_SHEET:
                new_column(force_sheet=True)
            elif fill[column] > 0:
                fill[column] = min(fill[column] + 1, SLOTS)  # a blank slot between boards
        part = 1
        position = 0
        previous: _Placed | None = None
        while True:
            # A continuation names the sheet and zone of the part before it.
            header = _header(
                block,
                _header_point(previous.column, previous.header_slot) if previous else None,
                previous.sheet if previous else None,
                sheet,
                cuts,
            )
            h = max(1, math.ceil(len(header) / 2))
            rest = spans(block.specs[position:])
            total = h + max(sum(rest), block.min_body)
            least = h + max(rest[0] if rest else 1, block.min_body)
            if column + block.cols > COLUMNS:
                new_column(force_sheet=True)
            while (
                start(block.cols) > 0
                and start(block.cols) + (total if total <= SLOTS else least) > SLOTS
            ):
                new_column(force_sheet=False, width=block.cols)
            slot = start(block.cols)
            header = _header(
                block,
                _header_point(previous.column, previous.header_slot) if previous else None,
                previous.sheet if previous else None,
                sheet,
                cuts,
            )
            h = max(1, math.ceil(len(header) / 2))
            if block.board not in sheet_boards[sheet]:
                sheet_boards[sheet].append(block.board)
            here = _Placed(block, part, sheet, column, slot, header)
            slot += h
            body_start = slot
            while position < len(block.specs):
                spec = block.specs[position]
                segment_first = (
                    not here.lines
                    or here.lines[-1][0].rows[-1:] != spec.rows[:1]
                    or spec.kind == "member"
                )
                span = span_of(spec, segment_first)
                if slot + span > SLOTS and here.lines:
                    break
                here.lines.append((spec, slot, span))
                slot += span
                position += 1
            slot = max(slot, min(SLOTS, body_start + block.min_body))
            here.body_end = slot
            placed.append(here)
            for taken in range(column, column + block.cols):
                fill[taken] = slot
            if position >= len(block.specs):
                break
            previous = here
            part += 1
            new_column(force_sheet=False, width=block.cols)
    return placed, sheet_boards


def _finish(
    model: entities.DesignModelV2,
    placed: list[_Placed],
    terminal_rows: tuple[schedules.TerminalRow, ...],
    wire_rows: tuple[schedules.WireRow, ...],
    keys: list[Key],
    labels: dict[Key, str],
    all_ends: list[list[End]],
    row_notes: dict[int, list[tuple[str, str]]],
    groups: dict[str, list[set[Key]]],
    cuts: FullTexts,
    sheet_boards: dict[int, list[str]],
    share: bool,
) -> DrawingSet:
    # Each row's place: its point, in the first line that shows it.
    places: dict[Key, Place] = {}
    headers: list[Place] = []
    for p in placed:
        left = _column_left(p.column)
        headers.append(
            Place(p.sheet, p.column, p.header_slot, *_header_point(p.column, p.header_slot))
        )
        for spec, slot, _span in p.lines:
            top = _slot_top(slot)
            if spec.rows:
                places.setdefault(
                    keys[spec.rows[0]], Place(p.sheet, p.column, slot, left + POINT, top - HALF)
                )

    rows = [
        Row(
            terminal=row,
            key=keys[index],
            label=labels[keys[index]],
            place=places[keys[index]],
            notes=tuple(text for _kind, text in row_notes[index]),
            ends=tuple(all_ends[index]),
        )
        for index, row in enumerate(terminal_rows)
    ]
    components = {c.id: c for c in model.components}

    def member_name(component_id: str) -> str:
        designation = components[component_id].designation
        return designation.product if designation is not None else component_id

    parts: list[BlockPart] = []
    owners: dict[tuple[str, str], tuple[str, Place]] = {}
    for i, p in enumerate(placed):
        previous = headers[i - 1] if p.part > 1 else None
        following = headers[i + 1] if i + 1 < len(placed) and placed[i + 1].part > 1 else None
        parts.append(_draw(p, headers[i], previous, following, rows))
        block = p.block
        if block.kind == "no_terminals":
            for spec, slot, _span in p.lines:
                for k, half in enumerate(spec.halves):
                    member = block.members[half[5]]
                    owners.setdefault(
                        ("component", member),
                        (
                            f"{block.board}/{member_name(member)}",
                            Place(p.sheet, p.column, slot, _column_left(p.column) + 4 * MM,
                                  _baseline(_slot_top(slot), k)),
                        ),
                    )  # fmt: skip
        elif p.part == 1:
            owners[(block.kind, block.owner_id)] = (f"{block.board}/{block.name}", headers[i])

    open_items = _open_items(
        model, placed, rows, row_notes, groups, labels, wire_rows, places, owners, member_name
    )
    sheets = tuple(
        Sheet(
            number=number,
            board_id=sheet_boards[number][0],
            parts=tuple(part for part in parts if part.place.sheet == number),
            boards=tuple(sheet_boards[number]),
        )
        for number in sorted(sheet_boards)
    )
    return DrawingSet(
        sheets=sheets,
        terminal_rows=terminal_rows,
        wire_rows=wire_rows,
        rows=tuple(rows),
        places=places,
        open_items=open_items,
        connections=tuple(sorted(c.id for c in model.connections)),
        nets=tuple(sorted(n.id for n in model.nets)),
        full_texts=tuple(cuts.texts),
        owners=owners,
        boards_share_sheets=share,
    )


def _draw(
    p: _Placed,
    header_place: Place,
    previous: Place | None,
    following: Place | None,
    rows: list[Row],
) -> BlockPart:
    """A placed part's every text, point, stub, bridge, lead and symbol frame."""
    block = p.block
    left = _column_left(p.column)
    point_x = left + POINT
    text_x = left + block.text_left
    top = _slot_top(p.header_slot)
    texts: list[Text] = [
        Text(left + TITLE_LEFT, _baseline(top, n), text, size, bold, tone, role=role)
        for n, (text, size, bold, tone, role, _i) in enumerate(p.header)
    ]
    if following is not None:
        texts.append(
            Text(
                left + block.text_right,
                _baseline(top, 0),
                ("list goes on at" if block.kind == "no_terminals" else "continues on")
                + f" {following.reference(p.sheet)}",
                BODY_PT,
                True,
                right=True,
                role="continues",
            )
        )
    lines_top = _slot_top(p.header_slot + max(1, math.ceil(len(p.header) / 2)))
    bottom = _slot_top(p.body_end)
    points: list[Point] = []
    stubs: list[Path] = []
    buses: list[Path] = []
    bridges: list[Path] = []
    dotted: list[Path] = []
    pinned: list[Path] = []
    lines: list[Line] = []
    end_ys: dict[int, list[float]] = defaultdict(list)
    bridge_ys: dict[str, list[float]] = defaultdict(list)
    point_y: dict[int, float] = {}
    rows_y: dict[str, float] = {}
    point_rows: list[Row] = []
    junctions: list[Point] = []
    for n, (spec, slot, span) in enumerate(p.lines):
        line_top = _slot_top(slot)
        mid = line_top - HALF
        if spec.kind == "member":
            texts += [
                Text(
                    left + 4 * MM,
                    _baseline(line_top, k),
                    text,
                    size,
                    bold,
                    tone,
                    role=role,
                    index=i,
                )
                for k, (text, size, bold, tone, role, i) in enumerate(spec.halves)
            ]
            continue
        for index in spec.rows:
            lines.append(Line(rows[index], spec.kind, spec.index, spec.first, slot, span))
        key = rows[spec.rows[0]].key
        index = spec.rows[0]
        row = rows[index]
        segment_first = n == 0 or p.lines[n - 1][0].rows[-1:] != spec.rows[:1]
        if segment_first:
            texts += [
                Text(
                    left + MARK_LEFT,
                    _baseline(line_top, k),
                    text,
                    size,
                    bold,
                    tone,
                    role=role,
                    key=key,
                )
                for k, (text, size, bold, tone, role, _i) in enumerate(
                    _mark_lines(row.terminal, not spec.first, block.mark_w)
                )
            ]
            points.append((point_x, mid))
            if len(row.ends) >= 2:
                junctions.append((point_x, mid))
            point_rows.append(row)
            point_y[index] = mid
            if spec.first and row.terminal.owner_kind == "component":
                rows_y[row.terminal.terminal_id] = mid
        texts += [
            Text(
                text_x, _baseline(line_top, k), text, size, bold, tone, role=role, key=key, index=i
            )
            for k, (text, size, bold, tone, role, i) in enumerate(spec.halves)
            if text
        ]
        if spec.kind == "end":
            end_ys[index].append(mid)
            stubs.append(((point_x, mid), (text_x - 1 * MM, mid)))
            end = row.ends[spec.index]
            if end.bridge:
                bridge_ys[end.connection_id].append(mid)
    # A strip point's bridge: a bar right of the points joining the two sides' stubs.
    bar = point_x + 1.3 * MM
    for ys in bridge_ys.values():
        if len(ys) == 2:
            bridges.append(((bar, max(ys)), (bar, min(ys))))
    for index, ys in end_ys.items():
        if min(ys) < point_y[index]:
            buses.append(((point_x, point_y[index]), (point_x, min(ys))))
    # The symbol and its leads, or the placeholder.
    frame: SymbolFrame | None = None
    leads: list[Lead] = []
    placeholder: tuple[float, float, float, float] | None = None
    pins_dropped: tuple[str, ...] = ()
    if block.kind == "no_terminals":
        pass
    elif p.part > 1:
        # The symbol (or placeholder) is drawn on the block's first part only.
        pinned_ids = {a.terminal for a in block.symbol.anchors} if block.symbol else set()
        for (_x, y), row in zip(points, point_rows, strict=False):
            stub = ((point_x - STUB, y), (point_x, y))
            if row.terminal.owner_kind == "component" and row.terminal.terminal_id in pinned_ids:
                pinned.append(stub)
            else:
                dotted.append(stub)
    elif block.symbol is None:
        placeholder = (
            left + SYMBOL_LEFT,
            bottom + 0.8 * MM,
            26 * MM,
            lines_top - bottom - 1.6 * MM,
        )
        texts.append(
            Text(left + SYMBOL_LEFT + 1.5 * MM, lines_top - 3.4 * MM, SYMBOL_NOT_DEFINED, BODY_PT,
                 True, role="placeholder")
        )  # fmt: skip
        dotted += [((left + SYMBOL_LEFT + 26 * MM, y), (point_x, y)) for _x, y in points]
    else:
        symbol = block.symbol
        anchored = {t: y for t, y in rows_y.items() if t in block.anchored}
        routed = None
        if block.routed and anchored:
            routed = route(symbol, block.cut, left, lines_top, bottom, anchored, point_x, LANE)
        if routed is not None:
            frame, leads = routed
        else:
            frame = _plain_frame(symbol, left, lines_top, bottom)
        lead_ys = {lead.points[-1][1] for lead in leads}
        pinned_ids = {a.terminal for a in symbol.anchors} if block.kind == "component" else set()
        for (_x, y), row in zip(points, point_rows, strict=False):
            if y in lead_ys:
                continue
            stub = ((point_x - STUB, y), (point_x, y))
            if row.terminal.terminal_id in pinned_ids:
                pinned.append(stub)  # a pin on the symbol, but no lead drawn
            else:
                dotted.append(stub)  # no pin on the symbol
        if block.kind == "component":
            markings = {
                r.terminal.terminal_id: r.terminal.marking
                for r in rows
                if r.terminal.owner_id == block.owner_id and r.terminal.owner_kind == "component"
            }
            pins, omitted = _pin_texts(
                symbol, frame, rows_y, markings, {lead.terminal for lead in leads}, block.pins_off
            )
            texts += pins
            pins_dropped = tuple(omitted)
    return BlockPart(
        owner_kind=block.kind,
        owner_id=block.owner_id,
        title=(
            block.title if p.part == 1 or block.kind == "no_terminals" else _continued_title(block)
        ),
        notes=tuple(block.notes),
        template_ref=block.template_ref,
        symbol=block.symbol,
        place=header_place,
        lines=tuple(lines),
        part=p.part,
        continued_from=previous,
        continues_on=following,
        board_id=block.board,
        members=block.members,
        texts=tuple(texts),
        points=tuple(points),
        stubs=tuple(stubs),
        buses=tuple(buses),
        bridges=tuple(bridges),
        dotted=tuple(dotted),
        pinned=tuple(pinned),
        leads=tuple(leads),
        frame=frame,
        placeholder=placeholder,
        top=top,
        bottom=bottom,
        left=left,
        right=left + block.cols * COLUMN,
        pins_dropped=pins_dropped,
        junctions=tuple(junctions),
    )


def _text_box(x: float, y: float, text: str, size: float, right: bool) -> tuple[float, ...]:
    width = text_width(text, size)
    x0 = x - width if right else x
    return (x0, y - 0.25 * size, x0 + width, y + 0.75 * size)


Stroke = tuple[str, tuple[float, ...]]


def _strokes(
    symbol: entities.SymbolDef, frame: SymbolFrame
) -> tuple[list[Stroke], tuple[float, float, float, float]]:
    """A symbol's drawn strokes (lines, and circles as centre and radius) and their extent."""
    out: list[Stroke] = []

    def at(x: int, y: int) -> Point:
        return frame.x0 + x * frame.scale, frame.y1 - y * frame.scale

    xs: list[float] = []
    ys: list[float] = []
    for shape in symbol.shapes:
        p = shape.points
        if shape.kind == "line":
            (x1, y1), (x2, y2) = at(p[0], p[1]), at(p[2], p[3])
            out.append(("line", (x1, y1, x2, y2)))
            xs += [x1, x2]
            ys += [y1, y2]
        elif shape.kind == "rect":
            (x1, y1), (x2, y2) = at(p[0], p[1]), at(p[0] + p[2], p[1] + p[3])
            for seg in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
                out.append(("line", seg))
            xs += [x1, x2]
            ys += [y1, y2]
        elif shape.kind == "circle":
            cx, cy = at(p[0], p[1])
            r = p[2] * frame.scale
            out.append(("circle", (cx, cy, r)))
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        else:
            tx, ty = at(p[0], p[1])
            box = _text_box(tx, ty, shape.text or "", SECONDARY_PT, False)
            xs += [box[0], box[2]]
            ys += [box[1], box[3]]
    if not xs:
        x0, y0, x1, y1 = frame.box(symbol)
        return out, (x0, y0, x1, y1)
    return out, (min(xs), min(ys), max(xs), max(ys))


def _meets(stroke: Stroke, box: tuple[float, ...]) -> bool:
    """Whether a stroke passes through (or within 0.5 pt of) a text's box."""
    x0, y0, x1, y1 = box[0] - 0.5, box[1] - 0.5, box[2] + 0.5, box[3] + 0.5
    kind, v = stroke
    if kind == "circle":
        cx, cy, r = v
        nearest = math.hypot(max(x0 - cx, 0, cx - x1), max(y0 - cy, 0, cy - y1))
        farthest = max(math.hypot(x - cx, y - cy) for x in (x0, x1) for y in (y0, y1))
        return nearest <= r <= farthest
    ax, ay, bx, by = v
    # Clip the segment to the box (Liang-Barsky).
    t0, t1 = 0.0, 1.0
    dx, dy = bx - ax, by - ay
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _pin_texts(
    symbol: entities.SymbolDef,
    frame: SymbolFrame,
    rows_y: Mapping[str, float],
    markings: Mapping[str, str],
    with_lead: set[str],
    off: frozenset[str] = frozenset(),
) -> tuple[list[Text], list[str]]:
    """Each drawn terminal's marking at its anchor, or ``None`` when they do not all fit.

    A marking at an anchor must lie inside the symbol's box (so no lead,
    which stays outside it, can meet it) and clear of every other marking
    and of the symbol's own text; otherwise none is printed at the symbol
    (the rows carry every marking) and the block's header says so.
    """
    bx0, by0, bx1, by1 = frame.box(symbol)
    edge_gap = 0.3
    strokes, (sx0, sy0, sx1, sy1) = _strokes(symbol, frame)
    if with_lead:  # a drawn lead's anchor is part of what is drawn
        for anchor in symbol.anchors:
            if anchor.terminal in with_lead:
                ax, ay = frame.x0 + anchor.x * frame.scale, frame.y1 - anchor.y * frame.scale
                sx0, sy0, sx1, sy1 = min(sx0, ax), min(sy0, ay), max(sx1, ax), max(sy1, ay)
    reach = 2.5 * MM  # a marking stays this close to what is drawn
    bx0, by0 = max(bx0, sx0 - reach), max(by0, sy0 - reach)
    bx1, by1 = min(bx1, sx1 + reach), min(by1, sy1 + reach)
    taken: list[tuple[float, ...]] = []
    for shape in symbol.shapes:
        if shape.kind == "text":
            sx, sy = (
                frame.x0 + shape.points[0] * frame.scale,
                frame.y1 - shape.points[1] * frame.scale,
            )
            taken.append(_text_box(sx, sy, shape.text or "", SECONDARY_PT, False))
    out: list[Text] = []
    omitted: list[str] = []
    for anchor in symbol.anchors:
        if anchor.terminal not in rows_y:
            continue
        if anchor.terminal in off:
            omitted.append(anchor.terminal)
            continue
        pin = markings.get(anchor.terminal, "")
        ax, ay = frame.x0 + anchor.x * frame.scale, frame.y1 - anchor.y * frame.scale
        edge = anchor_edge(anchor, symbol)
        right = edge == "right"
        tx = ax - 0.5 * MM if right else ax + 0.5 * MM
        ty = {"top": ay - 2.0 * MM, "bottom": ay + 0.6 * MM}.get(edge, ay - 0.7 * MM)
        box = _text_box(tx, ty, pin, SECONDARY_PT, right)
        inside = (
            box[0] >= bx0 + edge_gap
            and box[2] <= bx1 - edge_gap
            and box[1] >= by0 + edge_gap
            and box[3] <= by1 - edge_gap
        )
        clear = all(
            box[2] + edge_gap <= o[0] or o[2] + edge_gap <= box[0]
            or box[3] + edge_gap <= o[1] or o[3] + edge_gap <= box[1]
            for o in taken
        )  # fmt: skip
        crossed = any(_meets(stroke, box) for stroke in strokes)
        if not inside or not clear or crossed:
            omitted.append(anchor.terminal)
            continue
        taken.append(box)
        out.append(
            Text(tx, ty, pin, SECONDARY_PT, tone="grey", right=right, role="pin",
                 index=1 if anchor.terminal in with_lead else 0)
        )  # fmt: skip
    return out, omitted


def _open_items(
    model: entities.DesignModelV2,
    placed: list[_Placed],
    rows: list[Row],
    row_notes: dict[int, list[tuple[str, str]]],
    groups: dict[str, list[set[Key]]],
    labels: dict[Key, str],
    wire_rows: tuple[schedules.WireRow, ...],
    places: dict[Key, Place],
    owners: dict[tuple[str, str], tuple[str, Place]],
    member_name: Callable[[str], str],
) -> tuple[OpenItem, ...]:
    components = {c.id: c for c in model.components}
    items: list[OpenItem] = []
    for p in placed:
        block = p.block
        if p.part != 1:
            continue
        if block.kind == "no_terminals":
            names = [
                f"{member_name(m)} (component {m}, template "
                f"{components[m].template_ref or 'none'}, {where(owners[('component', m)][1])})"
                for m in block.members
            ]
            items.append(
                OpenItem(
                    "no_terminals_defined",
                    block.board,
                    block.owner_id,
                    f"board {block.board}: {len(block.members)} component(s) whose templates "
                    f"define no terminals and no symbol ({SYMBOL_NOT_DEFINED}): "
                    + "; ".join(names),
                    owners[("component", block.members[0])][1],
                    block.members,
                )
            )
            items += [
                OpenItem(
                    "designation_not_set",
                    block.board,
                    m,
                    f"component {m}: designation {NOT_SET}",
                    owners[("component", m)][1],
                )
                for m in block.members
                if components[m].designation is None
            ]
            continue
        name, first_header = owners[(block.kind, block.owner_id)]
        kind, owner_id = block.kind, block.owner_id
        if any(n.startswith(f"designation {NOT_SET}") for n in block.notes):
            items.append(
                OpenItem(
                    "designation_not_set",
                    block.board,
                    owner_id,
                    f"{kind} {owner_id}: designation {NOT_SET}",
                    first_header,
                )
            )
        if block.symbol is None:
            items.append(
                OpenItem(
                    "symbol_not_defined",
                    block.board,
                    owner_id,
                    f"{name} ({kind}): {SYMBOL_NOT_DEFINED}"
                    + (
                        f" in template {block.template_ref}"
                        if block.template_ref
                        else " (no template)"
                    ),
                    first_header,
                )
            )
        if any(n.startswith("leads not drawn") for n in block.notes):
            items.append(
                OpenItem(
                    "leads_not_drawn",
                    block.board,
                    owner_id,
                    f"{name} ({kind}): leads not drawn (no crossing-free route); "
                    "pin markings are shown at the symbol",
                    first_header,
                )
            )
        if not block.specs:
            items.append(
                OpenItem(
                    "no_terminals_defined",
                    block.board,
                    owner_id,
                    f"{name} ({kind}): "
                    + (
                        f"template {block.template_ref} defines no terminals"
                        if kind == "component"
                        else "strip has no points"
                    ),
                    first_header,
                )
            )
    for index, drawn in enumerate(rows):
        for open_kind, text in row_notes[index]:
            if open_kind and open_kind != "unrealised_net":
                items.append(
                    OpenItem(
                        open_kind,
                        drawn.terminal.board_id,
                        drawn.label,
                        f"{drawn.terminal.board_id}/{drawn.label}: {text}",
                        drawn.place,
                    )
                )
    for net in model.nets:
        net_groups = groups[net.id]
        if len(net_groups) > 1:
            described = "; ".join(
                f"group {n}: " + ", ".join(sorted(labels[key] for key in group))
                for n, group in enumerate(net_groups, start=1)
            )
            items.append(
                OpenItem(
                    "unrealised_net",
                    net.board_id,
                    net.id,
                    f"net {net.signal} ({net.id}): its connections do not join its members "
                    f"({described})",
                    places[min(min(group) for group in net_groups)],
                )
            )
    for wire_row in wire_rows:
        start = places[wire_row.start_ref.key]
        for name, cell in (
            ("number", wire_row.number),
            ("cross-section mm²", wire_row.cross_section_mm2),
            ("colour", wire_row.colour),
        ):
            if cell.value is None:
                items.append(
                    OpenItem(
                        "wire_attribute_unset",
                        wire_row.board_id,
                        wire_row.wire_id,
                        f"wire {wire_row.wire_id} ({wire_row.start} to {wire_row.end}): {name} {NOT_SET}",
                        start,
                    )
                )
            elif cell.source == "assumption":
                items.append(
                    OpenItem(
                        "wire_attribute_assumed",
                        wire_row.board_id,
                        wire_row.wire_id,
                        f"wire {wire_row.wire_id} ({wire_row.start} to {wire_row.end}): "
                        f"{name} {cell_text(cell)}: {cell.assumption_statement or NOT_SET}",
                        start,
                    )
                )
    for drawn in rows:
        for end in drawn.ends:
            if end.wire_missing and drawn.key < end.far_key:
                items.append(
                    OpenItem(
                        "wire_record_missing",
                        drawn.terminal.board_id,
                        end.connection_id,
                        f"connection {end.connection_id} ({drawn.label} to {far_name(end)}) is "
                        "via wire but holds no wire record",
                        drawn.place,
                    )
                )
    rank = {kind: n for n, kind in enumerate(OPEN_KINDS)}
    items.sort(
        key=lambda item: (
            rank[item.kind],
            item.board_id,
            (item.place.sheet, item.place.column, item.place.slot) if item.place else (0, 0, 0),
            item.text,
        )
    )
    return tuple(items)
