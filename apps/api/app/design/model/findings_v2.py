"""Structural findings of a ``pp-design/2`` model: its own consistency, not engineering.

Each finding is a plain record shaped like ``DesignFinding`` (code, severity,
message, where), with ``source="integrity"`` and no rule reference: it checks
only what the model's data says. None of them judges a rating, a
cross-section, a colour, a numbering format, electrical compatibility or a
standards clause; those need registered rules (M3). Passing every check here
proves the saved data is consistent with itself, nothing more.

This module is the single source of each code's severity (:data:`SEVERITIES`)
and of which codes block execution (:data:`EXECUTION_BLOCKING`).

Codes (inputs are model data only):

``template_unreviewed``
    A component or strip uses a template whose ``review.status`` is not
    ``approved`` or ``rejected``. A warning for design review (every legacy
    template is unreviewed), and an execution blocker.
``template_rejected``
    A component or strip uses a template a reviewer rejected. Blocking for
    approval, and an execution blocker.
``out_of_execution_scope``
    A template carries a ``restricted_scope`` (e.g. ``fire_pump``,
    ``life_safety``). Blocking for approval, and an execution blocker.
``pattern_unreviewed``
    An embedded connection pattern is not ``approved`` (nor ``rejected``).
    As for templates: a warning, and an execution blocker.
``pattern_rejected``
    An embedded connection pattern was rejected by a reviewer. Blocking for
    approval, and an execution blocker.
``unconnected_required_terminal``
    A terminal of a component is in no net, and its template **explicitly**
    requires it (:func:`entities.explicitly_required`: ``required`` is
    ``true`` and the template's provenance documents a source). Blocking for
    approval; a draft may hold it.
``terminal_requiredness_unresolved``
    A terminal of a component is in no net, and whether it must be connected
    is not documented: ``required`` is ``null``, or ``true`` or ``false`` in
    a template whose provenance names no source. A warning that stays
    unresolved; it is never guessed either way and never blocks. Only a
    documented ``required: false`` gives no finding.
``net_unrealised``
    A net has two or more members, and the net's own connections (every
    connection whose ``net_id`` is the net, any ``via``) do not join all of
    them into one connected group (graph connectivity, members as vertices,
    connections as edges). Blocking for approval: the data declares a
    potential its own connections do not make, an inconsistency of the
    declaration itself, which a reviewer cannot resolve by confirming.
``net_signal_conflict``
    Two or more different nets on one board carry the same ``signal`` label.
    A warning that needs confirmation: a data-consistency check on labels
    only, not an electrical rule (two separate potentials may share a label
    on purpose, e.g. per outgoing circuit).
``wire_number_duplicate``
    Two or more wires in one uniqueness scope carry the same explicit
    ``number`` value (an ``unset`` number is ignored). The scope is
    :data:`WIRE_NUMBER_SCOPE` (``board``) unless the caller passes another of
    :data:`WIRE_NUMBER_SCOPES`; which scope is right is the open decision
    D-M2-5, so this is a warning and the scope is a parameter a later
    profile setting will supply as data.
``designation_duplicate``
    Two or more components or terminal strips on one board print the same
    designation (``=function+location-product``). Blocking for approval: the
    parts cannot be told apart on the drawing or the schedules (M6a).
``wire_attribute_unset``
    A wire's number, cross-section or colour has no value (unresolved). A
    warning that needs confirmation; M2 never fills one.
``wire_attribute_assumed``
    A wire's number, cross-section or colour holds a value its input stated
    as an assumption (it names the assumption record). A warning that needs
    confirmation.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.design.model import entities

#: Every code this module produces, with its severity: the single source.
SEVERITIES: Mapping[str, str] = MappingProxyType(
    {
        "template_unreviewed": "warning",
        "template_rejected": "blocking",
        "out_of_execution_scope": "blocking",
        "pattern_unreviewed": "warning",
        "pattern_rejected": "blocking",
        "unconnected_required_terminal": "blocking",
        "terminal_requiredness_unresolved": "warning",
        "net_unrealised": "blocking",
        "net_signal_conflict": "warning",
        "wire_number_duplicate": "warning",
        "wire_attribute_unset": "warning",
        "designation_duplicate": "blocking",
        "wire_attribute_assumed": "warning",
    }
)

#: Every code this module produces.
CODES: tuple[str, ...] = tuple(SEVERITIES)

#: The codes that make execution impossible, whatever else is resolved.
EXECUTION_BLOCKING = frozenset(
    {
        "template_unreviewed",
        "template_rejected",
        "out_of_execution_scope",
        "pattern_unreviewed",
        "pattern_rejected",
    }
)

#: The codes that record missing information: unresolved until data gives it.
UNRESOLVED = frozenset({"terminal_requiredness_unresolved", "wire_attribute_unset"})

#: The scopes wire-number uniqueness may be checked in (D-M2-5 is open).
WIRE_NUMBER_SCOPES = ("board", "design")

#: The scope used until a profile supplies one as data.
WIRE_NUMBER_SCOPE = "board"

#: The wire attributes a value source is recorded for.
WIRE_ATTRIBUTES = ("number", "cross_section_mm2", "colour")


@dataclass(frozen=True)
class StructuralFinding:
    """One structural finding.

    Attributes:
        code: One of :data:`CODES`.
        severity: ``"blocking"`` or ``"warning"``, as :data:`SEVERITIES` says.
        message: In English.
        location: The ids it is about (``board_id``, ``component``, ``wire`` ...);
            a finding about several ids of one kind joins them with ``,``
            under a plural key (``nets``, ``wires``).
        source: Always ``"integrity"``.
        blocks_execution: Whether it is an execution blocker.
        unresolved: Whether it records missing information (:data:`UNRESOLVED`).
    """

    code: str
    severity: str
    message: str
    location: Mapping[str, str]
    source: str = "integrity"
    blocks_execution: bool = False
    unresolved: bool = False


#: The most characters of one id, label or value a finding's message quotes;
#: a longer one is cut with an ellipsis (its full value stays in the finding's
#: location), so a printed findings line always wraps inside its frame.
MESSAGE_VALUE_CHARS = 32


def _short(value: object) -> str:
    text = str(value)
    if len(text) <= MESSAGE_VALUE_CHARS:
        return text
    return text[: MESSAGE_VALUE_CHARS - 1] + "…"


def _finding(code: str, message: str, **location: str) -> StructuralFinding:
    return StructuralFinding(
        code=code,
        severity=SEVERITIES[code],
        message=message,
        location=MappingProxyType(dict(location)),
        blocks_execution=code in EXECUTION_BLOCKING,
        unresolved=code in UNRESOLVED,
    )


def _review_findings(
    kind: str, ref: str, review: entities.Review, **location: str
) -> list[StructuralFinding]:
    if review.status == "rejected":
        return [
            _finding(
                f"{kind}_rejected",
                f"uses {kind} {_short(ref)}, which a reviewer rejected; "
                "it may not be approved or executed",
                **{kind: ref},
                **location,
            )
        ]
    if review.status != "approved":
        return [
            _finding(
                f"{kind}_unreviewed",
                f"uses {kind} {_short(ref)}, which no qualified engineer has reviewed; "
                "it may not be executed",
                **{kind: ref},
                **location,
            )
        ]
    return []


def _template_findings(
    template: entities.ComponentTemplate, **location: str
) -> list[StructuralFinding]:
    out = _review_findings("template", template.ref, template.review, **location)
    if template.restricted_scope:
        out.append(
            _finding(
                "out_of_execution_scope",
                f"uses template {_short(template.ref)}, restricted to "
                f"{', '.join(_short(s) for s in template.restricted_scope)}; outside what may be executed",
                template=template.ref,
                **location,
            )
        )
    return out


def _terminal_findings(
    model: entities.DesignModelV2, templates: Mapping[str, entities.ComponentTemplate]
) -> list[StructuralFinding]:
    connected = {m.key for net in model.nets for m in net.members}
    out: list[StructuralFinding] = []
    for component in model.components:
        template = templates[component.template_ref]
        for terminal in template.terminals:
            ref = entities.TerminalRef(component=component.id, terminal=terminal.id)
            # Only a documented "not required" settles it; an undocumented one
            # is as unresolved as an undocumented "required".
            if (
                ref.key in connected
                or entities.documented_requiredness(template, terminal) is False
            ):
                continue
            where = {
                "board_id": component.board_id,
                "component": component.id,
                "terminal": terminal.id,
            }
            if entities.explicitly_required(template, terminal):
                out.append(
                    _finding(
                        "unconnected_required_terminal",
                        f"required terminal {_short(terminal.marking)} of component "
                        f"{_short(component.id)} is in no net",
                        **where,
                    )
                )
            else:
                why = (
                    "its template does not say whether it must be connected"
                    if terminal.required is None
                    else "its template documents no source for whether it must be connected"
                )
                out.append(
                    _finding(
                        "terminal_requiredness_unresolved",
                        f"terminal {_short(terminal.marking)} of component {_short(component.id)} is in "
                        f"no net and {why}; unresolved",
                        **where,
                    )
                )
    return out


def _groups(
    members: tuple[entities.TerminalRef, ...], connections: list[entities.Connection]
) -> int:
    """How many connected groups ``connections`` join ``members`` into (union-find)."""
    parent = {m.key: m.key for m in members}

    def root(key: tuple[str, str, str]) -> tuple[str, str, str]:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for connection in connections:
        parent[root(connection.start.key)] = root(connection.end.key)
    return len({root(m.key) for m in members})


def _net_findings(model: entities.DesignModelV2) -> list[StructuralFinding]:
    out: list[StructuralFinding] = []
    by_net: dict[str, list[entities.Connection]] = defaultdict(list)
    for connection in model.connections:
        by_net[connection.net_id].append(connection)
    for net in model.nets:
        if len(net.members) < 2:
            continue
        groups = _groups(net.members, by_net[net.id])
        if groups > 1:
            out.append(
                _finding(
                    "net_unrealised",
                    f"net {_short(net.id)} ({_short(net.signal)}) has {len(net.members)} members, but its "
                    f"connections join them into {groups} separate groups",
                    board_id=net.board_id,
                    net=net.id,
                    groups=str(groups),
                )
            )
    by_signal: dict[tuple[str, str], list[str]] = defaultdict(list)
    for net in model.nets:
        by_signal[(net.board_id, net.signal)].append(net.id)
    for (board_id, signal), net_ids in sorted(by_signal.items()):
        if len(net_ids) > 1:
            out.append(
                _finding(
                    "net_signal_conflict",
                    f"{len(net_ids)} different nets on board {_short(board_id)} carry the signal "
                    f"label {_short(signal)}; confirm they are meant to be separate",
                    board_id=board_id,
                    signal=signal,
                    nets=",".join(sorted(net_ids)),
                )
            )
    return out


def _wire_findings(model: entities.DesignModelV2, scope: str) -> list[StructuralFinding]:
    if scope not in WIRE_NUMBER_SCOPES:
        raise ValueError(f"wire number scope is one of {list(WIRE_NUMBER_SCOPES)}")
    net_board = {n.id: n.board_id for n in model.nets}
    connection_board = {x.id: net_board[x.net_id] for x in model.connections}
    out: list[StructuralFinding] = []
    numbered: dict[tuple[str, str], list[str]] = defaultdict(list)
    for wire in model.wires:
        board_id = connection_board[wire.connection_id]
        for name in WIRE_ATTRIBUTES:
            attribute: entities.WireAttribute = getattr(wire, name)
            if attribute.source == "unset":
                out.append(
                    _finding(
                        "wire_attribute_unset",
                        f"wire {_short(wire.id)} has no {name}; no rule sets it yet",
                        board_id=board_id,
                        wire=wire.id,
                        attribute=name,
                    )
                )
            elif attribute.source == "assumption":
                out.append(
                    _finding(
                        "wire_attribute_assumed",
                        f"wire {_short(wire.id)}'s {name} is an assumption ({_short(attribute.assumption)}); "
                        "confirm it",
                        board_id=board_id,
                        wire=wire.id,
                        attribute=name,
                        assumption=str(attribute.assumption),
                    )
                )
        if wire.number.value is not None:
            key = board_id if scope == "board" else ""
            numbered[(key, str(wire.number.value))].append(wire.id)
    for (key, number), wire_ids in sorted(numbered.items()):
        if len(wire_ids) > 1:
            where = {"board_id": key} if scope == "board" else {}
            out.append(
                _finding(
                    "wire_number_duplicate",
                    f"{len(wire_ids)} wires in one {scope} carry the number {_short(number)}; "
                    "whether numbers must be unique there is not decided yet",
                    scope=scope,
                    number=number,
                    wires=",".join(sorted(wire_ids)),
                    **where,
                )
            )
    return out


def printed_designation(designation: object) -> str:
    """A designation as it is drawn (IEC 81346-1): ``=function+location-product``."""
    function = getattr(designation, "function", None)
    location = getattr(designation, "location", None)
    product = getattr(designation, "product", "")
    return (
        (f"={function}" if function else "") + (f"+{location}" if location else "") + f"-{product}"
    )


def _designation_findings(model: entities.DesignModelV2) -> list[StructuralFinding]:
    """``designation_duplicate``: one designation printed on two parts of a board.

    A designation with no function aspect is read as on its own board (the
    board's id is its function aspect, as for the derived devices), so a
    configured ``-Q1`` on board MB-1 repeats a derived ``=MB-1-Q1``. Reported
    under the fuller form.
    """
    holders: dict[tuple[str, str], list[str]] = defaultdict(list)
    parts: list[entities.Component | entities.TerminalStrip] = [*model.components, *model.strips]
    for part in parts:
        if part.designation is not None:
            designation = part.designation
            if getattr(designation, "function", None) is None:
                designation = dataclasses.replace(designation, function=part.board_id)
            holders[(part.board_id, printed_designation(designation))].append(part.id)
    return [
        _finding(
            "designation_duplicate",
            f"{_short(printed)} is printed on {len(ids)} parts of board {_short(board)}; "
            "each part needs its own designation.",
            board_id=board,
            designation=printed,
            parts=",".join(sorted(ids)),
        )
        for (board, printed), ids in sorted(holders.items())
        if len(ids) > 1
    ]


def structural_findings(
    model: entities.DesignModelV2, *, wire_number_scope: str = WIRE_NUMBER_SCOPE
) -> tuple[StructuralFinding, ...]:
    """Every structural finding of a model, in a stable order.

    Args:
        model: A v2 model, as read (its invariants hold).
        wire_number_scope: Where wire numbers are compared, one of
            :data:`WIRE_NUMBER_SCOPES`.

    Returns:
        The findings, sorted by code and location.

    Raises:
        ValueError: If ``wire_number_scope`` is not a known scope.
    """
    templates = {t.ref: t for t in model.templates}
    found: list[StructuralFinding] = []
    for pattern in model.patterns:
        found += _review_findings("pattern", pattern.ref, pattern.review)
    for component in model.components:
        found += _template_findings(
            templates[component.template_ref], board_id=component.board_id, component=component.id
        )
    for strip in model.strips:
        if strip.template_ref is not None:
            found += _template_findings(
                templates[strip.template_ref], board_id=strip.board_id, strip=strip.id
            )
    found += _terminal_findings(model, templates)
    found += _net_findings(model)
    found += _wire_findings(model, wire_number_scope)
    found += _designation_findings(model)
    return tuple(sorted(found, key=lambda f: (f.code, sorted(f.location.items()))))


def execution_blockers(model: entities.DesignModelV2) -> tuple[StructuralFinding, ...]:
    """The structural findings that make executing the design impossible.

    Args:
        model: A v2 model, as read.

    Returns:
        Every finding whose code is in :data:`EXECUTION_BLOCKING`.
    """
    return tuple(f for f in structural_findings(model) if f.blocks_execution)
