"""The layout of a review-only connection drawing of a ``pp-design/2`` model (M4).

Pure and framework-free: a saved model (as read) in, a :class:`DrawingSet`
out. Nothing is rendered here (``app.design.connection_drawing_pdf`` draws a
``DrawingSet``), nothing is read from a database or a client, and nothing is
specific to a device, a panel family or a customer: every block, row and
cross-reference is derived from the model's components, strips, terminals,
nets, connections and wires, in the order of the review schedules.

The drawing's terminals and wires *are* the schedules' rows:
:attr:`DrawingSet.terminal_rows` is ``schedules.terminal_schedule(model)`` and
:attr:`DrawingSet.wire_rows` is ``schedules.wire_schedule(model)``, so a
drawing and the two schedules of one model cannot disagree on a terminal, a
net or a wire. The texts printed for each are made here (:func:`cell_text`,
the ``*_text`` helpers) from the model's values only. A value the model does
not hold is printed as :data:`NOT_SET`; nothing is filled in or guessed.

Geometry is abstract. A sheet's drawing area is :data:`COLUMNS` block columns
of :data:`SLOTS` slots; the zone grid divides it into :data:`ZONE_COLUMNS`
numbered columns and :data:`ZONE_ROWS` lettered rows. A block is a header
slot then one slot per line of its rows; a block that does not fit in a
column's remaining slots continues in the next column or on the next sheet,
and says so. A board never shares a sheet with another.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.design.model import entities, schedules

#: What is printed where the model holds no value.
NOT_SET = "not set"

#: What is printed where a template defines no symbol.
SYMBOL_NOT_DEFINED = "symbol not defined"

#: The first drawing sheet's number: sheet 1 is the cover.
FIRST_SHEET = 2

#: Block columns per sheet, and slots per column.
COLUMNS = 2
SLOTS = 36

#: The zone grid: numbered columns across, lettered rows down.
ZONE_COLUMNS = 8
ZONE_ROWS = 6
ZONE_LETTERS = "ABCDEF"

#: Where in its block column a terminal's connection point sits, in zone
#: columns from the column's left edge (the symbol sits in the first).
POINT_ZONE_OFFSET = 1


@dataclass(frozen=True)
class Place:
    """Where a slot is: sheet number, block column, slot index, and its zone."""

    sheet: int
    column: int
    slot: int

    @property
    def zone(self) -> str:
        """The zone of a terminal's connection point in this slot, e.g. ``"2B"``."""
        number = self.column * (ZONE_COLUMNS // COLUMNS) + POINT_ZONE_OFFSET + 1
        letter = ZONE_LETTERS[self.slot * ZONE_ROWS // SLOTS]
        return f"{number}{letter}"

    @property
    def header_zone(self) -> str:
        """The zone of a block header in this slot: its column's first zone, e.g. ``"1B"``."""
        number = self.column * (ZONE_COLUMNS // COLUMNS) + 1
        letter = ZONE_LETTERS[self.slot * ZONE_ROWS // SLOTS]
        return f"{number}{letter}"

    def reference(self, here: int, *, header: bool = False) -> str:
        """How a cross-reference to this place reads from sheet ``here``.

        ``header`` names a block header's zone (a continuation), not a point's.
        """
        zone = self.header_zone if header else self.zone
        if self.sheet == here:
            return f"this sheet, zone {zone}"
        return f"sheet {self.sheet}, zone {zone}"


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


@dataclass(frozen=True)
class Row:
    """One terminal row of a block: the terminal schedule's row, its ends and notes.

    ``notes`` are what the row must say before its ends: unconnected,
    unrealised, alone in its net. ``place`` is its first slot.
    """

    terminal: schedules.TerminalRow
    key: tuple[str, str, str]
    label: str
    place: Place
    notes: tuple[str, ...]
    ends: tuple[End, ...]


@dataclass(frozen=True)
class Line:
    """One slot of a block part: a row's note, a row's end, or a row with neither."""

    row: Row
    kind: str  # "note", "end"
    index: int
    first: bool


@dataclass(frozen=True)
class BlockPart:
    """The part of a block drawn in one column of one sheet.

    Attributes:
        owner_kind: ``"component"`` or ``"strip"``.
        owner_id: Its id.
        title: The owner's name (designation product or id) and kind.
        notes: What the header says beyond the title (designation not set,
            template, no terminals defined).
        template_ref: The template, or ``None``.
        symbol: The template's symbol, or ``None`` (a placeholder is drawn).
        anchors: For a component, the symbol anchor of each terminal id.
        place: The header slot.
        lines: The slots after the header.
        part: 1 for the first part; more for a continuation.
        continued_from: The previous part's place, or ``None``.
        continues_on: The next part's place, or ``None``.
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


@dataclass(frozen=True)
class Sheet:
    """A drawing sheet: its number, board and the block parts on it."""

    number: int
    board_id: str
    parts: tuple[BlockPart, ...]


@dataclass(frozen=True)
class OpenItem:
    """Something the drawing shows as not resolved, listed again on its own sheets.

    ``kind`` is one of :data:`OPEN_KINDS`; ``place`` is where it is drawn, or
    ``None`` for an item about a whole definition.
    """

    kind: str
    board_id: str
    subject: str
    text: str
    place: Place | None


#: The kinds of open item, in the order they are listed.
OPEN_KINDS = (
    "unrealised_net",
    "unconnected_required",
    "unconnected_unresolved",
    "unconnected_not_required",
    "unconnected_strip_point",
    "wire_record_missing",
    "wire_attribute_unset",
    "wire_attribute_assumed",
    "designation_not_set",
    "symbol_not_defined",
    "no_terminals_defined",
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
    """

    sheets: tuple[Sheet, ...]
    terminal_rows: tuple[schedules.TerminalRow, ...]
    wire_rows: tuple[schedules.WireRow, ...]
    rows: tuple[Row, ...]
    places: dict[tuple[str, str, str], Place]
    open_items: tuple[OpenItem, ...]
    connections: tuple[str, ...]
    nets: tuple[str, ...]

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
    """A wire's id and its three attributes, each as :func:`cell_text`."""
    return (
        f"wire {wire.wire_id}: no. {cell_text(wire.number)}; "
        f"mm² {cell_text(wire.cross_section_mm2)}; colour {cell_text(wire.colour)}"
    )


def end_texts(end: End, here: int) -> tuple[str, str]:
    """The two lines an end is printed as, read from sheet ``here``.

    The first names the far end and where it is; the second the wire (or the
    cable core, or that the connection has no wire because of its ``via``).
    """
    first = f"to {end.far_label} ({end.far_place.reference(here)}) via {end.via}"
    if end.wire is not None:
        second = wire_text(end.wire)
    elif end.wire_missing:
        second = f"connection {end.connection_id}: WIRE RECORD MISSING"
    elif end.core is not None:
        label = end.core.label if end.core.label is not None else NOT_SET
        second = f"cable {end.core.cable_id} core {end.core.index}, core label {label}"
    else:
        second = f"connection {end.connection_id}: no wire ({end.via})"
    return first, second


def row_marking(row: schedules.TerminalRow) -> str:
    """A row's terminal as printed beside its point: marking, and strip side and level."""
    if row.side is None:
        return row.marking
    return f"{row.marking} {row.side} L{row.level}"


def row_net(row: schedules.TerminalRow) -> str:
    """A row's net as printed under its marking."""
    return f"net {row.signal}" if row.signal is not None else "net: none"


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #


def _groups(model: entities.DesignModelV2) -> dict[str, list[set[tuple[str, str, str]]]]:
    """Each net's members grouped by its own connections (graph components)."""
    parent: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    def find(key: tuple[str, str, str]) -> tuple[str, str, str]:
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
    out: dict[str, list[set[tuple[str, str, str]]]] = {}
    for net in model.nets:
        by_root: dict[tuple[str, str, str], set[tuple[str, str, str]]] = defaultdict(set)
        for member in net.members:
            by_root[find(member.key)].add(member.key)
        out[net.id] = sorted(by_root.values(), key=lambda group: sorted(group))
    return out


def _row_key(row: schedules.TerminalRow) -> tuple[str, str, str]:
    if row.owner_kind == "component":
        return entities.TerminalRef(component=row.owner_id, terminal=row.terminal_id).key
    return entities.TerminalRef(point=row.terminal_id, side=row.side).key


def _label(row: schedules.TerminalRow) -> str:
    """The schedules' label, with a strip point's side and level (labels alone may repeat)."""
    name = row.designation.product if row.designation is not None else row.owner_id
    return f"{name}:{row_marking(row)}"


def _notes(
    row: schedules.TerminalRow,
    key: tuple[str, str, str],
    groups: dict[str, list[set[tuple[str, str, str]]]],
    has_ends: bool,
) -> list[tuple[str, str]]:
    """``(open kind, text)`` for each note a row carries; kind ``""`` for a plain note."""
    notes: list[tuple[str, str]] = []
    if row.net_id is None:
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


@dataclass
class _Cursor:
    sheet: int
    column: int
    slot: int


@dataclass
class _Pending:
    owner_kind: str
    owner_id: str
    title: str
    notes: tuple[str, ...]
    template_ref: str | None
    symbol: entities.SymbolDef | None
    header: Place
    lines: list[tuple[int, str, int, bool, Place]]  # row index, kind, index, first, place
    part: int


def drawing_set(model: entities.DesignModelV2) -> DrawingSet:
    """The connection drawing of a model, laid out.

    Args:
        model: A v2 model, as read (anchored).

    Returns:
        The drawing set: see the module docstring.
    """
    terminal_rows = schedules.terminal_schedule(model)
    wire_rows = schedules.wire_schedule(model)
    wires_by_connection = {row.connection_id: row for row in wire_rows}
    templates = {t.ref: t for t in model.templates}
    groups = _groups(model)
    keys = [_row_key(row) for row in terminal_rows]
    labels = {key: _label(row) for key, row in zip(keys, terminal_rows, strict=True)}
    ends_of: dict[tuple[str, str, str], list[tuple[entities.Connection, entities.TerminalRef]]] = (
        defaultdict(list)
    )
    for connection in sorted(model.connections, key=lambda c: c.id):
        ends_of[connection.start.key].append((connection, connection.end))
        ends_of[connection.end.key].append((connection, connection.start))
    nets = {n.id: n for n in model.nets}

    # Owners in schedule order, each with its rows' indexes.
    owners: list[tuple[str, str, str, list[int]]] = []  # board, kind, id, row indexes
    for index, row in enumerate(terminal_rows):
        if owners and owners[-1][1:3] == (row.owner_kind, row.owner_id):
            owners[-1][3].append(index)
        else:
            owners.append((row.board_id, row.owner_kind, row.owner_id, [index]))
    # Owners with no terminal row (a template with no terminals, a strip with no points).
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

    row_notes: dict[int, list[tuple[str, str]]] = {
        index: _notes(row, keys[index], groups, bool(ends_of.get(keys[index])))
        for index, row in enumerate(terminal_rows)
    }

    cursor = _Cursor(sheet=FIRST_SHEET - 1, column=COLUMNS, slot=SLOTS)
    board: str | None = None
    pending: list[_Pending] = []
    sheet_boards: dict[int, str] = {}

    def new_column(force_sheet: bool) -> None:
        if force_sheet or cursor.column + 1 >= COLUMNS:
            cursor.sheet += 1
            cursor.column = 0
        else:
            cursor.column += 1
        cursor.slot = 0
        sheet_boards[cursor.sheet] = board or ""

    open_items: list[OpenItem] = []
    for board_id, kind, owner_id, indexes in owners:
        if board_id != board:
            board = board_id
            new_column(force_sheet=True)
        if kind == "component":
            component = components[owner_id]
            designation = component.designation
            template_ref: str | None = component.template_ref
        else:
            strip = strips[owner_id]
            designation = strip.designation
            template_ref = strip.template_ref
        template = templates.get(template_ref) if template_ref is not None else None
        symbol = template.symbol if template is not None else None
        name = designation.product if designation is not None else owner_id
        header_notes: list[str] = [f"template {template_ref}" if template_ref else "no template"]
        if designation is None:
            header_notes.append(f"designation {NOT_SET} (id {owner_id})")
        if not indexes:
            header_notes.append(
                "no terminals defined in its template" if kind == "component" else "no points"
            )
        lines: list[tuple[int, str, int]] = []
        for index in indexes:
            notes = row_notes[index]
            for n in range(len(notes)):
                lines.append((index, "note", n))
            for n in range(len(ends_of.get(keys[index], []))):
                lines.append((index, "end", n))
        # A block with no rows still reserves one slot below its header.
        needed = 1 + max(len(lines), 1)
        if cursor.slot + needed > SLOTS and cursor.slot > 0:
            new_column(force_sheet=False)
        part = 1
        position = 0
        while True:
            header = Place(cursor.sheet, cursor.column, cursor.slot)
            cursor.slot += 1
            room = SLOTS - cursor.slot
            chunk = lines[position : position + max(room, 0)]
            placed: list[tuple[int, str, int, bool, Place]] = []
            for offset, (row_index, line_kind, n) in enumerate(chunk, start=position):
                first = offset == 0 or lines[offset - 1][0] != row_index
                placed.append(
                    (
                        row_index,
                        line_kind,
                        n,
                        first,
                        Place(cursor.sheet, cursor.column, cursor.slot),
                    )
                )
                cursor.slot += 1
            if not lines:
                cursor.slot += 1
            pending.append(
                _Pending(
                    owner_kind=kind,
                    owner_id=owner_id,
                    title=f"{name} ({kind})",
                    notes=tuple(header_notes),
                    template_ref=template_ref,
                    symbol=symbol,
                    header=header,
                    lines=placed,
                    part=part,
                )
            )
            position += len(chunk)
            if position >= len(lines):
                break
            part += 1
            new_column(force_sheet=False)
        first_header = next(
            p.header
            for p in pending
            if p.owner_kind == kind and p.owner_id == owner_id and p.part == 1
        )
        if designation is None:
            open_items.append(
                OpenItem(
                    "designation_not_set",
                    board_id,
                    owner_id,
                    f"{kind} {owner_id}: designation {NOT_SET}",
                    first_header,
                )
            )
        if symbol is None:
            open_items.append(
                OpenItem(
                    "symbol_not_defined",
                    board_id,
                    owner_id,
                    f"{name} ({kind}): {SYMBOL_NOT_DEFINED}"
                    + (f" in template {template_ref}" if template_ref else " (no template)"),
                    first_header,
                )
            )
        if not indexes:
            open_items.append(
                OpenItem(
                    "no_terminals_defined",
                    board_id,
                    owner_id,
                    f"{name} ({kind}): "
                    + (
                        f"template {template_ref} defines no terminals"
                        if kind == "component"
                        else "strip has no points"
                    ),
                    first_header,
                )
            )

    # The place of each row: its first line, or (no lines) the slot after its header.
    places: dict[tuple[str, str, str], Place] = {}
    for p in pending:
        for row_index, _kind, _n, _first, place in p.lines:
            places.setdefault(keys[row_index], place)

    rows: list[Row] = []
    for index, row in enumerate(terminal_rows):
        key = keys[index]
        ends: list[End] = []
        for connection, far in ends_of.get(key, []):
            net = nets[connection.net_id]
            wire = wires_by_connection.get(connection.id)
            ends.append(
                End(
                    connection_id=connection.id,
                    via=connection.via,
                    net_id=net.id,
                    signal=net.signal,
                    far_key=far.key,
                    far_label=labels.get(far.key, NOT_SET),
                    far_place=places[far.key],
                    wire=wire,
                    wire_missing=connection.via == "wire" and wire is None,
                    core=connection.core,
                )
            )
        rows.append(
            Row(
                terminal=row,
                key=key,
                label=labels[key],
                place=places[key],
                notes=tuple(text for _kind, text in row_notes[index]),
                ends=tuple(ends),
            )
        )
        for open_kind, text in row_notes[index]:
            if open_kind and open_kind != "unrealised_net":
                open_items.append(
                    OpenItem(
                        open_kind, row.board_id, labels[key], f"{labels[key]}: {text}", places[key]
                    )
                )
    for net in model.nets:
        net_groups = groups[net.id]
        if len(net_groups) > 1:
            described = "; ".join(
                f"group {n}: " + ", ".join(sorted(labels[key] for key in group))
                for n, group in enumerate(net_groups, start=1)
            )
            open_items.append(
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
                open_items.append(
                    OpenItem(
                        "wire_attribute_unset",
                        wire_row.board_id,
                        wire_row.wire_id,
                        f"wire {wire_row.wire_id} ({wire_row.start} to {wire_row.end}): "
                        f"{name} {NOT_SET}",
                        start,
                    )
                )
            elif cell.source == "assumption":
                open_items.append(
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
                open_items.append(
                    OpenItem(
                        "wire_record_missing",
                        drawn.terminal.board_id,
                        end.connection_id,
                        f"connection {end.connection_id} ({drawn.label} to {end.far_label}) is "
                        "via wire but holds no wire record",
                        drawn.place,
                    )
                )
    rank = {kind: n for n, kind in enumerate(OPEN_KINDS)}
    open_items.sort(
        key=lambda item: (
            rank[item.kind],
            item.board_id,
            (item.place.sheet, item.place.column, item.place.slot) if item.place else (0, 0, 0),
            item.text,
        )
    )

    # Block parts, with their continuation links, grouped into sheets.
    row_objects = dict(enumerate(rows))
    parts: list[BlockPart] = []
    for i, p in enumerate(pending):
        previous = pending[i - 1] if p.part > 1 else None
        following = pending[i + 1] if i + 1 < len(pending) and pending[i + 1].part > 1 else None
        parts.append(
            BlockPart(
                owner_kind=p.owner_kind,
                owner_id=p.owner_id,
                title=p.title,
                notes=p.notes,
                template_ref=p.template_ref,
                symbol=p.symbol,
                place=p.header,
                lines=tuple(
                    Line(row=row_objects[row_index], kind=kind, index=n, first=first)
                    for row_index, kind, n, first, _place in p.lines
                ),
                part=p.part,
                continued_from=previous.header if previous is not None else None,
                continues_on=following.header if following is not None else None,
            )
        )
    sheets = tuple(
        Sheet(
            number=number,
            board_id=sheet_boards[number],
            parts=tuple(part for part in parts if part.place.sheet == number),
        )
        for number in sorted(sheet_boards)
    )
    return DrawingSet(
        sheets=sheets,
        terminal_rows=terminal_rows,
        wire_rows=wire_rows,
        rows=tuple(rows),
        places=places,
        open_items=tuple(open_items),
        connections=tuple(sorted(c.id for c in model.connections)),
        nets=tuple(sorted(n.id for n in model.nets)),
    )
