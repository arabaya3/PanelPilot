"""Review schedules of a ``pp-design/2`` model: terminal and wire lists as plain rows.

Pure functions over a saved model (as read): nothing is rendered, computed,
defaulted or looked up elsewhere. Every value in a row is copied from the
model; a value the model does not hold is ``None`` (an attribute's source is
then shown as :data:`UNRESOLVED`). Rows are review content only: they are not
an execution document, and a schedule says nothing about electrical
correctness.

Labels. A terminal is labelled ``"<designation>:<marking>"``, where
``<designation>`` is the ``product`` part of the owning component's or
strip's designation as the data gives it, or the owner's id when it has
none; nothing is added to it (no prefix, level or numbering convention). The
levels of a multi-level strip point share a marking, so a label may repeat:
rows also carry the structured terminal (``side``/``position``/``level``,
``start_ref``/``end_ref``) for a renderer to tell them apart.

Flags. A row's ``flags`` are the codes of the structural findings
(:func:`findings_v2.structural_findings`) whose location names the row's
entity: its component and terminal, its strip, its net, its wire, or one of
the nets or wires a finding lists.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from app.design.model import domain, entities, findings_v2

#: How an ``unset`` attribute's source is shown: there is no value to review.
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class TerminalRow:
    """One terminal: a component terminal, or one side of a strip point.

    Attributes:
        board_id: The v1 board.
        owner_kind: ``"component"`` or ``"strip"``.
        owner_id: The component or strip id.
        designation: The owner's designation as the data gives it, or ``None``.
        terminal_id: The template terminal id, or the strip point id.
        marking: The printed marking from the template or the strip point.
        side: For a strip point, ``"inner"`` or ``"outer"``; else ``None``.
        position: For a strip point, its position; else ``None``.
        level: For a strip point, its level; else ``None``.
        kind: The terminal's or point's kind (open vocabulary, from data).
        required: For a component terminal, its requiredness as the template documents it (``entities.documented_requiredness``): ``None`` when unresolved.
            (``True``, ``False`` or ``None``); else ``None``.
        net_id: The net it is a member of, or ``None``.
        signal: That net's signal label, or ``None``.
        connected_to: The labels of the other ends of its connections, sorted.
        template_ref: The owner's template, or ``None`` (a strip without one).
        pattern_refs: The patterns that created the owner (component) or the point.
        net_origin: The net's ``origin``, or ``None``.
        flags: Codes of the findings that touch the row, sorted.
    """

    board_id: str
    owner_kind: str
    owner_id: str
    designation: domain.Designation | None
    terminal_id: str
    marking: str
    side: str | None
    position: int | None
    level: int | None
    kind: str
    required: bool | None
    net_id: str | None
    signal: str | None
    connected_to: tuple[str, ...]
    template_ref: str | None
    pattern_refs: tuple[str, ...]
    net_origin: str | None
    flags: tuple[str, ...]


@dataclass(frozen=True)
class AttributeCell:
    """A wire attribute as reviewed: its value (text) and where it came from.

    ``source`` is ``user``, ``data`` or ``assumption``, or :data:`UNRESOLVED`
    when the model holds no value. An assumption names its record and statement.
    """

    value: str | None
    source: str
    assumption_id: str | None
    assumption_statement: str | None


@dataclass(frozen=True)
class WireRow:
    """One wire.

    Attributes:
        board_id: The board of its net.
        wire_id: The wire.
        connection_id: The connection it makes.
        start: The ``from`` end's label (see the module docstring).
        end: The ``to`` end's label.
        start_ref: The ``from`` end itself (labels alone may repeat, e.g.
            the levels of a multi-level strip point share a marking).
        end_ref: The ``to`` end itself.
        net_id: Its net.
        signal: Its net's signal label.
        number: The wire number cell.
        cross_section_mm2: The cross-section cell.
        colour: The colour cell.
        pattern_ref: The pattern that created its connection, or ``None``.
        flags: Codes of the findings that touch the row, sorted.
    """

    board_id: str
    wire_id: str
    connection_id: str
    start: str
    end: str
    start_ref: entities.TerminalRef
    end_ref: entities.TerminalRef
    net_id: str
    signal: str
    number: AttributeCell
    cross_section_mm2: AttributeCell
    colour: AttributeCell
    pattern_ref: str | None
    flags: tuple[str, ...]


def _name(designation: domain.Designation | None, fallback: str) -> str:
    return designation.product if designation is not None else fallback


class _Index:
    """Lookups over one model, built once per schedule."""

    def __init__(self, model: entities.DesignModelV2) -> None:
        self.model = model
        self.components = {c.id: c for c in model.components}
        self.points: dict[str, tuple[entities.TerminalStrip, entities.StripPoint]] = {
            p.id: (s, p) for s in model.strips for p in s.points
        }
        self.templates = {t.ref: t for t in model.templates}
        self.net_of: dict[tuple[str, str, str], entities.Net] = {
            m.key: n for n in model.nets for m in n.members
        }
        self.nets = {n.id: n for n in model.nets}
        self.connections = {x.id: x for x in model.connections}
        self.peers: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for x in model.connections:
            self.peers[x.start.key].append(self.label(x.end))
            self.peers[x.end.key].append(self.label(x.start))
        self.findings = findings_v2.structural_findings(model)

    def label(self, ref: entities.TerminalRef) -> str:
        if ref.component is not None:
            component = self.components[ref.component]
            template = self.templates[component.template_ref]
            terminal = template.terminal(ref.terminal or "")
            marking = terminal.marking if terminal is not None else str(ref.terminal)
            return f"{_name(component.designation, component.id)}:{marking}"
        strip, point = self.points[ref.point or ""]
        return f"{_name(strip.designation, strip.id)}:{point.marking}"

    def flags(self, **where: str) -> tuple[str, ...]:
        """Codes of the findings whose location names every given key's value.

        A key matches its own value, or the value as a member of its plural
        key (``net`` in ``nets``, ``wire`` in ``wires``).
        """

        def names(location: Mapping[str, str], key: str, value: str) -> bool:
            plural = location.get(f"{key}s")
            return location.get(key) == value or (plural is not None and value in plural.split(","))

        return tuple(
            sorted(
                {
                    found.code
                    for found in self.findings
                    if all(names(found.location, k, v) for k, v in where.items())
                }
            )
        )


def _net_flags(index: _Index, net: entities.Net | None) -> set[str]:
    return set(index.flags(net=net.id)) if net is not None else set()


def terminal_schedule(model: entities.DesignModelV2) -> tuple[TerminalRow, ...]:
    """Every component terminal and every strip point side, as review rows.

    Args:
        model: A v2 model, as read.

    Returns:
        The rows, ordered by board, then components before strips, then the
        owner's label and id, then (strip) position, level and side or
        (component) marking and terminal id.
    """
    index = _Index(model)
    rows: list[tuple[tuple[object, ...], TerminalRow]] = []
    for component in model.components:
        template = index.templates[component.template_ref]
        for terminal in template.terminals:
            ref = entities.TerminalRef(component=component.id, terminal=terminal.id)
            net = index.net_of.get(ref.key)
            flags = set(index.flags(component=component.id, terminal=terminal.id))
            flags |= {
                f.code
                for f in index.findings
                if f.location.get("component") == component.id and "terminal" not in f.location
            }
            flags |= _net_flags(index, net)
            name = _name(component.designation, component.id)
            rows.append(
                (
                    (
                        component.board_id,
                        0,
                        name,
                        component.id,
                        0,
                        0,
                        terminal.marking,
                        terminal.id,
                    ),
                    TerminalRow(
                        board_id=component.board_id,
                        owner_kind="component",
                        owner_id=component.id,
                        designation=component.designation,
                        terminal_id=terminal.id,
                        marking=terminal.marking,
                        side=None,
                        position=None,
                        level=None,
                        kind=terminal.kind,
                        # As documented: an undocumented value reads unresolved.
                        required=entities.documented_requiredness(template, terminal),
                        net_id=net.id if net else None,
                        signal=net.signal if net else None,
                        connected_to=tuple(sorted(index.peers.get(ref.key, ()))),
                        template_ref=component.template_ref,
                        pattern_refs=component.pattern_refs,
                        net_origin=net.origin if net else None,
                        flags=tuple(sorted(flags)),
                    ),
                )
            )
    for strip in model.strips:
        name = _name(strip.designation, strip.id)
        strip_flags = set(index.flags(strip=strip.id))
        for point in strip.points:
            for side in entities.SIDES:
                ref = entities.TerminalRef(point=point.id, side=side)
                net = index.net_of.get(ref.key)
                rows.append(
                    (
                        (strip.board_id, 1, name, strip.id, point.position, point.level, side, ""),
                        TerminalRow(
                            board_id=strip.board_id,
                            owner_kind="strip",
                            owner_id=strip.id,
                            designation=strip.designation,
                            terminal_id=point.id,
                            marking=point.marking,
                            side=side,
                            position=point.position,
                            level=point.level,
                            kind=point.kind,
                            required=None,
                            net_id=net.id if net else None,
                            signal=net.signal if net else None,
                            connected_to=tuple(sorted(index.peers.get(ref.key, ()))),
                            template_ref=strip.template_ref,
                            pattern_refs=(point.pattern_ref,) if point.pattern_ref else (),
                            net_origin=net.origin if net else None,
                            flags=tuple(sorted(strip_flags | _net_flags(index, net))),
                        ),
                    )
                )
    return tuple(row for _key, row in sorted(rows, key=lambda item: item[0]))


def _cell(model: entities.DesignModelV2, attribute: entities.WireAttribute) -> AttributeCell:
    record = model.assumption(attribute.assumption) if attribute.assumption else None
    return AttributeCell(
        value=None if attribute.value is None else str(attribute.value),
        source=UNRESOLVED if attribute.source == "unset" else attribute.source,
        assumption_id=attribute.assumption,
        assumption_statement=record.statement if record is not None else None,
    )


def wire_schedule(model: entities.DesignModelV2) -> tuple[WireRow, ...]:
    """Every wire, as review rows.

    Args:
        model: A v2 model, as read.

    Returns:
        The rows, ordered by board, ``from`` label, ``to`` label and wire id.
    """
    index = _Index(model)
    rows: list[WireRow] = []
    for wire in model.wires:
        connection = index.connections[wire.connection_id]
        net = index.nets[connection.net_id]
        flags = set(index.flags(wire=wire.id)) | _net_flags(index, net)
        rows.append(
            WireRow(
                board_id=net.board_id,
                wire_id=wire.id,
                connection_id=connection.id,
                start=index.label(connection.start),
                end=index.label(connection.end),
                start_ref=connection.start,
                end_ref=connection.end,
                net_id=net.id,
                signal=net.signal,
                number=_cell(model, wire.number),
                cross_section_mm2=_cell(model, wire.cross_section_mm2),
                colour=_cell(model, wire.colour),
                pattern_ref=connection.pattern_ref,
                flags=tuple(sorted(flags)),
            )
        )
    return tuple(sorted(rows, key=lambda r: (r.board_id, r.start, r.end, r.wire_id)))
