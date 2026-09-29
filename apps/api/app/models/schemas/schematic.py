"""The schematic contract (PD-007): what to draw, kept apart from how.

The calc-tools layer fills this in and the single-line renderer (PD-008)
draws it; neither knows the other's internals, so either can change alone.

**Every value that could have come from a calculation says where it came
from, or why it did not.** A schematic is read by someone who will build a
panel from it. A blank conductor size looks like an oversight and invites the
reader to fill it in from habit; an explicit "not calculated — blocked on
AI-005" tells them the tool did not decide it and they must. So a calculated
quantity is one of three shapes, never an optional field that can simply be
missing:

* ``calculated`` — a value, with the source it came from;
* ``not_calculated`` — no tool can produce it yet, and which task blocks it;
* ``refused`` — a tool ran and declined, e.g. an input outside its tables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

#: PD-002's device families, by value. Restated rather than imported: models
#: are shapes the tools depend on, not the reverse. A test pins this to
#: ``app.ai.tools.din_module_width.ComponentCategory`` so the two cannot drift.
DinCategory = Literal["mcb", "rcbo", "terminal-block"]

#: A reference designator as printed on a drawing: `Q1`, `-KM3`, `X1`.
Designator = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16, pattern=r"^\S+$")
]

#: Short free text printed on the drawing.
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class CalculatedValue(BaseModel):
    """A quantity a tool produced.

    Attributes:
        display: The value as it is printed, with its unit.
        source: Where it came from, citable.
    """

    status: Literal["calculated"] = "calculated"
    display: Label
    source: Label


class NotCalculatedValue(BaseModel):
    """A quantity no tool can produce yet.

    Attributes:
        reason: Why, in words an engineer can act on.
        blocked_by: The task(s) that would unblock it, e.g. ``"AI-005"``.
    """

    status: Literal["not_calculated"] = "not_calculated"
    reason: str
    blocked_by: str


class RefusedValue(BaseModel):
    """A quantity a tool declined to produce for these inputs.

    Attributes:
        reason: The tool's refusal, verbatim.
    """

    status: Literal["refused"] = "refused"
    reason: str


#: A calculated quantity in one of its three explicit states.
CalcValue = Annotated[
    CalculatedValue | NotCalculatedValue | RefusedValue, Field(discriminator="status")
]


class DinSpec(BaseModel):
    """What PD-002 needs to look up a device's rail width.

    Attributes:
        category: The device family, which decides the width convention.
        series: Manufacturer series, where the family has more than one.
        poles: Pole count; module-pitch devices scale with it.
    """

    category: DinCategory
    series: str | None = None
    poles: int = Field(default=1, ge=1, le=4)


class ScheduleLine(BaseModel):
    """One device in the panel schedule, as the engineer states it.

    Attributes:
        designator: Its reference designator; unique within the schedule.
        kind: The PD-006 symbol kind. An unrecognised kind is accepted and
            drawn as a marked placeholder, never dropped.
        rating: The device's stated rating, e.g. ``"C16"``. The engineer's
            input, not a calculation, so it is printed as given.
        group: The functional row it belongs to, e.g. ``"motors"``.
        feeds_from: The designator directly upstream of it. ``None`` for the
            incomer only — exactly one line may leave it empty.
        mounting: Where it physically sits. Only rail-mounted devices take rail.
        din: How to look up its rail width; omit when not rail-mounted, or
            when its width is not sourced (the group's rows then say so).
    """

    designator: Designator
    kind: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    rating: Label | None = None
    group: Label = "main"
    feeds_from: Designator | None = None
    mounting: Literal["rail", "door", "field"] = "rail"
    din: DinSpec | None = None


class SchematicRequest(BaseModel):
    """A panel to draw.

    Attributes:
        title: Printed in the title block.
        supply: The incoming supply as printed, e.g. ``"400 V 3~ 50 Hz"``.
        lines: The schedule.
        usable_rail_mm: Usable rail length per row, from the enclosure drawing.
            Without it rail rows are reported as not calculated.
    """

    title: Label
    supply: Label
    lines: list[ScheduleLine] = Field(min_length=1, max_length=500)
    usable_rail_mm: Decimal | None = Field(default=None, gt=0)


class SchematicComponent(BaseModel):
    """A device placed on the diagram.

    Attributes:
        designator: Its reference designator.
        kind: Its PD-006 symbol kind, possibly one the library cannot draw.
        rating: Its stated rating, if any.
        group: Its functional row.
        mounting: Where it sits.
    """

    designator: str
    kind: str
    rating: str | None
    group: str
    mounting: Literal["rail", "door", "field"]


class SchematicConnection(BaseModel):
    """One conductor between two devices.

    Attributes:
        upstream: The feeding device.
        downstream: The fed device.
        conductor: The conductor size — explicit when not calculated.
    """

    upstream: str
    downstream: str
    conductor: CalcValue


class SchematicGroup(BaseModel):
    """A functional row, in drawing order.

    Attributes:
        name: The group.
        order: Position left to right on the diagram.
        designators: Its devices, in schedule order.
        rail_rows: How many rail rows it needs (PD-003).
    """

    name: str
    order: int
    designators: list[str]
    rail_rows: CalcValue


class SchematicSpec(BaseModel):
    """Everything the renderer needs, and nothing it has to infer.

    Attributes:
        title: For the title block.
        supply: The incoming supply.
        incomer: The designator fed by the supply.
        components: Every device, in schedule order.
        connections: The topology, one entry per fed device.
        groups: Functional rows, in drawing order.
        enclosure: The enclosure selection (PD-003).
        trunking: The trunking size (PD-004).
        unknown_kinds: Symbol kinds the library cannot draw, so a caller can
            warn before rendering rather than discover placeholders after.
    """

    title: str
    supply: str
    incomer: str
    components: list[SchematicComponent]
    connections: list[SchematicConnection]
    groups: list[SchematicGroup]
    enclosure: CalcValue
    trunking: CalcValue
    unknown_kinds: list[str]
