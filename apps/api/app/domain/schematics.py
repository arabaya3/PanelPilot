"""Turning a panel schedule into a schematic specification (PD-007).

The one place that decides what the single-line diagram shows: which device
feeds which, in which row, and — for every quantity a calculation would
supply — either the calculated value with its source, or an explicit statement
that it was not calculated and why.

**Topology is checked, never inferred.** The schedule names each device's
upstream. A schedule with no incomer, with two, with an upstream that does
not exist, or with a loop is refused with every problem listed: a drawing
that guessed which breaker feeds which contactor would look authoritative and
be wired as drawn.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.tools.din_module_width import ComponentCategory
from app.ai.tools.enclosure_sizing import ComponentSpec, rail_requirements
from app.core.errors import ValidationError
from app.models.schemas.schematic import (
    CalculatedValue,
    CalcValue,
    NotCalculatedValue,
    RefusedValue,
    ScheduleLine,
    SchematicComponent,
    SchematicConnection,
    SchematicGroup,
    SchematicRequest,
    SchematicSpec,
)

#: Symbol kinds PD-006 draws. Mirrors `SYMBOL_KINDS` in
#: apps/web/src/components/schematic/symbols.tsx; a test pins the two together.
KNOWN_SYMBOL_KINDS = frozenset(
    {
        "circuit-breaker",
        "contactor",
        "overload-relay",
        "relay-coil",
        "terminal-block",
        "fuse",
        "isolator",
        "transformer",
        "motor",
        "vfd",
        "busbar",
        "indicator-lamp",
        "push-button",
    }
)

#: Why no conductor is sized. Shared by every connection, so the drawing and
#: the tracker say the same thing.
CONDUCTOR_NOT_CALCULATED = NotCalculatedValue(
    reason=(
        "conductor sizing is not sourced: the cable-sizing tables lack enough "
        "published worked examples to verify against"
    ),
    blocked_by="AI-005, PD-005",
)

#: Why no trunking is sized.
TRUNKING_NOT_CALCULATED = NotCalculatedValue(
    reason="no normative wiring-duct fill ratio exists to cite",
    blocked_by="PD-004",
)

#: Why no enclosure is selected.
ENCLOSURE_NOT_CALCULATED = NotCalculatedValue(
    reason=(
        "enclosure selection needs the PD-001 catalogue export and the enclosure "
        "drawing's rail pitch, neither of which this deployment holds"
    ),
    blocked_by="PD-001, PD-003",
)

#: Cited on every rail-row figure this module calculates.
RAIL_ROWS_SOURCE = "PD-003 rail_requirements; widths from PD-002 manufacturer datasheets"


def build_schematic(request: SchematicRequest) -> SchematicSpec:
    """Build the specification the single-line renderer draws.

    Args:
        request: The panel schedule.

    Returns:
        The complete specification, with every uncalculated quantity marked.

    Raises:
        ValidationError: If the topology is unspecified or ambiguous — no
            incomer, more than one, an unknown upstream, a duplicate
            designator, or a loop. Every problem is listed, so the engineer
            fixes the schedule once rather than one error at a time.
    """
    incomer = _check_topology(request.lines)

    groups: dict[str, list[ScheduleLine]] = {}
    for line in request.lines:
        groups.setdefault(line.group, []).append(line)

    return SchematicSpec(
        title=request.title,
        supply=request.supply,
        incomer=incomer,
        components=[
            SchematicComponent(
                designator=line.designator,
                kind=line.kind,
                rating=line.rating,
                group=line.group,
                mounting=line.mounting,
            )
            for line in request.lines
        ],
        connections=[
            SchematicConnection(
                upstream=line.feeds_from,
                downstream=line.designator,
                conductor=CONDUCTOR_NOT_CALCULATED,
            )
            for line in request.lines
            if line.feeds_from is not None
        ],
        groups=[
            SchematicGroup(
                name=name,
                order=order,
                designators=[line.designator for line in lines],
                rail_rows=_rail_rows(name, lines, usable_rail_mm=request.usable_rail_mm),
            )
            for order, (name, lines) in enumerate(groups.items())
        ],
        enclosure=ENCLOSURE_NOT_CALCULATED,
        trunking=TRUNKING_NOT_CALCULATED,
        unknown_kinds=sorted(
            {line.kind for line in request.lines if line.kind not in KNOWN_SYMBOL_KINDS}
        ),
    )


def _check_topology(lines: list[ScheduleLine]) -> str:
    """Refuse a schedule whose wiring cannot be drawn without guessing.

    Args:
        lines: The schedule.

    Returns:
        The incomer's designator.

    Raises:
        ValidationError: Listing every topology problem found.
    """
    problems: list[str] = []

    seen: set[str] = set()
    for line in lines:
        if line.designator in seen:
            problems.append(f"designator {line.designator} appears more than once")
        seen.add(line.designator)

    roots = [line.designator for line in lines if line.feeds_from is None]
    if not roots:
        problems.append("no incomer: every line names an upstream, so nothing is fed by the supply")
    elif len(roots) > 1:
        problems.append(
            f"ambiguous incomer: {', '.join(roots)} all have no upstream; exactly one line "
            "may be fed by the supply — give the others their feeding device"
        )

    for line in lines:
        if line.feeds_from is not None and line.feeds_from not in seen:
            problems.append(
                f"{line.designator} is fed from {line.feeds_from}, which is not in the schedule"
            )
        if line.feeds_from == line.designator:
            problems.append(f"{line.designator} is fed from itself")

    problems.extend(_loops(lines))

    if problems:
        raise ValidationError(
            "the schedule's wiring is not fully specified: " + "; ".join(problems)
        )
    return roots[0]


def _loops(lines: list[ScheduleLine]) -> list[str]:
    """Find chains of upstream references that close on themselves.

    Args:
        lines: The schedule.

    Returns:
        One problem per distinct loop, naming its members in feed order. A line
        fed from itself is reported by the caller and not repeated here.
    """
    upstream = {line.designator: line.feeds_from for line in lines}
    reported: set[frozenset[str]] = set()
    problems: list[str] = []
    for start in upstream:
        path: list[str] = []
        node: str | None = start
        # Walk towards the supply until it is reached, the chain leaves the
        # schedule (reported separately), or a device repeats.
        while node is not None and node in upstream and node not in path:
            path.append(node)
            node = upstream[node]
        if node is None or node not in path:
            continue
        cycle = path[path.index(node) :]
        if len(cycle) > 1 and frozenset(cycle) not in reported:
            reported.add(frozenset(cycle))
            problems.append(f"loop: {' -> '.join([*cycle, cycle[0]])}")
    return problems


def _rail_rows(
    group: str, lines: list[ScheduleLine], *, usable_rail_mm: Decimal | None
) -> CalcValue:
    """Work out a group's rail rows, or say precisely why not.

    Args:
        group: The group's name.
        lines: Its devices.
        usable_rail_mm: Usable rail per row, if the caller supplied one.

    Returns:
        The row count with its source; not calculated when an input is
        missing; refused when PD-002/PD-003 decline the inputs.
    """
    rail_mounted = [line for line in lines if line.mounting == "rail"]
    if not rail_mounted:
        return CalculatedValue(display="0 rows (no rail-mounted devices)", source=RAIL_ROWS_SOURCE)
    if usable_rail_mm is None:
        return NotCalculatedValue(
            reason="the usable rail length per row was not given (it comes from the enclosure drawing)",
            blocked_by="PD-003 input",
        )
    unsized = [line.designator for line in rail_mounted if line.din is None]
    if unsized:
        return NotCalculatedValue(
            reason=f"no sourced rail width for {', '.join(unsized)}",
            blocked_by="PD-002",
        )

    specs = [
        ComponentSpec(
            category=ComponentCategory(line.din.category),
            series=line.din.series,
            poles=line.din.poles,
            quantity=1,
            group=group,
        )
        for line in rail_mounted
        if line.din is not None
    ]
    try:
        (requirement,) = rail_requirements(specs, usable_rail_mm=usable_rail_mm)
    except ValidationError as exc:
        return RefusedValue(reason=str(exc))
    return CalculatedValue(
        display=f"{requirement.rows} row{'s' if requirement.rows != 1 else ''} "
        f"({requirement.width_mm} mm of rail)",
        source=RAIL_ROWS_SOURCE,
    )
