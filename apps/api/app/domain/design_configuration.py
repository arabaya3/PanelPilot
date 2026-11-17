"""Configurations sent by a client: bounded, checked for claims, composed, summarised.

A configuration (``app.design.model.compose``) is data a client sends with a
revision or a dry run. Before the composer sees it, this module refuses:

* **Anything too large** (:data:`LIMITS`): its canonical JSON size, nesting
  depth, total number of values, the length of any text, the number of
  templates, patterns, strips, components and bindings, and the size of each
  template and pattern. Checked iteratively, so a deep value cannot exhaust
  the stack, and before composition, so the composed model is bounded by
  ``bindings x pattern size`` (``configuration_too_large``).
* **A claimed review** (``configuration_claims_review``): a client may submit
  a template or pattern only as ``unreviewed``, with no reviewer, review
  date, standards or scope note. A review is recorded by a qualified
  engineer the owner designates, never by whoever sends a request.
* **A reserved ref** (``configuration_ref_reserved``): a submitted
  definition is ``tenant`` scope only; ``global`` refs (the shipped
  ``global/legacy-*`` templates among them) are the software's, and a
  submitted definition can neither redefine nor shadow one.

The composer then refuses everything it cannot apply without guessing
(``configuration_refused``, the composer's code in ``params.refusal``),
including a template with a ``restricted_scope``.

:func:`summary` turns a v2 model into the review shape the API returns.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core import config
from app.core.errors import ValidationError
from app.design.model import entities, findings_v2, schedules, versions
from app.design.model.compose import CompositionRefusedError
from app.design.model.write import snapshot_v2_from_v1
from app.models.schemas.design import (
    ProjectDesignRequest,
    V2Component,
    V2Connection,
    V2Definition,
    V2Finding,
    V2ModelSummary,
    V2Net,
    V2Strip,
    V2StripPoint,
    V2TerminalRow,
    V2Value,
    V2WireRow,
)


@dataclass(frozen=True)
class Limits:
    """The bounds a configuration is held to (documented in the M2 record, 6b)."""

    #: Its canonical JSON, in bytes (UTF-8).
    max_bytes: int = 256 * 1024
    #: Nesting depth of objects and lists.
    max_depth: int = 12
    #: Every object, list, text, number and literal in it.
    max_values: int = 25_000
    #: Characters of any one text (key or value).
    max_text: int = 2_000
    #: Entries of each top-level list.
    max_templates: int = 64
    max_patterns: int = 32
    max_strips: int = 64
    max_components: int = 256
    max_bindings: int = 128
    #: Terminals, contact groups and parameters of one template, together.
    max_template_items: int = 256
    #: Slots, points, net members and connections of one pattern, together.
    max_pattern_items: int = 256


LIMITS = Limits()

_COUNTS = MappingProxyType(
    {
        "templates": LIMITS.max_templates,
        "patterns": LIMITS.max_patterns,
        "strips": LIMITS.max_strips,
        "components": LIMITS.max_components,
        "bindings": LIMITS.max_bindings,
    }
)


def _too_large(what: str, limit: int) -> ValidationError:
    return ValidationError(
        f"the configuration is too large: {what} exceeds {limit}",
        code="configuration_too_large",
        params={"limit_of": what, "limit": str(limit)},
    )


def _bounded_walk(value: Any) -> None:
    """Refuse a value nested too deep, with too many values, or a text too long."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    seen = 0
    while stack:
        item, depth = stack.pop()
        seen += 1
        if seen > LIMITS.max_values:
            raise _too_large("values", LIMITS.max_values)
        if depth > LIMITS.max_depth:
            raise _too_large("depth", LIMITS.max_depth)
        if isinstance(item, str):
            if len(item) > LIMITS.max_text:
                raise _too_large("text length", LIMITS.max_text)
        elif isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(key, str) and len(key) > LIMITS.max_text:
                    raise _too_large("text length", LIMITS.max_text)
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _size(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0


def _definition_items(kind: str, item: Mapping[str, Any]) -> int:
    if kind == "templates":
        names = ("terminals", "contact_groups", "parameters")
        symbol = item.get("symbol")
        drawn = (
            _size(symbol.get("shapes")) + _size(symbol.get("anchors"))
            if isinstance(symbol, Mapping)
            else 0
        )
        return sum(_size(item.get(name)) for name in names) + drawn
    nets = item.get("nets")
    total = _size(item.get("slots")) + _size(item.get("points"))
    for net in nets if isinstance(nets, list) else ():
        if isinstance(net, Mapping):
            total += _size(net.get("members")) + _size(net.get("connections"))
    return total


def _refuse_claims(kind: str, item: Mapping[str, Any]) -> None:
    """A submitted definition claims no review and defines a ``tenant`` ref only."""
    ref = item.get("ref")
    label = ref if isinstance(ref, str) else "?"
    if isinstance(ref, str) and not ref.startswith("tenant/"):
        raise ValidationError(
            f"a submitted {kind[:-1]} must have a tenant ref; {label} is reserved",
            code="configuration_ref_reserved",
            params={"kind": kind[:-1], "ref": label[:200]},
        )
    review = item.get("review")
    if not isinstance(review, Mapping):
        return  # The composer refuses a missing or malformed review itself.
    claimed = [
        name
        for name, ok in (
            ("status", review.get("status") == "unreviewed"),
            ("reviewer", review.get("reviewer") is None),
            ("reviewed_at", review.get("reviewed_at") is None),
            ("standards", review.get("standards") in (None, [])),
            ("scope_note", review.get("scope_note") is None),
        )
        if not ok
    ]
    if claimed:
        raise ValidationError(
            f"a submitted {kind[:-1]} cannot claim a review ({', '.join(claimed)}); "
            "only a qualified engineer the owner designates records one",
            code="configuration_claims_review",
            params={"kind": kind[:-1], "ref": label[:200], "fields": ",".join(claimed)},
        )


def check_configuration(configuration: Any) -> None:
    """Refuse a configuration that is too large, claims a review or uses a reserved ref.

    Args:
        configuration: As the client sent it (JSON-shaped).

    Raises:
        ValidationError: ``configuration_too_large``,
            ``configuration_claims_review``, ``configuration_ref_reserved``,
            or ``configuration_refused`` (``params.refusal`` is
            ``configuration_invalid``) for a value that is not an object.
    """
    _bounded_walk(configuration)
    try:
        raw = json.dumps(configuration, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _refused("configuration_invalid", "not JSON data") from exc
    if len(raw.encode("utf-8")) > LIMITS.max_bytes:
        raise _too_large("bytes", LIMITS.max_bytes)
    if not isinstance(configuration, Mapping):
        raise _refused("configuration_invalid", "the configuration is not an object")
    for name, limit in _COUNTS.items():
        if _size(configuration.get(name)) > limit:
            raise _too_large(name, limit)
    for kind, limit in (
        ("templates", LIMITS.max_template_items),
        ("patterns", LIMITS.max_pattern_items),
    ):
        items = configuration.get(kind)
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, Mapping):
                continue
            if _definition_items(kind, item) > limit:
                raise _too_large(f"{kind[:-1]} size", limit)
            _refuse_claims(kind, item)


#: Composer params naming what a refusal is about, most specific first.
_ENTRY_REFS = (
    "device",
    "cable",
    "circuit",
    "component",
    "board",
    "point",
    "strip",
    "slot",
)
#: Where in the configuration the entry sits: the first present is named.
_ENTRY_PLACES = ("binding", "strip", "component")
#: Refusals about a template ref.
_TEMPLATE_REFUSALS = ("unknown_template", "restricted_scope", "template_conflict")


def entry_params(refusal: str, params: Mapping[str, Any]) -> dict[str, str]:
    """``ref``, ``kind`` and the entry (``binding``|``strip``|``component``) a refusal names.

    The same shape for a sent configuration's ``configuration_refused`` and a
    carried one's ``configuration_conflict`` (M7b), so a client can mark the
    entry. ``kind`` is what ``ref`` is (``board``, ``circuit``, ``template`` ...);
    ``configuration`` when the refusal names no reference.
    """
    if refusal in _TEMPLATE_REFUSALS and "ref" in params:
        kind = "template"
        ref = str(params["ref"])
    else:
        kind = next((name for name in _ENTRY_REFS if name in params), "configuration")
        ref = str(params.get(kind, ""))
        if kind == "configuration" and "ref" in params:
            # A template or pattern definition named by its ref.
            kind, ref = "definition", str(params["ref"])
    out = {"ref": ref[:200], "kind": kind}
    place = next((name for name in _ENTRY_PLACES if name in params), None)
    if place is not None:
        out[place] = str(params[place])[:200]
    return out


def _refused(refusal: str, message: str, **params: str) -> ValidationError:
    given = {k: v[:200] for k, v in params.items()}
    return ValidationError(
        f"the configuration cannot be applied: {message}",
        code="configuration_refused",
        params={**given, **entry_params(refusal, given), "refusal": refusal},
    )


def compose_snapshot(designed_v1: dict[str, Any], configuration: Mapping[str, Any]) -> Any:
    """The stored v2 envelope of a server design under a checked configuration.

    Args:
        designed_v1: The server's v1 design (``DesignProject`` JSON).
        configuration: Already passed :func:`check_configuration`.

    Returns:
        The envelope, read back by the v2 reader.

    Raises:
        ValidationError: ``configuration_refused`` with the composer's code.
    """
    try:
        # The one place every revision and every preview is composed: the cross-board cable-core
        # write gate (DESIGN_CABLE_CORE_CROSS_BOARD, off by default) applies to all of them alike.
        return snapshot_v2_from_v1(
            designed_v1, configuration, cross_board_cores=config.design_cable_core_cross_board()
        )
    except CompositionRefusedError as exc:
        raise _refused(exc.code, str(exc), **exc.params) from exc
    except versions.SnapshotUnreadableError as exc:
        raise _refused("composition_invalid", str(exc)) from exc


# --------------------------------------------------------------------------- #
# The review summary
# --------------------------------------------------------------------------- #


def _designation(value: Any) -> str | None:
    return value.product if value is not None else None


def _cell(cell: schedules.AttributeCell) -> V2Value:
    return V2Value(
        value=cell.value,
        source=cell.source,
        assumption_id=cell.assumption_id,
        assumption_statement=cell.assumption_statement,
    )


def required_value(required: bool | None, owner_kind: str) -> V2Value:
    """A terminal's requiredness as a reviewed value.

    ``None`` on a component terminal is ``unresolved`` (the template does not
    say); a strip point side has no requiredness of its own.
    """
    if owner_kind != "component":
        return V2Value(value=None, source="data")
    if required is None:
        return V2Value(value=None, source=schedules.UNRESOLVED)
    return V2Value(value="required" if required else "not required", source="data")


def finding_view(found: findings_v2.StructuralFinding, confirm: frozenset[str]) -> V2Finding:
    """A structural finding with its approval and execution effects."""
    return V2Finding(
        code=found.code,
        severity=found.severity,
        message=found.message,
        location=dict(found.location),
        unresolved=found.unresolved,
        blocks_approval=found.severity not in ("warning", "info"),
        blocks_execution=found.blocks_execution,
        requires_engineering_confirmation=found.code in confirm,
    )


def summary(model: entities.DesignModelV2, confirm: frozenset[str]) -> V2ModelSummary:
    """A v2 model as the API shows it for review.

    Args:
        model: As read.
        confirm: The codes an engineer must confirm (the store's mapping).

    Returns:
        The summary; review content only.
    """
    label_of = _labeller(model)
    definitions = [
        V2Definition(
            ref=t.ref,
            kind="template",
            review_status=t.review.status,
            provenance_source=t.provenance.source,
            restricted_scope=list(t.restricted_scope),
        )
        for t in model.templates
    ] + [
        V2Definition(
            ref=p.ref,
            kind="pattern",
            review_status=p.review.status,
            provenance_source=p.provenance.source,
        )
        for p in model.patterns
    ]
    return V2ModelSummary(
        definitions=definitions,
        components=[
            V2Component(
                id=c.id,
                board_id=c.board_id,
                designation=_designation(c.designation),
                template_ref=c.template_ref,
                legacy_device_id=c.legacy_device_id,
                pattern_refs=list(c.pattern_refs),
                source="data" if c.legacy_device_id is not None else "configuration",
            )
            for c in model.components
        ],
        strips=[
            V2Strip(
                id=s.id,
                board_id=s.board_id,
                designation=_designation(s.designation),
                template_ref=s.template_ref,
                points=[
                    V2StripPoint(
                        id=p.id,
                        position=p.position,
                        level=p.level,
                        marking=p.marking,
                        kind=p.kind,
                        pattern_ref=p.pattern_ref,
                    )
                    for p in s.points
                ],
            )
            for s in model.strips
        ],
        nets=[
            V2Net(
                id=n.id,
                board_id=n.board_id,
                signal=n.signal,
                origin=n.origin,
                members=[label_of(m) for m in n.members],
                pattern_refs=list(n.pattern_refs),
            )
            for n in model.nets
        ],
        connections=[
            V2Connection(
                id=x.id,
                net_id=x.net_id,
                start=label_of(x.start),
                end=label_of(x.end),
                via=x.via,
                cable_core=(f"{x.core.cable_id}#{x.core.index}" if x.core is not None else None),
                pattern_ref=x.pattern_ref,
            )
            for x in model.connections
        ],
        wires=[
            V2WireRow(
                board_id=r.board_id,
                wire_id=r.wire_id,
                connection_id=r.connection_id,
                start=r.start,
                end=r.end,
                net_id=r.net_id,
                signal=r.signal,
                number=_cell(r.number),
                cross_section_mm2=_cell(r.cross_section_mm2),
                colour=_cell(r.colour),
                pattern_ref=r.pattern_ref,
                flags=list(r.flags),
            )
            for r in schedules.wire_schedule(model)
        ],
        terminals=[
            V2TerminalRow(
                board_id=r.board_id,
                owner_kind=r.owner_kind,
                owner_id=r.owner_id,
                designation=_designation(r.designation),
                terminal_id=r.terminal_id,
                marking=r.marking,
                side=r.side,
                position=r.position,
                level=r.level,
                kind=r.kind,
                required=required_value(r.required, r.owner_kind),
                net_id=r.net_id,
                signal=r.signal,
                connected_to=list(r.connected_to),
                template_ref=r.template_ref,
                pattern_refs=list(r.pattern_refs),
                net_origin=r.net_origin,
                flags=list(r.flags),
            )
            for r in schedules.terminal_schedule(model)
        ],
        findings=[finding_view(f, confirm) for f in findings_v2.structural_findings(model)],
    )


def _labeller(model: entities.DesignModelV2) -> Any:
    """A terminal ref's label, as the schedules print it (``<designation>:<marking>``)."""
    components = {c.id: c for c in model.components}
    templates = {t.ref: t for t in model.templates}
    points = {p.id: (s, p) for s in model.strips for p in s.points}

    def label(ref: entities.TerminalRef) -> str:
        if ref.component is not None:
            component = components[ref.component]
            terminal = templates[component.template_ref].terminal(ref.terminal or "")
            marking = terminal.marking if terminal is not None else str(ref.terminal)
            name = _designation(component.designation) or component.id
            return f"{name}:{marking}"
        strip, point = points[ref.point or ""]
        name = _designation(strip.designation) or strip.id
        return f"{name}:{point.marking} ({ref.side})"

    return label


# --------------------------------------------------------------------------- #
# References that moved with the load schedule
# --------------------------------------------------------------------------- #

#: What a binding may name that the design numbers by position: a circuit, a cable, a device.
_REF_KINDS = ("circuit", "cable", "device")


def _entered_boards(request: Any) -> dict[str, dict[str, Any]]:
    """Each board as entered (JSON values), by name; ``{}`` if the request cannot be read.

    ``loads`` are its loads; ``board`` is the rest of it, without its name and loads.
    """
    try:
        if isinstance(request, Mapping):
            request = ProjectDesignRequest.model_validate(request)
        return {
            b.name: {
                "loads": [load.model_dump(mode="json") for load in b.loads],
                "board": b.model_dump(mode="json", exclude={"name", "loads"}),
            }
            for b in request.boards
        }
    except (PydanticValidationError, AttributeError, TypeError):
        return {}


def _entered_loads(request: Any) -> dict[str, list[Any]]:
    """Each board's loads as entered (JSON values), by board name; ``{}`` if unreadable."""
    return {name: board["loads"] for name, board in _entered_boards(request).items()}


def _device_role(device_id: str, circuit_id: str) -> str:
    """``c2-breaker`` -> ``breaker``: what the device is to its circuit."""
    return device_id[len(circuit_id) + 1 :] if device_id.startswith(circuit_id + "-") else device_id


def _owners(
    project: Any, request: Any, side: str = ""
) -> dict[tuple[str, str, str], tuple[str, str, str]]:
    """Each circuit, cable and device of a v1 design: ``(label, fingerprint, circuit)``.

    Circuit ids are positional (``c2`` is the second load, the server's feeders
    after the entered loads), and so are their cables and devices: after a load
    is inserted, removed or moved, the same id can name another load. The
    fingerprint is everything that tells the load apart in the model: every
    field entered for it (description, kind, power, phases, power factor,
    control, starter, length) or, for a feeder, the board it feeds and that
    board's position; and the binding context the design gives it (its devices'
    roles and whether it has a cable). Two loads with equal fingerprints are
    indistinguishable in the model.

    A board's own devices are owners too: an incomer by the board as entered
    (all but its name and loads) and its position, and any other device (a
    residual current group, ``g1-rcd``, positional as well) by its kind and the
    fingerprints of the circuits it feeds. What cannot be read gets a
    fingerprint tagged with ``side``, so it never proves anything.
    """
    entered_boards = _entered_boards(request)
    entered = {name: board["loads"] for name, board in entered_boards.items()}
    position = {b.id: at for at, b in enumerate(getattr(project, "boards", None) or [])}
    owners: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for board in getattr(project, "boards", None) or []:
        loads = entered.get(board.id)
        for circuit in board.circuits or []:
            number = circuit.id[1:]
            index = int(number) - 1 if number.isdigit() else -1
            if circuit.feeds:
                what: Any = ["feeder", circuit.feeds, position.get(circuit.feeds)]
            elif loads is not None and 0 <= index < len(loads):
                what = ["load", loads[index]]
            else:
                what = ["unknown", side, circuit.id]  # never equal across sides
            context = [
                sorted(_device_role(d, circuit.id) for d in circuit.device_ids or []),
                circuit.cable_id is not None,
            ]
            fingerprint = json.dumps([what, context], sort_keys=True, default=str)
            mark = (circuit.description, fingerprint, circuit.id)
            owners[(board.id, "circuit", circuit.id)] = mark
            if circuit.cable_id:
                owners[(board.id, "cable", circuit.cable_id)] = mark
            for device in circuit.device_ids or []:
                owners[(board.id, "device", device)] = mark
        circuit_marks = {c.id: owners[(board.id, "circuit", c.id)][1] for c in board.circuits or []}
        meta = entered_boards.get(board.id, {}).get("board")
        board_mark: Any = (
            ["board", meta, position.get(board.id)] if meta is not None else ["unknown", side]
        )
        incomers = list(getattr(board, "incomer_ids", None) or [])
        devices = list(getattr(board, "devices", None) or [])
        upstream_of = {d.id: getattr(d, "upstream_id", None) for d in devices}
        kind_of = {
            d.id: str(getattr(getattr(d, "kind", None), "value", getattr(d, "kind", "")))
            for d in devices
        }
        for device in getattr(board, "devices", None) or []:
            if (board.id, "device", device.id) in owners:
                continue
            kind = getattr(getattr(device, "kind", None), "value", str(getattr(device, "kind", "")))
            if device.id in incomers:
                what = ["incomer", kind, incomers.index(device.id), board_mark]
            else:
                # Everything it feeds, through the devices below it (a group breaker
                # feeds its circuits through the group's residual current device).
                fed = sorted(
                    circuit_marks[c.id]
                    for c in board.circuits or []
                    if device.id in _upstream_chain(c, upstream_of)
                )
                below = sorted(
                    kind_of[d]
                    for d in upstream_of
                    if d != device.id and device.id in _upstream_chain_of(d, upstream_of)
                )
                # A device that feeds no circuit is told apart by nothing: never proven.
                what = ["board_device", kind, fed, below, board_mark] if fed else ["unknown", side]
            label = getattr(device, "description", None) or device.id
            fingerprint = json.dumps(what, sort_keys=True, default=str)
            owners[(board.id, "device", device.id)] = (label, fingerprint, device.id)
    return owners


def _upstream_chain_of(device_id: str | None, upstream_of: Mapping[str, Any]) -> set[str]:
    """``device_id`` and every device above it on its board (stops at a cycle)."""
    chain: set[str] = set()
    while isinstance(device_id, str) and device_id not in chain:
        chain.add(device_id)
        device_id = upstream_of.get(device_id)
    return chain


def _upstream_chain(circuit: Any, upstream_of: Mapping[str, Any]) -> set[str]:
    """Every board device a circuit is fed through."""
    return _upstream_chain_of(getattr(circuit, "upstream_id", None), upstream_of)


def _refs(binding: Mapping[str, Any]) -> list[tuple[str, str, str, Any]]:
    """A binding's positional references: ``(field, key, kind, id)``."""
    refs: list[tuple[str, str, str, Any]] = [
        ("circuit", "", "circuit", binding.get("circuit")),
        ("cable", "", "cable", binding.get("cable")),
    ]
    slots = binding.get("slots")
    for key, slot in slots.items() if isinstance(slots, Mapping) else []:
        if isinstance(slot, Mapping):
            refs.append(("slot", str(key), "device", slot.get("device")))
    return refs


def reference_token(
    binding: str, field: str, key: str, ref: str, before: tuple[str, str], after: tuple[str, str]
) -> str:
    """What a confirmation is bound to: the reference and its exact before and after.

    ``before`` and ``after`` are ``(board, fingerprint)``. Any later change to
    either load, the board or the reference gives another token, so an earlier
    confirmation no longer applies.
    """
    body = json.dumps([binding, field, key, ref, list(before), list(after)], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def moved_references(
    baseline: Any,
    current: Any,
    configuration: Mapping[str, Any],
    confirmed: Sequence[Mapping[str, Any]] = (),
    baseline_configuration: Mapping[str, Any] | None = None,
    *,
    baseline_request: Any = None,
    current_request: Any = None,
) -> list[dict[str, str]]:
    """Binding references whose id is not proven to name what it named when last saved.

    Checked: each reference whose id is the same as in ``baseline_configuration``
    (the configuration saved with ``baseline``), whether or not its binding's
    board changed. It is proven only when the load the id names now has the same
    fingerprint (:func:`_owners`) as the load it named then, on the board the
    binding named then. Otherwise (another load, a reordered, swapped, renamed
    or deleted board, a stored request that cannot be read) it is refused until
    the user chooses again or confirms it with its token. A reference whose id
    the user changed, a binding new since, or one with nothing saved is chosen
    against the current choices; the composer checks it as before.

    Returns:
        One entry per unproven reference not confirmed: ``binding``, ``field``
        (``circuit``, ``cable`` or ``slot``), ``key`` (the slot, or ``""``),
        ``id``, ``before`` and ``after`` (descriptions), ``before_board``,
        ``after_board``, ``before_circuit``, ``after_circuit`` and ``token``.
    """
    if baseline_configuration is None:
        return []
    saved: dict[tuple[str, str, str], tuple[Any, Any]] = {}
    saved_bindings = baseline_configuration.get("bindings")
    for old in saved_bindings if isinstance(saved_bindings, list) else []:
        if isinstance(old, Mapping) and isinstance(old.get("id"), str):
            for field, key, _kind, ref in _refs(old):
                saved[(old["id"], field, key)] = (old.get("board"), ref)
    before = _owners(baseline, baseline_request, "before")
    after = _owners(current, current_request, "after")
    kept = {
        (
            str(c.get("binding")),
            str(c.get("field")),
            str(c.get("key") or ""),
            str(c.get("id")),
            str(c.get("token")),
        )
        for c in confirmed
    }
    moved: list[dict[str, str]] = []
    bindings = configuration.get("bindings")
    for binding in bindings if isinstance(bindings, list) else []:
        if not isinstance(binding, Mapping) or not isinstance(binding.get("id"), str):
            continue
        bid, board = binding["id"], binding.get("board")
        for field, key, kind, ref in _refs(binding):
            if not isinstance(ref, str) or not isinstance(board, str):
                continue
            was_board, was_ref = saved.get((bid, field, key), (None, None))
            if was_ref != ref or not isinstance(was_board, str):
                continue  # changed by the user, or new: chosen against the current choices
            was, now = before.get((was_board, kind, ref)), after.get((board, kind, ref))
            if now is None:
                continue  # gone: the composer refuses it (unknown reference)
            if was is not None and was[1] == now[1]:
                continue  # proven: the same load, indistinguishable in the model
            token = reference_token(
                bid, field, key, ref, (was_board, was[1] if was else ""), (board, now[1])
            )
            if (bid, field, key, ref, token) in kept:
                continue
            moved.append(
                {
                    "binding": bid,
                    "field": field,
                    "key": key,
                    "id": ref,
                    "before": was[0] if was else "",
                    "after": now[0],
                    "before_board": was_board,
                    "after_board": board,
                    "before_circuit": was[2] if was else "",
                    "after_circuit": now[2],
                    "token": token,
                }
            )
    return moved


#: How many unproven references a refusal names in full.
MOVED_IN_FULL = 20


def check_references(
    baseline: Any,
    current: Any,
    configuration: Mapping[str, Any],
    confirmed: Sequence[Mapping[str, Any]] = (),
    baseline_configuration: Mapping[str, Any] | None = None,
    *,
    baseline_request: Any = None,
    current_request: Any = None,
) -> None:
    """Refuse a configuration whose unchanged references are not proven to name the same load.

    Raises:
        ValidationError: ``configuration_refused``, refusal ``reference_moved``,
            naming the first unproven reference in full (``binding``, ``field``,
            ``key``, ``ref``, ``before``, ``after``, ``before_board``,
            ``after_board``, ``before_circuit``, ``after_circuit``, ``token``)
            each of the first ``MOVED_IN_FULL`` in full (``r{i}_*``), their
            number (``moved_count``), and a compact ``moved`` list
            (``binding|field|key|id|token`` joined by ``;``, cut at the
            parameter bound; the editor reads ``r{i}_*``).
    """
    moved = moved_references(
        baseline,
        current,
        configuration,
        confirmed,
        baseline_configuration,
        baseline_request=baseline_request,
        current_request=current_request,
    )
    if not moved:
        return
    first = moved[0]
    raise _refused(
        "reference_moved",
        f"binding {first['binding']} names {first['id']} on {first['after_board']}, which is "
        f"not proven to be the load it named when saved ({first['before']!r} on "
        f"{first['before_board']}; now {first['after']!r}); choose again or confirm it",
        binding=first["binding"],
        field=first["field"],
        key=first["key"],
        ref=first["id"],
        before=first["before"],
        after=first["after"],
        before_board=first["before_board"],
        after_board=first["after_board"],
        before_circuit=first["before_circuit"],
        after_circuit=first["after_circuit"],
        token=first["token"],
        moved=";".join(
            f"{m['binding']}|{m['field']}|{m['key']}|{m['id']}|{m['token']}" for m in moved
        ),
        moved_count=str(len(moved)),
        # Each reference in full (to the first MOVED_IN_FULL), so the editor can say what
        # keeping each one means; the rest are named again once these are settled.
        **{
            f"r{i}_{name}": value
            for i, entry in enumerate(moved[:MOVED_IN_FULL])
            for name, value in entry.items()
        },
    )


def board_choices(project: Any) -> list[dict[str, Any]]:
    """Each board's circuits and cables in a v1 design, for the editor's choices."""
    out: list[dict[str, Any]] = []
    for board in getattr(project, "boards", None) or []:
        out.append(
            {
                "id": board.id,
                "circuits": [
                    {"id": c.id, "description": c.description} for c in board.circuits or []
                ],
                "cables": [
                    {
                        "id": c.id,
                        "cores": c.cores,
                        "designation": (
                            " ".join(
                                p
                                for p in (
                                    c.designation.function,
                                    c.designation.location,
                                    c.designation.product,
                                )
                                if p
                            )
                            if c.designation is not None
                            else None
                        ),
                    }
                    for c in board.cables or []
                ],
            }
        )
    return out
