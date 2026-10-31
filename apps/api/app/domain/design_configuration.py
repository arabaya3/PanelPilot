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

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.core.errors import ValidationError
from app.design.model import entities, findings_v2, schedules, versions
from app.design.model.compose import CompositionRefusedError
from app.design.model.write import snapshot_v2_from_v1
from app.models.schemas.design import (
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
        return sum(_size(item.get(name)) for name in names)
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


def _refused(refusal: str, message: str, **params: str) -> ValidationError:
    return ValidationError(
        f"the configuration cannot be applied: {message}",
        code="configuration_refused",
        params={"refusal": refusal, **{k: v[:200] for k, v in params.items()}},
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
        return snapshot_v2_from_v1(designed_v1, configuration)
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
