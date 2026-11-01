"""Schema ``pp-design/2`` as plain values: templates, components, strips, nets, wires.

v2 adds to v1 and replaces nothing: :attr:`DesignModelV2.design` is the v1
design as :mod:`domain` holds it, and every new entity refers to v1 content by
its v1 id. This module only holds values, free of any framework; the
invariants that span objects (ids unique, references resolving, a terminal in
one net at most) are checked by :mod:`codec_v2` when a snapshot is read, so a
model built here and not read back is not yet known to be valid.

Conventions are those of :mod:`domain`: ``Decimal``, never ``float``; tuples;
kinds, functions and statuses are strings from an open vocabulary (data, not
Python enums). Ids are opaque: no code reads meaning out of one.

Nothing here carries an engineering value. A wire's number, cross-section and
colour are each a :class:`WireAttribute` whose value is ``None`` with source
``"unset"`` unless input data gave it; no rule fills them in M2.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.design.model import domain

#: Where a wire attribute's value came from. ``unset`` means there is none (unresolved);
#: ``assumption`` means the value was stated as an assumption by its input, and names
#: the :class:`Assumption` record that says so (never created automatically).
WIRE_SOURCES = ("user", "data", "assumption", "unset")

#: A template's or a connection pattern's review statuses. Only ``approved`` is
#: reviewed and accepted.
REVIEW_STATUSES = ("unreviewed", "approved", "rejected")

#: A template ref's scopes.
SCOPES = ("global", "tenant")

#: How a connection is made. ``internal`` joins the inner and outer side of
#: one strip point through the point itself; it exists only where data
#: declares it (no strip point is assumed to conduct between its sides).
VIAS = ("wire", "cable_core", "jumper", "busbar", "internal")

#: The sides of a strip point.
SIDES = ("inner", "outer")

#: How a net came about.
ORIGINS = ("derived", "user")


@dataclass(frozen=True)
class TerminalDef:
    """A terminal of a template, as printed on the device.

    Attributes:
        id: Unique within the template; what a :class:`TerminalRef` names.
        marking: The printed marking, from the template's data only.
        kind: An open-vocabulary string (``power_line``, ``coil`` ...).
        required: ``True`` if the template says a design must connect it,
            ``False`` if it says it need not, ``None`` if it does not say.
            Missing requiredness stays unresolved; it is never guessed. Only
            :func:`explicitly_required` decides whether it blocks approval.
        group: The contact group it belongs to, or ``None``.
    """

    id: str
    marking: str
    kind: str
    required: bool | None
    group: str | None


@dataclass(frozen=True)
class ContactGroup:
    """Terminals that act together (a coil and its contacts), so cross-references are data."""

    id: str
    kind: str


@dataclass(frozen=True)
class ParameterDef:
    """A parameter a component of the template may hold. No default, no rating."""

    name: str
    kind: str
    unit: str | None


@dataclass(frozen=True)
class Provenance:
    """Where a template or pattern definition came from.

    ``source`` is the document the definition is taken from, or ``None`` when
    the definition documents none (see :func:`documents_a_source`).
    """

    source: str | None
    author: str
    created: str


def documents_a_source(provenance: Provenance) -> bool:
    """Whether a provenance names a source: ``source`` is set and not blank.

    Args:
        provenance: A template's or pattern's provenance.

    Returns:
        ``True`` exactly when ``source`` holds a non-whitespace character.
    """
    return provenance.source is not None and provenance.source.strip() != ""


@dataclass(frozen=True)
class Review:
    """A template's review record. ``unreviewed`` until a reviewer decides."""

    status: str
    reviewer: str | None
    reviewed_at: str | None
    standards: tuple[str, ...]
    scope_note: str | None


#: The shape kinds a symbol is drawn from, and how many integers each takes:
#: ``line`` x1 y1 x2 y2; ``rect`` x y width height; ``circle`` cx cy radius;
#: ``text`` x y (its words are the shape's ``text``).
SYMBOL_SHAPES: dict[str, int] = {"line": 4, "rect": 4, "circle": 3, "text": 2}


@dataclass(frozen=True)
class SymbolShape:
    """One drawn element of a symbol, in the symbol's grid (integers, y downwards)."""

    kind: str
    points: tuple[int, ...]
    text: str | None


@dataclass(frozen=True)
class SymbolAnchor:
    """Where a template terminal joins the symbol, in the symbol's grid."""

    terminal: str
    x: int
    y: int


@dataclass(frozen=True)
class SymbolDef:
    """A template's drawing symbol, as data: shapes and terminal anchors (M4).

    Pure geometry in a ``width`` by ``height`` grid of whole units, with no
    meaning beyond what it draws: it states no rating and follows no named
    symbol standard. Absent (``ComponentTemplate.symbol is None``), a drawing
    shows a labelled placeholder instead.
    """

    width: int
    height: int
    shapes: tuple[SymbolShape, ...]
    anchors: tuple[SymbolAnchor, ...]


@dataclass(frozen=True)
class ComponentTemplate:
    """What a kind of component is, as data: its terminals and parameters.

    Attributes:
        ref: ``"<scope>/<id>/v<version>"``, unique per snapshot.
        function: Open-vocabulary string, e.g. ``"contactor"``.
        terminals: By id.
        contact_groups: By id.
        parameters: By name.
        provenance: Its source.
        review: Its review record.
        restricted_scope: Uses it may not be executed for (e.g. ``"life_safety"``).
        symbol: Its drawing symbol, or ``None`` when the data defines none.
    """

    ref: str
    function: str
    terminals: tuple[TerminalDef, ...]
    contact_groups: tuple[ContactGroup, ...]
    parameters: tuple[ParameterDef, ...]
    provenance: Provenance
    review: Review
    restricted_scope: tuple[str, ...]
    symbol: SymbolDef | None = None

    def terminal(self, terminal_id: str) -> TerminalDef | None:
        """The terminal with this id, or ``None``.

        Args:
            terminal_id: A :attr:`TerminalDef.id`.

        Returns:
            The terminal, if the template has it.
        """
        for terminal in self.terminals:
            if terminal.id == terminal_id:
                return terminal
        return None


def explicitly_required(template: ComponentTemplate, terminal: TerminalDef) -> bool:
    """Whether a documented template explicitly identifies a terminal as required.

    The one definition the ``unconnected_required_terminal`` finding uses: the
    terminal's ``required`` is ``True`` (not ``None``, not ``False``) **and**
    the template's provenance documents a source (:func:`documents_a_source`).
    A ``required: true`` in a template that names no source is not enough: it
    is unresolved, like ``required: null``.

    Args:
        template: The template holding the terminal.
        terminal: One of its terminals.

    Returns:
        ``True`` exactly in that case.
    """
    return terminal.required is True and documents_a_source(template.provenance)


def documented_requiredness(template: ComponentTemplate, terminal: TerminalDef) -> bool | None:
    """A terminal's requiredness as a documented template states it; ``None`` if unresolved.

    ``True`` or ``False`` count only when the template's provenance documents
    a source (:func:`documents_a_source`), whichever way they point: an
    undocumented ``required: false`` is no more settled than an undocumented
    ``required: true``. Anything else is ``None``: unresolved, never guessed.

    Args:
        template: The template holding the terminal.
        terminal: One of its terminals.

    Returns:
        ``True``, ``False``, or ``None`` for unresolved.
    """
    if terminal.required is None or not documents_a_source(template.provenance):
        return None
    return terminal.required


@dataclass(frozen=True)
class PatternSlot:
    """A component a pattern joins: its template (or ``None``) and, optionally, a v1 device kind."""

    id: str
    template: str | None
    device_kind: str | None


@dataclass(frozen=True)
class PatternPoint:
    """A strip point a pattern allocates, relative to the binding's base position."""

    id: str
    strip: str
    offset: int
    level: int
    kind: str


@dataclass(frozen=True)
class PatternMember:
    """A net member of a pattern: ``slot``/``terminal`` or ``point``/``side``."""

    slot: str | None = None
    terminal: str | None = None
    point: str | None = None
    side: str | None = None


@dataclass(frozen=True)
class PatternConnection:
    """A connection of a pattern net, between two member indexes."""

    start: int
    end: int
    via: str
    core: int | None
    label: str | None


@dataclass(frozen=True)
class PatternNet:
    """A net a pattern declares, by role; ``scope`` is ``binding`` or ``board``."""

    role: str
    signal: str
    scope: str
    members: tuple[PatternMember, ...]
    connections: tuple[PatternConnection, ...]


@dataclass(frozen=True)
class ConnectionPattern:
    """A reusable connection pattern, as data, versioned and reviewed like a template.

    Attributes:
        ref: ``"<scope>/<id>/v<version>"`` (no ``@``), unique per snapshot.
        slots: By id.
        strips: The strip roles, sorted.
        points: By id.
        nets: By role.
        provenance: Its source.
        review: Its review record (same vocabulary as a template's).
    """

    ref: str
    slots: tuple[PatternSlot, ...]
    strips: tuple[str, ...]
    points: tuple[PatternPoint, ...]
    nets: tuple[PatternNet, ...]
    provenance: Provenance
    review: Review


@dataclass(frozen=True)
class LockEntry:
    """The SHA-256 of one embedded template's or pattern's canonical JSON."""

    ref: str
    digest: str


@dataclass(frozen=True)
class ParameterValue:
    """A component's value for a template parameter: a string (a Decimal as text)."""

    name: str
    value: str


@dataclass(frozen=True)
class Component:
    """An instance of a template on a board.

    Attributes:
        id: Stable, opaque.
        board_id: A v1 board.
        template_ref: An embedded template.
        designation: Copied from the v1 device it mirrors, or from input data.
        parameters: By name; each names a parameter of the template.
        legacy_device_id: The v1 device on the same board it mirrors, or ``None``.
        pattern_refs: The embedded patterns whose bindings created or joined
            it, sorted; empty for a legacy or configured-only component.
    """

    id: str
    board_id: str
    template_ref: str
    designation: domain.Designation | None
    parameters: tuple[ParameterValue, ...]
    legacy_device_id: str | None
    pattern_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class StripPoint:
    """One point of a terminal strip; ``(marking, level)`` is unique per strip.

    ``pattern_ref`` is the embedded pattern that allocated it, or ``None``.
    """

    id: str
    position: int
    level: int
    marking: str
    kind: str
    pattern_ref: str | None = None


@dataclass(frozen=True)
class TerminalStrip:
    """A terminal strip on a board; points ordered by ``(position, level)``."""

    id: str
    board_id: str
    designation: domain.Designation | None
    template_ref: str | None
    points: tuple[StripPoint, ...]


@dataclass(frozen=True)
class TerminalRef:
    """A terminal: of a component (``component``, ``terminal``) or a strip point (``point``, ``side``).

    Exactly one of the two pairs is set.
    """

    component: str | None = None
    terminal: str | None = None
    point: str | None = None
    side: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """The canonical sort key: component refs before point refs."""
        if self.component is not None:
            return ("component", self.component, self.terminal or "")
        return ("point", self.point or "", self.side or "")


@dataclass(frozen=True)
class Net:
    """Terminals that are one potential, by declared signal label (data, e.g. ``"L1"``).

    ``pattern_refs`` are the embedded patterns that declared it, sorted (a
    board-scoped net may be declared by several); empty for a user net.
    """

    id: str
    board_id: str
    signal: str
    origin: str
    members: tuple[TerminalRef, ...]
    pattern_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CableCore:
    """The core of a v1 cable that makes a connection."""

    cable_id: str
    index: int
    label: str | None


@dataclass(frozen=True)
class Connection:
    """A physical link between two members of one net.

    Attributes:
        id: Stable, opaque.
        net_id: Its net; both ends are members of it.
        start: The ``from`` end (``from`` in JSON).
        end: The ``to`` end (``to`` in JSON).
        via: How it is made, one of :data:`VIAS`.
        core: The cable core, exactly when ``via`` is ``"cable_core"``.
        pattern_ref: The embedded pattern that created it, or ``None``.
    """

    id: str
    net_id: str
    start: TerminalRef
    end: TerminalRef
    via: str
    core: CableCore | None
    pattern_ref: str | None = None


@dataclass(frozen=True)
class WireAttribute:
    """A wire attribute's value and its source; ``value`` is ``None`` exactly when unset.

    ``assumption`` is the id of the :class:`Assumption` that states the value,
    exactly when ``source`` is ``"assumption"``.
    """

    value: str | Decimal | None
    source: str
    assumption: str | None = None


#: The attribute every wire leaves unset unless input data gives a value.
UNSET = WireAttribute(value=None, source="unset")


@dataclass(frozen=True)
class Wire:
    """The wire making a ``via: wire`` connection. Nothing in it is computed."""

    id: str
    connection_id: str
    number: WireAttribute
    cross_section_mm2: WireAttribute
    colour: WireAttribute


@dataclass(frozen=True)
class Assumption:
    """A value that is neither user-given nor rule-produced, recorded as such."""

    id: str
    subject: str
    statement: str


@dataclass(frozen=True)
class DesignModelV2:
    """A whole ``pp-design/2`` design: the v1 design plus connectivity.

    Every collection is sorted by id (templates, patterns and the locks by ref).
    ``registry_lock`` locks the templates and ``pattern_lock`` the patterns;
    stored, both sit under ``registry_lock`` (``templates`` and ``patterns``).
    """

    design: domain.DesignModel
    templates: tuple[ComponentTemplate, ...]
    registry_lock: tuple[LockEntry, ...]
    components: tuple[Component, ...]
    strips: tuple[TerminalStrip, ...]
    nets: tuple[Net, ...]
    connections: tuple[Connection, ...]
    wires: tuple[Wire, ...]
    assumptions: tuple[Assumption, ...]
    patterns: tuple[ConnectionPattern, ...] = ()
    pattern_lock: tuple[LockEntry, ...] = ()

    def template(self, ref: str) -> ComponentTemplate | None:
        """The embedded template with this ref, or ``None``.

        Args:
            ref: A template ref.

        Returns:
            The template, if embedded.
        """
        for template in self.templates:
            if template.ref == ref:
                return template
        return None

    def pattern(self, ref: str) -> ConnectionPattern | None:
        """The embedded connection pattern with this ref, or ``None``.

        Args:
            ref: A pattern ref.

        Returns:
            The pattern, if embedded.
        """
        for pattern in self.patterns:
            if pattern.ref == ref:
                return pattern
        return None

    def assumption(self, assumption_id: str) -> Assumption | None:
        """The assumption record with this id, or ``None``.

        Args:
            assumption_id: An :attr:`Assumption.id`.

        Returns:
            The record, if present.
        """
        for assumption in self.assumptions:
            if assumption.id == assumption_id:
                return assumption
        return None
