"""Routed connection sheets of a review-only connection drawing (M5a/M5b).

Pure: a saved model and its laid-out :class:`~connection_drawing.DrawingSet`
in, a :class:`RoutedSet` out. Nothing here is specific to a device, a panel
family or a customer: every block, row, line and label comes from the
model's components, strips, terminals, connections and wires, in the order
of the review schedules, and every decision is a fixed rule over that data.

What a routed sheet is
----------------------

The cross-reference sheets (M4) stay the authoritative record. After them,
each board gets one or more *routed sheets*: its components and strips as
boxes in two fixed columns (components on the left, strips on the right; a
column that is full continues in the other, then on a new sheet), each
terminal a filled dot on its box's edge facing a central channel, and each
saved :class:`~entities.Connection` drawn as **one** orthogonal line between
its two dots:

* dot, a short fan-out piece inside the dot (several connections at one
  terminal leave it at different rows), a horizontal escape to the
  connection's own lane in the channel, the lane, an escape to the other dot;
* a connection whose other end is on another sheet (another board, or a
  board split over sheets) runs down its lane to its own track in a band at
  the foot of the sheet and ends at a continuation flag at the frame edge,
  labelled ``to <board>/<label> (sheet n, zone XY)``; the other sheet has the
  matching flag;
* where no route is found within the fixed limits (:data:`MAX_FAN`
  connections per terminal, the channel's lanes) the connection is not
  routed: a short stub with its label, the M4 cross-reference stays, and an
  ``unrouted`` open item gives the reason.

Lines are never merged: no bus, no trunk, no shared segment. Two lines meet
only inside a terminal's dot; lines of different connections cross only at
right angles, the horizontal one hopping over the vertical one. Every line
carries its label (``w-<wire id prefix>`` or ``x-<connection id prefix>``)
above each of its escapes. Line paths are schematic: they say which two
terminals a connection joins, not where a wire runs.

Determinism: every order is by schedule index or id, every limit a count.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.design.model import connection_drawing as cd
from app.design.model import entities

MM = cd.MM

# --------------------------------------------------------------------------- #
# Readability on routed sheets: a product target, not a drawing standard.
# --------------------------------------------------------------------------- #

#: Markings, line labels, continuation texts and box notes.
BODY_PT = 7.0
#: Grey detail (template and review lines of a box).
SECONDARY_PT = 6.0
#: Box titles.
TITLE_PT = 8.0

# --------------------------------------------------------------------------- #
# Geometry (PDF points)
# --------------------------------------------------------------------------- #

#: The routed lines' stroke width, distinct from every other stroke drawn.
LINE_WIDTH = 0.35
#: A terminal dot's radius, and a junction dot's (a terminal with two or more connections).
DOT_R = 0.7 * MM
JUNCTION_R = 1.1 * MM
#: The vertical unit: one fan slot (one line leaving a terminal) per unit.
U = 4.2 * MM
BOX_W = 74 * MM
#: A dot's centre lies this far outside its box's edge.
DOT_OUT = 1.6 * MM
#: Room between a dot and the channel, for the line labels.
ESCAPE = 46 * MM
#: Lanes in the channel, and the hop over a crossed line.
PITCH = 3.5 * MM
HOP_R = 0.7 * MM
#: Fan-out offsets inside a junction dot.
FAN_DX = 0.35 * MM
#: Continuation tracks in the band at the sheet's foot.
TRACK = 4.5 * MM
#: The most lines one terminal's dot can fan out to (three up, three down, one straight).
MAX_FAN = 7
#: Two lanes' runs closer than this never share a lane.
LANE_GAP = 1.5 * MM

L_BOX_X0 = cd.AREA_LEFT + 4 * MM
L_BOX_X1 = L_BOX_X0 + BOX_W
R_BOX_X1 = cd.AREA_RIGHT - 4 * MM
R_BOX_X0 = R_BOX_X1 - BOX_W
L_DOT = L_BOX_X1 + DOT_OUT
R_DOT = R_BOX_X0 - DOT_OUT
CH_X0 = L_DOT + ESCAPE
CH_X1 = R_DOT - ESCAPE
LANES = int((CH_X1 - CH_X0) // PITCH) - 3
NOTE_Y = cd.AREA_TOP - 4.5 * MM
CONTENT_TOP = cd.AREA_TOP - 8 * MM
BAND_BASE = cd.AREA_BOTTOM + 3 * MM
#: Header text: line pitch and padding inside a box.
HEAD_LINE = 3.4 * MM
PAD_X = 1.5 * MM

#: Printed at the top of every routed sheet, and on the cover.
SCHEMATIC_NOTE = (
    "ROUTED CONNECTION SHEET: line paths are schematic. Each line is one saved connection "
    "between two terminal points; it does not show the physical cable path, routing, length "
    "or landing order."
)

#: Links drawn dashed: every via but a wire or a cable core (from the model's vocabulary).
SOLID_VIAS = ("wire", "cable_core")
DASHED_VIAS = tuple(v for v in entities.VIAS if v not in SOLID_VIAS)

Key = tuple[str, str, str]
Point = tuple[float, float]


@dataclass(frozen=True)
class RoutedLine:
    """One connection drawn as one path: dot to dot, or dot to a continuation flag."""

    connection_id: str
    via: str
    label: str
    #: ``("L"|"C", x, y)`` pieces: a line to (x, y), or a hop ending at (x, y).
    path: tuple[tuple[str, float, float], ...]
    start: Point
    dashed: bool
    continued: bool = False
    marker: tuple[Point, ...] = ()


@dataclass(frozen=True)
class RoutedStub:
    """An unrouted connection's end: a short stub from its dot, with an arrowhead."""

    connection_id: str
    points: tuple[Point, ...]
    arrow: tuple[tuple[Point, Point], ...]


@dataclass(frozen=True)
class RoutedBox:
    """One part of a component's or strip's box on a routed sheet."""

    owner_kind: str
    owner_id: str
    rect: tuple[float, float, float, float]  # x, y (bottom), w, h
    placeholder: tuple[float, float, float, float] | None
    side: str
    part: int


@dataclass(frozen=True)
class RoutedSheet:
    """A routed sheet: its boxes, dots, lines, stubs and texts."""

    number: int
    board_id: str
    boxes: tuple[RoutedBox, ...]
    dots: tuple[tuple[Point, float], ...]
    lines: tuple[RoutedLine, ...]
    stubs: tuple[RoutedStub, ...]
    texts: tuple[cd.Text, ...]
    hops: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Unrouted:
    """A connection with no routed line, and why."""

    connection_id: str
    label: str
    reason: str


@dataclass(frozen=True)
class RoutedSet:
    """Every routed sheet, and what was routed, continued and not routed."""

    sheets: tuple[RoutedSheet, ...]
    places: dict[Key, cd.Place]
    labels: dict[str, str]
    routed: tuple[str, ...]
    continued: tuple[str, ...]
    unrouted: tuple[Unrouted, ...]
    open_items: tuple[cd.OpenItem, ...] = ()
    full_texts: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def short_labels(model: entities.DesignModelV2) -> dict[str, str]:
    """Each connection's line label: its wire's id, or its own, cut to a unique prefix.

    The prefix is the shortest (at least six characters after the ``w-``/``x-``
    kind) that no other connection or wire id of the model starts with, so
    the full id is always recoverable from the schedules.
    """
    wire_of = {w.connection_id: w.id for w in model.wires}
    ids = sorted({c.id for c in model.connections} | set(wire_of.values()))
    out: dict[str, str] = {}
    for connection in sorted(model.connections, key=lambda c: c.id):
        full = wire_of.get(connection.id, connection.id)
        kind = "w-" if connection.id in wire_of else "x-"
        body = full[2:] if full.startswith(kind) else full
        for n in range(6, len(body) + 1):
            token = kind + body[:n]
            if sum(1 for i in ids if i.startswith(token)) == 1 or n == len(body):
                out[connection.id] = token
                break
        else:
            out[connection.id] = kind + body
    return out


# --------------------------------------------------------------------------- #
# Blocks and their rows
# --------------------------------------------------------------------------- #


@dataclass
class _Row:
    row: cd.Row
    index: int  # schedule index
    marking: list[str]
    tag: str
    ends: list[cd.End]  # every connection at the terminal, in fan order once known

    @property
    def units(self) -> int:
        over = len(self.ends) - MAX_FAN
        # Each connection beyond the fan gets a row naming it, and one row more points
        # to the open items.
        fan = min(len(self.ends), MAX_FAN) + (over + 1 if over > 0 else 0)
        return max(1, fan, len(self.marking))


@dataclass
class _Owner:
    kind: str
    owner_id: str
    board: str
    name: str
    template_ref: str | None
    status: str  # symbol status text
    has_symbol: bool
    rows: list[_Row]
    m4_place: cd.Place | None
    order: int
    side: str = ""


@dataclass
class _Part:
    owner: _Owner
    rows: list[_Row]
    number: int
    split: bool
    side: str = ""
    unit: int = 0  # first unit in its column
    head: int = 0  # header units


@dataclass
class _SheetState:
    board: str
    parts: list[_Part] = field(default_factory=list)
    fill: dict[str, int] = field(default_factory=lambda: {"L": 0, "R": 0})


_status = cd.symbol_status


def _owners(model: entities.DesignModelV2, drawing: cd.DrawingSet) -> list[_Owner]:
    templates = {t.ref: t for t in model.templates}
    components = {c.id: c for c in model.components}
    strips = {s.id: s for s in model.strips}
    owners: dict[tuple[str, str], _Owner] = {}
    for index, row in enumerate(drawing.rows):
        t = row.terminal
        key = (t.owner_kind, t.owner_id)
        if key not in owners:
            ref: str | None
            if t.owner_kind == "component":
                ref = components[t.owner_id].template_ref
                designation = components[t.owner_id].designation
            else:
                ref = strips[t.owner_id].template_ref
                designation = strips[t.owner_id].designation
            template = templates.get(ref) if ref is not None else None
            owners[key] = _Owner(
                kind=t.owner_kind,
                owner_id=t.owner_id,
                board=t.board_id,
                name=designation.product if designation is not None else t.owner_id,
                template_ref=ref,
                status=_status(template),
                has_symbol=template is not None and template.symbol is not None,
                rows=[],
                m4_place=drawing.owners.get(key, ("", None))[1],
                order=index,
            )
        loud = [n for n in row.notes if n.startswith(("UNREALISED", "UNCONNECTED"))]
        tag = loud[0].split(":")[0] if loud else ""
        owners[key].rows.append(_Row(row, index, [], tag, list(row.ends)))
    return sorted(owners.values(), key=lambda o: (o.board, o.kind != "component", o.order))


def _marking_lines(owner: _Owner, cuts: cd.FullTexts) -> None:
    for r in owner.rows:
        room = BOX_W - 2 * PAD_X - (cd.text_width(r.tag, SECONDARY_PT) + 3 * MM if r.tag else 0)
        r.marking = cd.wrap(cd.row_marking(r.row.terminal), room, BODY_PT, True, cuts)


def _info(owner: _Owner) -> str:
    bits = [f"template {owner.template_ref}" if owner.template_ref else "no template"]
    if owner.has_symbol:
        bits.append(owner.status)
    if owner.m4_place is not None:
        bits.append(f"rows, nets and wire details: {cd.where(owner.m4_place)}")
    return "; ".join(bits)


_ROOM = BOX_W - 2 * PAD_X


def _head_lines(
    owner: _Owner, part: _Part, cuts: cd.FullTexts, refs: str
) -> list[tuple[str, float, bool, str, str]]:
    # The name with its kind: wrapped in full, its kind always on the last line.
    kind = f" ({owner.kind})" + (" continued" if part.number > 1 else "")
    title = f"{owner.board}/{owner.name}{kind}"
    out = [(t, TITLE_PT, True, "plain", "title") for t in cd.wrap(title, _ROOM, TITLE_PT, True)]
    out += [
        (t, SECONDARY_PT, False, "grey", "header")
        for t in cd.wrap(_info(owner), _ROOM, SECONDARY_PT, False, cuts)
    ]
    if refs:
        out += [
            (t, BODY_PT, False, "plain", "continues")
            for t in cd.wrap(refs, _ROOM, BODY_PT, False, cuts)
        ]
    if part.number == 1 and not owner.has_symbol:
        out.append((cd.SYMBOL_NOT_DEFINED, BODY_PT, True, "plain", "placeholder"))
    return out


_WORST_REFS = "continued from sheet 999, zone 8F; continues on sheet 999, zone 8F"


def _head_units(owner: _Owner, part: _Part) -> int:
    lines = _head_lines(owner, part, cd.FullTexts(), _WORST_REFS if part.split else "")
    return max(1, math.ceil((len(lines) * HEAD_LINE + 1.5 * MM) / U))


def _part_units(part: _Part) -> int:
    return part.head + sum(r.units for r in part.rows)


# --------------------------------------------------------------------------- #
# Placement
# --------------------------------------------------------------------------- #


def _column_top(side: str) -> float:
    return CONTENT_TOP - (U / 2 if side == "R" else 0.0)


def _band_top(height: float) -> float:
    """The band's top for tracks ``height`` high (the sum of :func:`_track_height`)."""
    if height <= 0:
        return cd.AREA_BOTTOM + 2 * MM
    return BAND_BASE + height + 1.0 * MM


#: Room for a continuation flag's text, and its line pitch when wrapped.
FLAG_ROOM = (
    (cd.AREA_RIGHT - 3.2 * MM) - (cd.AREA_RIGHT - 4 * MM - BOX_W - DOT_OUT - ESCAPE) - 7 * MM
)
FLAG_LINE = 3.0 * MM
#: Extra room above a wrapped flag text.
FLAG_SEP = 1.0 * MM


def _flag_text(end: cd.End, reference: str) -> str:
    """A continuation flag's whole text: never cut, wrapped when long."""
    side = _side_of(end)
    return f"to {end.far_board}/{_far_label(end)} ({reference})" + (
        f", {side} side" if side else ""
    )


def _flag_lines(text: str) -> list[str]:
    return cd.wrap(text, FLAG_ROOM, BODY_PT)


def _track_height(lines: int) -> float:
    """A track and the flag text above it.

    A wrapped text gets 1 mm more below the next track up, so each text reads
    with its own track (below it) rather than the one above.
    """
    return TRACK + (lines - 1) * FLAG_LINE + (FLAG_SEP if lines > 1 else 0.0)


def _sheet_keys(state: _SheetState) -> set[Key]:
    return {r.row.key for p in state.parts for r in p.rows}


def _tracks(state: _SheetState) -> dict[str, float]:
    """Each side's band height, with the longest reference a flag can name."""
    keys = _sheet_keys(state)
    out = {"L": 0.0, "R": 0.0}
    for p in state.parts:
        for r in p.rows:
            for e in r.ends[:MAX_FAN]:
                if e.far_key not in keys or e.far_board != state.board:
                    text = _flag_text(e, "sheet 9999, zone 8F")
                    out[p.side] += _track_height(len(_flag_lines(text)))
    return out


def _lanes_needed(state: _SheetState) -> int:
    """At most one lane per connection touching the sheet: never more than the channel holds."""
    return len({e.connection_id for p in state.parts for r in p.rows for e in r.ends[:MAX_FAN]})


def _fits(state: _SheetState) -> bool:
    if _lanes_needed(state) > LANES and len(state.parts) > 1:
        return False
    band = _band_top(max(_tracks(state).values()))
    for side in ("L", "R"):
        room = int((_column_top(side) - band) // U)
        if state.fill[side] > room:
            return False
    return True


def _add(state: _SheetState, part: _Part, side: str) -> bool:
    gap = 1 if state.fill[side] else 0
    part.side = side
    part.unit = state.fill[side] + gap
    part.head = _head_units(part.owner, part)
    state.parts.append(part)
    before = state.fill[side]
    state.fill[side] = part.unit + _part_units(part)
    if _fits(state):
        return True
    state.parts.pop()
    state.fill[side] = before
    return False


def _sides(owner: _Owner) -> tuple[str, str]:
    return ("L", "R") if owner.kind == "component" else ("R", "L")


def _place_whole(state: _SheetState, owner: _Owner) -> bool:
    for side in _sides(owner):
        if _add(state, _Part(owner, list(owner.rows), 1, False), side):
            return True
    return False


def _clone(state: _SheetState) -> _SheetState:
    return _SheetState(state.board, list(state.parts), dict(state.fill))


def _place_board(owners: list[_Owner]) -> list[_SheetState]:
    if not owners:
        return []
    board = owners[0].board
    # Connected components of the board's own connections, in schedule order.
    of_key = {r.row.key: n for n, o in enumerate(owners) for r in o.rows}
    parent = list(range(len(owners)))

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for n, o in enumerate(owners):
        for r in o.rows:
            for e in r.ends:
                m = of_key.get(e.far_key)
                if m is not None and find(m) != find(n):
                    parent[find(m)] = find(n)
    groups: dict[int, list[_Owner]] = {}
    for n, o in enumerate(owners):
        groups.setdefault(find(n), []).append(o)
    sheets: list[_SheetState] = [_SheetState(board)]
    for group in groups.values():
        for attempt in range(2):
            trial = _clone(sheets[-1])
            if all(_place_whole(trial, o) for o in group):
                sheets[-1] = trial
                break
            if attempt == 0 and sheets[-1].parts:
                sheets.append(_SheetState(board))
                continue
            # Larger than a sheet: block by block in breadth-first order of
            # the connections (so joined blocks share a sheet), splitting where needed.
            for o in _breadth_first(group):
                _place_split(sheets, o)
            break
    return [s for s in sheets if s.parts]


def _breadth_first(group: list[_Owner]) -> list[_Owner]:
    """The group's blocks from its first, each block's neighbours in schedule order."""
    index = {r.row.key: n for n, o in enumerate(group) for r in o.rows}
    seen = [False] * len(group)
    out: list[_Owner] = []
    for start in range(len(group)):
        if seen[start]:
            continue
        seen[start] = True
        queue = [start]
        while queue:
            n = queue.pop(0)
            out.append(group[n])
            near = sorted(
                {index[e.far_key] for r in group[n].rows for e in r.ends if e.far_key in index}
            )
            for m in near:
                if not seen[m]:
                    seen[m] = True
                    queue.append(m)
    return out


def _place_split(sheets: list[_SheetState], owner: _Owner) -> None:
    if _place_whole(sheets[-1], owner):
        return
    rows = list(owner.rows)
    number = 1
    split = True
    tries = 0
    while rows:
        state = sheets[-1]
        placed = False
        for side in _sides(owner):
            for count in range(len(rows), 0, -1):
                part = _Part(owner, rows[:count], number, split or count < len(rows))
                if _add(state, part, side):
                    rows = rows[count:]
                    number += 1
                    placed = True
                    break
            if placed:
                break
        if placed:
            tries = 0
            continue
        tries += 1
        if tries > 2:  # a single row larger than an empty sheet: cannot happen at these sizes
            raise ValueError("a row does not fit an empty routed sheet")
        sheets.append(_SheetState(owner.board))


# --------------------------------------------------------------------------- #
# Geometry of placed rows
# --------------------------------------------------------------------------- #


def _unit_top(side: str, unit: int) -> float:
    return _column_top(side) - unit * U


@dataclass
class _Slot:
    end: cd.End
    y: float
    dx: float  # fan-out offset from the dot's centre
    straight: bool


@dataclass
class _Terminal:
    row: _Row
    sheet: int
    side: str
    dot: Point
    slots: list[_Slot]
    overflow: list[cd.End]
    overflow_ys: list[float] = field(default_factory=list)
    nudge: float = 0.0  # how far the dot is moved off a zone line
    slot_ys: list[float] = field(default_factory=list)  # its lines' rows
    unit: int = 0  # its first row's unit in its column
    row_top: float = 0.0  # the top of its rows


#: A dot (and the marking centred on it) stays this far from a zone row's edge.
ZONE_CLEAR = 1.5 * MM


def _off_zone_line(y: float, lo: float, hi: float, lines: int) -> float:
    """How far to move a dot near a zone row's edge, so it and its marking sit in one zone.

    The marking (``lines`` lines, centred on the dot) stays inside its row
    (``lo``..``hi``): the nearer side of the edge is tried first, then the other.
    """
    edge_y = cd.AREA_TOP - round((cd.AREA_TOP - y) / cd.ZONE_ROW) * cd.ZONE_ROW
    d = y - edge_y
    if abs(d) >= ZONE_CLEAR:
        return 0.0
    up = ((lines - 1) / 2 * 3.0 + 0.95) * MM
    down = ((lines - 1) / 2 * 3.0 + 1.4) * MM
    near = (ZONE_CLEAR if d >= 0 else -ZONE_CLEAR) - d
    far = (-ZONE_CLEAR if d >= 0 else ZONE_CLEAR) - d
    for move in (near, far):
        if y + move + up <= hi - 0.2 * MM and y + move - down >= lo + 0.2 * MM:
            return move
    return near


#: The least distance between two adjacent dots (rows) of one block.
ROW_PITCH = 3.6 * MM


def _extent(t: _Terminal) -> tuple[float, float]:
    """How far a terminal's marking reaches above and below its dot."""
    lines = len(t.row.marking)
    return ((lines - 1) / 2 * 3.0 + 0.95) * MM, ((lines - 1) / 2 * 3.0 + 1.4) * MM


def _zone_clear(y: float, way: float) -> float:
    """How far to move ``y`` (0 when clear) to keep it ZONE_CLEAR off the nearest zone line.

    Onwards in the direction it was being moved (``way``: below 0 down, above
    0 up), else away from the line on the side it is on.
    """
    edge_y = cd.AREA_TOP - round((cd.AREA_TOP - y) / cd.ZONE_ROW) * cd.ZONE_ROW
    d = y - edge_y
    if abs(d) >= ZONE_CLEAR - 1e-6:
        return 0.0
    up = way > 0 or (way == 0 and d >= 0)
    return (ZONE_CLEAR if up else -ZONE_CLEAR) - d


def _space_rows(column: list[_Terminal], bottom: float) -> None:
    """Keep a block's dots ROW_PITCH apart where one was moved off a zone line.

    Moving one dot off a zone line can bring it within a few tenths of a
    millimetre of its neighbour. Then the rows below move down, each only as
    far as needed (so the move fades out within two or three rows); where the
    block's foot stops that, the rows move up from the foot instead. A row
    pushed near a zone line goes on past it. Where the block has no room for
    that, the rows keep their places.
    """
    n = len(column)
    if n < 2:
        return
    ys = [t.dot[1] for t in column]
    low = bottom + _extent(column[-1])[1] + 0.3 * MM
    high = column[0].row_top - _extent(column[0])[0]
    # Down from the top: each row at least ROW_PITCH below the one above, and
    # a row pushed near a zone line goes on past it.
    for i in range(1, n):
        if ys[i] > ys[i - 1] - ROW_PITCH:
            ys[i] = ys[i - 1] - ROW_PITCH
            ys[i] += _zone_clear(ys[i], -1.0)
    # Up from the foot where the box's foot stops that, the same way upwards.
    if ys[-1] < low:
        ys[-1] = low + _zone_clear(low, 1.0)
        for i in range(n - 2, -1, -1):
            if ys[i] < ys[i + 1] + ROW_PITCH:
                ys[i] = ys[i + 1] + ROW_PITCH
                ys[i] += _zone_clear(ys[i], 1.0)
    if ys[0] > high + 1e-6:
        return  # no room: keep the places as they were
    for t, y in zip(column, ys, strict=True):
        if abs(y - t.dot[1]) > 1e-9:
            t.nudge += y - t.dot[1]
            t.dot = (t.dot[0], y)


def _fan(t: _Terminal, ys: list[float]) -> None:
    """Fan-out offsets: highest-going up line and lowest-going down line nearest the dot."""
    n = len(ys)
    sign = 1.0 if t.side == "L" else -1.0
    y = t.dot[1]
    ups = [j for j in range(n) if ys[j] > y + 1e-6]
    downs = [j for j in range(n) if ys[j] < y - 1e-6]
    for j, end in enumerate(t.row.ends[:n]):
        if j in ups:
            dx = ups.index(j) * FAN_DX
        elif j in downs:
            dx = (len(downs) - 1 - downs.index(j)) * FAN_DX
        else:
            dx = 0.0
        t.slots.append(_Slot(end, ys[j], sign * dx, j not in ups and j not in downs))


# --------------------------------------------------------------------------- #
# The whole routed set
# --------------------------------------------------------------------------- #


def routed_set(model: entities.DesignModelV2, drawing: cd.DrawingSet) -> RoutedSet:
    """The routed sheets of a drawing, numbered after its cross-reference sheets."""
    cuts = cd.FullTexts()
    cuts.texts = list(drawing.full_texts)
    labels = short_labels(model)
    owners = _owners(model, drawing)
    for o in owners:
        _marking_lines(o, cuts)
    by_board: dict[str, list[_Owner]] = defaultdict(list)
    for o in owners:
        by_board[o.board].append(o)
    states: list[_SheetState] = []
    for board in sorted(by_board):
        states += _place_board(by_board[board])
    first = (drawing.sheets[-1].number + 1) if drawing.sheets else cd.FIRST_SHEET

    # Each terminal's dot, fan slots and place.
    terminals: dict[Key, _Terminal] = {}
    places: dict[Key, cd.Place] = {}
    headers: dict[tuple[str, str, int], cd.Place] = {}
    for n, state in enumerate(states):
        number = first + n
        for p in state.parts:
            column: list[_Terminal] = []
            top = _unit_top(p.side, p.unit)
            x_title = L_BOX_X0 + PAD_X if p.side == "L" else R_BOX_X0 + 1.0 * MM
            headers[(p.owner.kind, p.owner.owner_id, p.number)] = cd.Place(
                number, 0, p.unit, x_title, top - HEAD_LINE + 0.35 * TITLE_PT
            )
            unit = p.unit + p.head
            for r in p.rows:
                # Order the fan by where the far ends are: higher far ends leave higher.
                k = r.units
                ys = [_unit_top(p.side, unit + j) - U / 2 for j in range(k)]
                drawn = min(len(r.ends), MAX_FAN)
                # The dot (and its marking) sits at the middle of its lines' rows, or
                # of the whole row when the marking takes more rows than the lines.
                span = ys if len(r.marking) > drawn else ys[: max(drawn, 1)]
                dot_y = sum(span) / len(span)
                nudge = _off_zone_line(dot_y, ys[-1] - U / 2, ys[0] + U / 2, len(r.marking))
                dot_y += nudge
                dot_x = L_DOT if p.side == "L" else R_DOT
                t = _Terminal(
                    r, number, p.side, (dot_x, dot_y), [], [], nudge=nudge, slot_ys=ys[:drawn]
                )
                t.unit, t.row_top = unit, ys[0] + U / 2
                terminals[r.row.key] = t
                column.append(t)
                t.overflow_ys = ys[drawn:] if len(r.ends) > MAX_FAN else []
                unit += k
            bottom = _unit_top(p.side, p.unit + _part_units(p))
            _space_rows(column, bottom)
            for t in column:
                places[t.row.row.key] = cd.Place(number, 0, t.unit, t.dot[0], t.dot[1])
    # Fan order: by far end's place (same sheet first, higher first), then id.
    for t in terminals.values():
        r = t.row

        def order(e: cd.End, t: _Terminal = t) -> tuple[int, float, str]:
            far = places.get(e.far_key)
            if far is None or far.sheet != t.sheet:
                return (1, 0.0, e.connection_id)
            return (0, -far.y, e.connection_id)

        r.ends.sort(key=order)
        drawn = min(len(r.ends), MAX_FAN)
        _fan(t, t.slot_ys)
        t.overflow = r.ends[MAX_FAN:]

    # Every connection: routed on a sheet, continued, or not routed.
    unrouted: dict[str, str] = {}
    for t in terminals.values():
        for e in t.overflow:
            unrouted.setdefault(
                e.connection_id,
                f"more than {MAX_FAN} connections at {t.row.row.terminal.board_id}/{t.row.row.label}",
            )

    # A connection one sheet cannot route is not routed on any: route again
    # until no sheet adds one (each pass only adds, so this ends).
    base = len(cuts.texts)
    while True:
        known = dict(unrouted)
        del cuts.texts[base:]
        sheets: list[RoutedSheet] = []
        routed: list[str] = []
        continued: list[str] = []
        for n, state in enumerate(states):
            sheet = _route_sheet(
                first + n, state, terminals, places, headers, labels, unrouted, cuts
            )
            sheets.append(sheet[0])
            routed += sheet[1]
            continued += sheet[2]
        if unrouted == known:
            break

    connections = {c.id: c for c in model.connections}
    rows = {r.key: r for r in drawing.rows}
    items: list[cd.OpenItem] = []
    out_unrouted: list[Unrouted] = []
    for cid in sorted(unrouted):
        c = connections[cid]
        a, b = rows[c.start.key], rows[c.end.key]
        reason = unrouted[cid]
        out_unrouted.append(Unrouted(cid, labels[cid], reason))
        place = places.get(c.start.key)
        items.append(
            cd.OpenItem(
                "unrouted",
                a.terminal.board_id,
                cid,
                f"{labels[cid]} ({a.terminal.board_id}/{a.label} to {b.terminal.board_id}/"
                f"{b.label}) is not drawn as a line: {reason}; its stubs and "
                "cross-references are drawn",
                place,
            )
        )
    return RoutedSet(
        sheets=tuple(sheets),
        places=places,
        labels=labels,
        routed=tuple(sorted(set(routed))),
        continued=tuple(sorted(set(continued))),
        unrouted=tuple(out_unrouted),
        open_items=tuple(items),
        full_texts=tuple(cuts.texts),
    )


@dataclass
class _Run:
    """One connection's route on one sheet, before lanes."""

    cid: str
    via: str
    a: _Slot
    ta: _Terminal
    b: _Slot | None = None
    tb: _Terminal | None = None
    group: str = ""
    lane: int = -1
    track: int = -1

    @property
    def low(self) -> float:
        if self.b is None:
            return BAND_BASE
        return min(self.a.y, self.b.y)

    @property
    def high(self) -> float:
        return self.a.y if self.b is None else max(self.a.y, self.b.y)


def _escape_text(t: _Terminal, slot: _Slot, text: str) -> cd.Text:
    if t.side == "L":
        return cd.Text(t.dot[0] + 2.6 * MM, slot.y + 0.55 * MM, text, BODY_PT, role="label")
    return cd.Text(t.dot[0] - 2.6 * MM, slot.y + 0.55 * MM, text, BODY_PT, right=True, role="label")


def _route_sheet(
    number: int,
    state: _SheetState,
    terminals: dict[Key, _Terminal],
    places: dict[Key, cd.Place],
    headers: dict[tuple[str, str, int], cd.Place],
    labels: dict[str, str],
    unrouted: dict[str, str],
    cuts: cd.FullTexts,
) -> tuple[RoutedSheet, list[str], list[str]]:
    here = [terminals[r.row.key] for p in state.parts for r in p.rows]
    texts: list[cd.Text] = [
        cd.Text(cd.AREA_LEFT + 4 * MM, NOTE_Y, line, BODY_PT, True, role="note")
        for line in cd.wrap(SCHEMATIC_NOTE, cd.AREA_RIGHT - cd.AREA_LEFT - 8 * MM, BODY_PT, True)
    ]
    assert len(texts) == 1, "the schematic note fits one line"
    boxes: list[RoutedBox] = []
    dots: list[tuple[Point, float]] = []
    # Boxes, headers and rows.
    for p in state.parts:
        o = p.owner
        top = _unit_top(p.side, p.unit)
        bottom = _unit_top(p.side, p.unit + _part_units(p))
        x0 = L_BOX_X0 if p.side == "L" else R_BOX_X0
        refs = []
        prev = headers.get((o.kind, o.owner_id, p.number - 1))
        nxt = headers.get((o.kind, o.owner_id, p.number + 1))
        if prev is not None:
            refs.append(f"continued from {prev.reference(number)}")
        if nxt is not None:
            refs.append(f"continues on {nxt.reference(number)}")
        head = _head_lines(o, p, cuts, "; ".join(refs))
        tx = x0 + PAD_X if p.side == "L" else x0 + 1.0 * MM
        placeholder = None
        for k, (text, size, bold, tone, role) in enumerate(head):
            y = top - HEAD_LINE * (k + 1) + 0.6 * MM
            texts.append(cd.Text(tx, y, text, size, bold, tone, role=role))
            if role == "placeholder":
                w = cd.text_width(text, size, bold)
                placeholder = (tx - 0.8 * MM, y - 1.0 * MM, w + 1.6 * MM, 0.75 * size + 1.6 * MM)
        boxes.append(
            RoutedBox(
                o.kind, o.owner_id, (x0, bottom, BOX_W, top - bottom), placeholder, p.side, p.number
            )
        )
        for r in p.rows:
            t = terminals[r.row.key]
            junction = len(r.row.ends) >= 2
            dots.append((t.dot, JUNCTION_R if junction else DOT_R))
            n = len(r.marking)
            for k, line in enumerate(r.marking):
                y = t.dot[1] - 0.9 * MM + ((n - 1) / 2 - k) * 3.0 * MM
                if p.side == "L":
                    texts.append(
                        cd.Text(
                            L_BOX_X1 - PAD_X,
                            y,
                            line,
                            BODY_PT,
                            True,
                            right=True,
                            role="marking",
                            key=r.row.key,
                        )
                    )
                else:
                    texts.append(
                        cd.Text(
                            R_BOX_X0 + PAD_X, y, line, BODY_PT, True, role="marking", key=r.row.key
                        )
                    )
            if r.tag:
                y = t.dot[1] - 0.8 * MM
                if p.side == "L":
                    texts.append(
                        cd.Text(
                            L_BOX_X0 + PAD_X,
                            y,
                            r.tag,
                            SECONDARY_PT,
                            True,
                            "loud",
                            role="note",
                            key=r.row.key,
                        )
                    )
                else:
                    texts.append(
                        cd.Text(
                            R_BOX_X1 - PAD_X,
                            y,
                            r.tag,
                            SECONDARY_PT,
                            True,
                            "loud",
                            right=True,
                            role="note",
                            key=r.row.key,
                        )
                    )
            for n, over in enumerate(t.overflow):
                slot = _Slot(over, t.overflow_ys[n], 0.0, False)
                texts.append(_escape_text(t, slot, f"{labels[over.connection_id]} not routed"))
            if t.overflow:
                slot = _Slot(t.overflow[0], t.overflow_ys[len(t.overflow)], 0.0, False)
                texts.append(_escape_text(t, slot, "(see the open items)"))

    # Runs.
    keys = {t.row.row.key for t in here}
    runs: dict[str, _Run] = {}
    for t in here:
        for slot in t.slots:
            cid = slot.end.connection_id
            if cid in unrouted:
                continue
            if cid in runs:
                run = runs[cid]
                run.b, run.tb = slot, t
                continue
            run = _Run(cid, slot.end.via, slot, t)
            runs[cid] = run
    for run in runs.values():
        if run.b is None:
            far = terminals.get(run.a.end.far_key)
            local = run.a.end.far_key in keys and far is not None and far.sheet == number
            if local:
                # The far end is here but has no slot (its terminal overflowed).
                unrouted.setdefault(run.cid, "the other end's terminal has too many connections")
                run.group = "X"
                continue
            run.group = "Lc" if run.ta.side == "L" else "Rc"
        else:
            sides = {run.ta.side, run.tb.side}  # type: ignore[union-attr]
            run.group = "LL" if sides == {"L"} else "RR" if sides == {"R"} else "LR"
    live = [r for r in runs.values() if r.group != "X"]
    while True:
        order = _assign_lanes(live)
        if order is not None:
            break
        # Too many lanes: the longest run is not routed (ties by id).
        worst = max(live, key=lambda r: (r.high - r.low, r.cid))
        unrouted.setdefault(worst.cid, "no free lane in the sheet's channel")
        live.remove(worst)
    lane_x = _lane_xs(live, order)
    lines: list[RoutedLine] = []
    stubs: list[RoutedStub] = []
    routed_ids: list[str] = []
    continued_ids: list[str] = []
    # Tracks, bottom up per side, each as high as its flag's wrapped text.
    flags: dict[str, list[str]] = {}
    track_y: dict[str, float] = {}
    for group in ("Lc", "Rc"):
        y = BAND_BASE
        for run in sorted((r for r in live if r.group == group), key=lambda r: r.track):
            reference = places[run.a.end.far_key].reference(-1)
            flags[run.cid] = _flag_lines(_flag_text(run.a.end, reference))
            track_y[run.cid] = y
            y += _track_height(len(flags[run.cid]))
    geoms: dict[str, list[Point]] = {}
    for run in sorted(live, key=lambda r: r.cid):
        x = lane_x[run.lane]
        pts = _escape(run.ta, run.a, x)
        if run.b is not None:
            back = _escape(run.tb, run.b, x)  # type: ignore[arg-type]
            pts = pts + list(reversed(back))
            routed_ids.append(run.cid)
        else:
            y = track_y[run.cid]
            edge = cd.AREA_LEFT + 3.2 * MM if run.group == "Lc" else cd.AREA_RIGHT - 3.2 * MM
            pts = [*pts, (x, y), (edge, y)]
            continued_ids.append(run.cid)
        geoms[run.cid] = _clean(pts)
    hops = _hops(geoms)
    for run in sorted(live, key=lambda r: r.cid):
        pts = geoms[run.cid]
        path: list[tuple[str, float, float]] = []
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if abs(a[1] - b[1]) < 1e-6:
                xs = sorted(
                    (h for h in hops.get((run.cid, i), [])),
                    reverse=b[0] < a[0],
                )
                for hx in xs:
                    s = 1 if b[0] > a[0] else -1
                    path.append(("L", hx - s * HOP_R, a[1]))
                    path.append(("C", hx + s * HOP_R, a[1]))
            path.append(("L", b[0], b[1]))
        label = labels[run.cid]
        marker: tuple[Point, ...] = ()
        if run.b is None:
            end = pts[-1]
            s = -1 if run.group == "Lc" else 1
            marker = (
                (end[0], end[1] + 0.9 * MM),
                (end[0] + s * 2 * MM, end[1]),
                (end[0], end[1] - 0.9 * MM),
            )
            # The whole text, wrapped above the track; its lines share a left edge.
            wrapped = flags[run.cid]
            width = max(cd.text_width(t, BODY_PT) for t in wrapped)
            x0 = end[0] + 1.0 * MM if run.group == "Lc" else end[0] - 1.0 * MM - width
            for k, line in enumerate(wrapped):
                y = end[1] + 0.6 * MM + (len(wrapped) - 1 - k) * FLAG_LINE
                texts.append(cd.Text(x0, y, line, BODY_PT, role="far"))
        lines.append(
            RoutedLine(
                run.cid,
                run.via,
                label,
                tuple(path),
                pts[0],
                run.via in DASHED_VIAS,
                continued=run.b is None,
                marker=marker,
            )
        )
        texts.append(_escape_text(run.ta, run.a, label))
        if run.b is not None:
            texts.append(_escape_text(run.tb, run.b, label))  # type: ignore[arg-type]
    # Stubs of connections not routed, at every end on this sheet with a slot.
    for t in here:
        for slot in t.slots:
            cid = slot.end.connection_id
            if cid not in unrouted:
                continue
            s = 1 if t.side == "L" else -1
            start = (t.dot[0] + slot.dx, t.dot[1])
            pts = [start] if slot.straight else [start, (start[0], slot.y)]
            tip = (t.dot[0] + s * 8 * MM, slot.y)
            pts.append(tip)
            arrow = (
                ((tip[0] - s * 1.5 * MM, slot.y + 0.8 * MM), tip),
                ((tip[0] - s * 1.5 * MM, slot.y - 0.8 * MM), tip),
            )
            stubs.append(RoutedStub(cid, tuple(_clean(pts)), arrow))
            # The text follows the arrowhead on the stub's own line, clear of it.
            texts.append(
                cd.Text(
                    tip[0] + s * 1.2 * MM,
                    slot.y - 0.9 * MM,
                    f"{labels[cid]} not routed",
                    BODY_PT,
                    right=t.side == "R",
                    role="label",
                )
            )
    sheet = RoutedSheet(
        number=number,
        board_id=state.board,
        boxes=tuple(boxes),
        dots=tuple(dots),
        lines=tuple(lines),
        stubs=tuple(stubs),
        texts=tuple(texts),
        hops=tuple(
            sorted((x, y) for (cid, i), xs in hops.items() for x in xs for y in [geoms[cid][i][1]])
        ),
    )
    return sheet, routed_ids, continued_ids


def _far_label(end: cd.End) -> str:
    """The far end's ``<designation>:<marking>``, without a strip point's side and level."""
    label = end.far_label
    if end.far_key[0] == "point":
        for side in entities.SIDES:
            if f" {side} L" in label:
                return label.split(f" {side} L")[0]
    return label


def _side_of(end: cd.End) -> str:
    return end.far_key[2] if end.far_key[0] == "point" else ""


def _escape(t: _Terminal, slot: _Slot, lane_x: float) -> list[Point]:
    """From a terminal's dot to its escape's end at the lane."""
    start = (t.dot[0] + slot.dx, t.dot[1])
    pts = [start]
    if not slot.straight:
        pts.append((start[0], slot.y))
    pts.append((lane_x, slot.y))
    return pts


def _clean(pts: Sequence[Point]) -> list[Point]:
    out: list[Point] = []
    for p in pts:
        if out and math.dist(out[-1], p) < 1e-6:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - p[0]) < 1e-6) or (
                abs(a[1] - b[1]) < 1e-6 and abs(b[1] - p[1]) < 1e-6
            ):
                out[-1] = p
                continue
        out.append(p)
    return out


def _assign_lanes(runs: list[_Run]) -> int | None:
    """Lanes for every run (and tracks for continuations); the lane count, or ``None`` if too many."""

    def apart(run: _Run, other: _Run) -> bool:
        # Two runs share a lane only well apart, and never when they meet at a
        # terminal: one lane through a junction would read as one line.
        ends = {id(run.ta), id(run.tb)}
        if ends & {id(other.ta), id(other.tb)}:
            return False
        return run.low > other.high + LANE_GAP or other.low > run.high + LANE_GAP

    def pack(group: list[_Run]) -> list[list[_Run]]:
        lanes: list[list[_Run]] = []
        for run in group:
            for lane in lanes:
                if all(apart(run, o) for o in lane):
                    lane.append(run)
                    break
            else:
                lanes.append([run])
        return lanes

    by = defaultdict(list)
    for run in runs:
        by[run.group].append(run)
    # Left continuations: the lowest escape leftmost, and the highest track.
    lc = sorted(by["Lc"], key=lambda r: (r.a.y, r.cid))
    for n, run in enumerate(lc):
        run.track = len(lc) - 1 - n
    ll = sorted(by["LL"], key=lambda r: (r.high - r.low, r.low, r.cid))
    # Left lanes, nearest the left column first: same-side lanes inserted
    # among the continuations (whose own order keeps tracks from crossing).
    order: list[list[_Run]] = _nearest_first([[r] for r in lc], pack(ll))
    lr = sorted(by["LR"], key=lambda r: (r.low, r.high, r.cid))
    order += _fewest_crossings([[r] for r in lr])  # one lane each: lanes reorder freely
    rr = sorted(by["RR"], key=lambda r: (r.high - r.low, r.low, r.cid))
    # Right continuations: the highest escape leftmost; the rightmost lane takes the highest track.
    rc = sorted(by["Rc"], key=lambda r: (-r.a.y, r.cid))
    for n, run in enumerate(rc):
        run.track = n
    order += list(reversed(_nearest_first([[r] for r in reversed(rc)], pack(rr))))
    if len(order) > LANES:
        return None
    for n, lane in enumerate(order):
        for run in lane:
            run.lane = n
    return len(order)


def _escape_ys(run: _Run) -> list[float]:
    """The heights of a run's escapes (one for a continuation)."""
    return [run.a.y] if run.b is None else [run.a.y, run.b.y]


def _side_crossings(near: list[_Run], far: list[_Run]) -> int:
    """Crossings when lane ``near`` lies nearer its column than lane ``far`` (one side's runs).

    Each escape of a farther lane crosses every nearer lane whose span it passes.
    """
    return sum(p.low < y < p.high for p in near for q in far for y in _escape_ys(q))


def _nearest_first(fixed: list[list[_Run]], free: list[list[_Run]]) -> list[list[_Run]]:
    """One side's lanes, nearest its column first, with the fewest crossings found by insertion.

    ``fixed`` keep their order; each ``free`` lane in turn goes where the
    order's crossings are fewest (ties: farthest from the column, so that of
    equal lanes the shorter, placed first, stays nearer). Deterministic.
    """
    out = list(fixed)
    for lane in free:
        best: tuple[int, int] | None = None
        for i in range(len(out) + 1):
            c = sum(_side_crossings(o, lane) for o in out[:i]) + sum(
                _side_crossings(lane, o) for o in out[i:]
            )
            if best is None or c <= best[0]:
                best = (c, i)
        assert best is not None
        out.insert(best[1], lane)
    return out


def _sides_y(run: _Run) -> tuple[float, float]:
    """A left-right run's escape heights: (left, right)."""
    assert run.b is not None
    return (run.a.y, run.b.y) if run.ta.side == "L" else (run.b.y, run.a.y)


def _crossings(left: list[_Run], right: list[_Run]) -> int:
    """Crossings when lane ``left`` lies left of lane ``right`` (left-right runs)."""
    n = 0
    for p in left:
        for q in right:
            _pl, pr = _sides_y(p)
            ql, _qr = _sides_y(q)
            n += q.low < pr < q.high  # p's right escape crosses q's lane
            n += p.low < ql < p.high  # q's left escape crosses p's lane
    return n


def _fewest_crossings(lanes: list[list[_Run]]) -> list[list[_Run]]:
    """The lanes ordered by insertion, each where it adds the fewest crossings (ties: earliest)."""
    out: list[list[_Run]] = []
    for lane in lanes:
        best, where = None, 0
        for i in range(len(out) + 1):
            cost = sum(_crossings(o, lane) for o in out[:i]) + sum(
                _crossings(lane, o) for o in out[i:]
            )
            if best is None or cost < best:
                best, where = cost, i
        out.insert(where, lane)
    return out


def _lane_xs(runs: list[_Run], count: int) -> dict[int, float]:
    """Lane positions in the channel.

    Left-side lanes from the channel's left, right-side ones from its right,
    lanes joining the two sides centred between them.
    """
    kind: dict[int, str] = {}
    for run in runs:
        kind[run.lane] = (
            "L" if run.group in ("Lc", "LL") else "R" if run.group in ("RR", "Rc") else "M"
        )
    left = [n for n in range(count) if kind.get(n) == "L"]
    middle = [n for n in range(count) if kind.get(n) == "M"]
    right = [n for n in range(count) if kind.get(n) == "R"]
    out: dict[int, float] = {}
    for i, n in enumerate(left):
        out[n] = CH_X0 + (i + 1) * PITCH
    for j, n in enumerate(right):
        out[n] = CH_X1 - (len(right) - j) * PITCH
    lo = CH_X0 + (len(left) + 1) * PITCH
    hi = CH_X1 - (len(right) + 1) * PITCH
    start = max(lo, (lo + hi) / 2 - (len(middle) - 1) / 2 * PITCH)
    for k, n in enumerate(middle):
        out[n] = start + k * PITCH
    return out


def _hops(geoms: dict[str, list[Point]]) -> dict[tuple[str, int], list[float]]:
    """Where each horizontal piece hops a vertical piece of another connection."""
    vertical: list[tuple[str, float, float, float]] = []
    for cid, pts in geoms.items():
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) > 1e-6:
                vertical.append((cid, a[0], min(a[1], b[1]), max(a[1], b[1])))
    out: dict[tuple[str, int], list[float]] = {}
    for cid, pts in geoms.items():
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if abs(a[1] - b[1]) > 1e-6:
                continue
            x0, x1 = sorted((a[0], b[0]))
            y = a[1]
            for other, x, y0, y1 in vertical:
                if other != cid and x0 < x < x1 and y0 < y < y1:
                    out.setdefault((cid, i), []).append(x)
    return out
