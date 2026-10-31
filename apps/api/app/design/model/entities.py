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

#: Where a wire attribute's value came from. ``unset`` means there is none.
WIRE_SOURCES = ("user", "data", "unset")

#: A template's review statuses. Only ``approved`` is reviewed and accepted.
REVIEW_STATUSES = ("unreviewed", "approved", "rejected")

#: A template ref's scopes.
SCOPES = ("global", "tenant")

#: How a connection is made.
VIAS = ("wire", "cable_core", "jumper", "busbar")

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
        required: Whether a design must connect it.
        group: The contact group it belongs to, or ``None``.
    """

    id: str
    marking: str
    kind: str
    required: bool
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
    """Where a template definition came from."""

    source: str
    author: str
    created: str


@dataclass(frozen=True)
class Review:
    """A template's review record. ``unreviewed`` until a reviewer decides."""

    status: str
    reviewer: str | None
    reviewed_at: str | None
    standards: tuple[str, ...]
    scope_note: str | None


@dataclass(frozen=True)
class ComponentTemplate:
    """What a kind of component is, as data: its terminals and parameters.

    Attributes:
        ref: ``"<scope>/<id>@<version>"``, unique per snapshot.
        function: Open-vocabulary string, e.g. ``"contactor"``.
        terminals: By id.
        contact_groups: By id.
        parameters: By name.
        provenance: Its source.
        review: Its review record.
        restricted_scope: Uses it may not be executed for (e.g. ``"life_safety"``).
    """

    ref: str
    function: str
    terminals: tuple[TerminalDef, ...]
    contact_groups: tuple[ContactGroup, ...]
    parameters: tuple[ParameterDef, ...]
    provenance: Provenance
    review: Review
    restricted_scope: tuple[str, ...]

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


@dataclass(frozen=True)
class LockEntry:
    """The SHA-256 of one embedded template's canonical JSON."""

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
    """

    id: str
    board_id: str
    template_ref: str
    designation: domain.Designation | None
    parameters: tuple[ParameterValue, ...]
    legacy_device_id: str | None


@dataclass(frozen=True)
class StripPoint:
    """One point of a terminal strip; ``(marking, level)`` is unique per strip."""

    id: str
    position: int
    level: int
    marking: str
    kind: str


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
    """Terminals that are one potential, by declared signal label (data, e.g. ``"L1"``)."""

    id: str
    board_id: str
    signal: str
    origin: str
    members: tuple[TerminalRef, ...]


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
    """

    id: str
    net_id: str
    start: TerminalRef
    end: TerminalRef
    via: str
    core: CableCore | None


@dataclass(frozen=True)
class WireAttribute:
    """A wire attribute's value and its source; ``value`` is ``None`` exactly when unset."""

    value: str | Decimal | None
    source: str


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

    Every collection is sorted by id (templates and the lock by ref).
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
