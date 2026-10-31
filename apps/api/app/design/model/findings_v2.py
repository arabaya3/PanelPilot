"""Structural findings of a ``pp-design/2`` model: its own completeness, not engineering.

Each finding is a plain record shaped like ``DesignFinding`` (code, severity,
message, where), with ``source="integrity"`` and no rule reference: it checks
only what the model's data says. None of them judges a rating, a
cross-section, a colour, a numbering format or a standards clause; those need
registered rules (M3).

Codes:

``template_unreviewed``
    A component or strip uses a template whose review status is not
    ``approved`` (``unreviewed`` or ``rejected``). Blocking, and an
    execution blocker: nothing designed on an unreviewed template may be
    executed.
``out_of_execution_scope``
    A template carries a ``restricted_scope`` (e.g. ``fire_pump``,
    ``life_safety``). Blocking, and an execution blocker.
``unconnected_required_terminal``
    A ``required`` terminal of a component is in no net. Blocking for
    approval (the proposed D-M2-6 default); a draft may hold it.
``wire_attribute_unset``
    A wire's number, cross-section or colour has no value. A warning that
    needs confirmation; M2 never fills one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.design.model import entities

#: Every code this module produces.
CODES = (
    "template_unreviewed",
    "out_of_execution_scope",
    "unconnected_required_terminal",
    "wire_attribute_unset",
)

#: The codes that make execution impossible, whatever else is resolved.
EXECUTION_BLOCKING = frozenset({"template_unreviewed", "out_of_execution_scope"})


@dataclass(frozen=True)
class StructuralFinding:
    """One structural finding.

    Attributes:
        code: One of :data:`CODES`.
        severity: ``"blocking"`` or ``"warning"``.
        message: In English.
        location: The ids it is about (``board_id``, ``component``, ``wire`` ...).
        source: Always ``"integrity"``.
        blocks_execution: Whether it is an execution blocker.
    """

    code: str
    severity: str
    message: str
    location: Mapping[str, str]
    source: str = "integrity"
    blocks_execution: bool = False


def _finding(code: str, severity: str, message: str, **location: str) -> StructuralFinding:
    return StructuralFinding(
        code=code,
        severity=severity,
        message=message,
        location=MappingProxyType(dict(location)),
        blocks_execution=code in EXECUTION_BLOCKING,
    )


def _template_findings(
    template: entities.ComponentTemplate, **location: str
) -> list[StructuralFinding]:
    out: list[StructuralFinding] = []
    if template.review.status != "approved":
        out.append(
            _finding(
                "template_unreviewed",
                "blocking",
                f"uses template {template.ref}, whose review status is "
                f"{template.review.status}; it may not be executed",
                template=template.ref,
                **location,
            )
        )
    if template.restricted_scope:
        out.append(
            _finding(
                "out_of_execution_scope",
                "blocking",
                f"uses template {template.ref}, restricted to "
                f"{', '.join(template.restricted_scope)}; outside what may be executed",
                template=template.ref,
                **location,
            )
        )
    return out


def structural_findings(model: entities.DesignModelV2) -> tuple[StructuralFinding, ...]:
    """Every structural finding of a model, in a stable order.

    Args:
        model: A v2 model, as read (its invariants hold).

    Returns:
        The findings, sorted by code and location.
    """
    templates = {t.ref: t for t in model.templates}
    connected = {m.key for net in model.nets for m in net.members}
    found: list[StructuralFinding] = []
    for component in model.components:
        template = templates[component.template_ref]
        found += _template_findings(template, board_id=component.board_id, component=component.id)
        for terminal in template.terminals:
            ref = entities.TerminalRef(component=component.id, terminal=terminal.id)
            if terminal.required and ref.key not in connected:
                found.append(
                    _finding(
                        "unconnected_required_terminal",
                        "blocking",
                        f"required terminal {terminal.marking} of component "
                        f"{component.id} is in no net",
                        board_id=component.board_id,
                        component=component.id,
                        terminal=terminal.id,
                    )
                )
    for strip in model.strips:
        if strip.template_ref is not None:
            found += _template_findings(
                templates[strip.template_ref], board_id=strip.board_id, strip=strip.id
            )
    for wire in model.wires:
        for name in ("number", "cross_section_mm2", "colour"):
            if getattr(wire, name).source == "unset":
                found.append(
                    _finding(
                        "wire_attribute_unset",
                        "warning",
                        f"wire {wire.id} has no {name}; no rule sets it yet",
                        wire=wire.id,
                        attribute=name,
                    )
                )
    return tuple(sorted(found, key=lambda f: (f.code, sorted(f.location.items()))))


def execution_blockers(model: entities.DesignModelV2) -> tuple[StructuralFinding, ...]:
    """The structural findings that make executing the design impossible.

    Args:
        model: A v2 model, as read.

    Returns:
        Every ``template_unreviewed`` and ``out_of_execution_scope`` finding.
    """
    return tuple(f for f in structural_findings(model) if f.blocks_execution)
