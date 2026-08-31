"""Enclosure sizing (PD-003).

The deterministic replacement for flipping through a catalogue: given a
component list, work out how much DIN rail it needs and pick the smallest
standard enclosure that holds it.

A lookup against standard sizes, not geometric optimisation. Panels are built
from catalogue enclosures by convention, so the answer is always one of the
sizes PD-001 ingested — or a refusal.

**Where each number comes from, and what this module will not invent.**

* *Component widths* come from PD-002, which reads them off manufacturer
  datasheets. A component whose width is not sourced raises there rather than
  being estimated here.
* *Enclosure dimensions* come from PD-001's ingested catalogue: 266 real
  records, external W x H x D as published.
* *Rail capacity per row* is **not in the catalogue and is not derived.** PD-001
  established that zero of 53 ingested enclosures publish a module capacity,
  and that dividing an enclosure's width by a module pitch does not recover one
  — the six rails that publish both a count and a width imply a pitch drifting
  from 17.9 to 19.9 mm. So the usable rail length per row is a required input
  the caller supplies from the enclosure's own drawing, and a sizing run
  without one refuses instead of guessing.

That last point is the whole shape of this module. It would be easy to write a
version that multiplies width by a fudge factor and always returns an
enclosure; it would also be wrong in the direction of a panel that does not
close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from app.ai.tools.din_module_width import ComponentCategory, module_width_for
from app.core.errors import ValidationError
from app.models.schemas.products import ProductClass, ProductRecord, sizing_candidates


@dataclass(frozen=True)
class ComponentSpec:
    """One line of a panel schedule.

    Attributes:
        category: Which device family, for the width lookup.
        series: Manufacturer series, required where a category has more than
            one sourced series.
        poles: Pole count; module-pitch devices scale linearly with it.
        quantity: How many of this line.
        group: Functional row this line belongs to, e.g. ``"incoming"``.
            Components in different groups are never packed onto the same rail,
            because a panel schedule's rows are a wiring decision rather than a
            packing one.
    """

    category: ComponentCategory
    quantity: int
    series: str | None = None
    poles: int = 1
    group: str = "main"


@dataclass(frozen=True)
class RowRequirement:
    """How much rail one functional group needs.

    Attributes:
        group: The group's name.
        width_mm: Total width of its components.
        rows: Rows needed at the caller's usable rail length.
    """

    group: str
    width_mm: Decimal
    rows: int


@dataclass(frozen=True)
class EnclosureSizingResult:
    """The chosen enclosure and the working behind it.

    Attributes:
        enclosure: The selected catalogue record.
        requirements: Per-group rail requirement.
        total_rows: Rows needed across every group.
        total_width_mm: Total component width, all groups.
    """

    enclosure: ProductRecord
    requirements: tuple[RowRequirement, ...]
    total_rows: int
    total_width_mm: Decimal


def _component_width(component: ComponentSpec) -> Decimal:
    """Return the rail width one line occupies.

    Args:
        component: The schedule line.

    Returns:
        Width in millimetres for the whole quantity.

    Raises:
        ValidationError: If ``quantity`` is not positive, or PD-002 has no
            sourced width for the category and series.
    """
    if component.quantity < 1:
        raise ValidationError(f"component quantity must be at least 1, got {component.quantity}")

    unit = module_width_for(component.category, series=component.series, poles=component.poles)
    return unit * component.quantity


def rail_requirements(
    components: list[ComponentSpec], *, usable_rail_mm: Decimal
) -> tuple[RowRequirement, ...]:
    """Work out how many rail rows each functional group needs.

    Args:
        components: The panel schedule.
        usable_rail_mm: Usable rail length in one row, from the enclosure's
            drawing. Not derived from the enclosure's width — see the module
            docstring.

    Returns:
        One requirement per group, in first-seen order.

    Raises:
        ValidationError: If the schedule is empty, the rail length is not
            positive, a component has no sourced width, or a single component
            is wider than one row.

    Source:
        Component widths come from ``app.ai.tools.din_module_width``, which
        reads them off manufacturer datasheets (ABB S201-C63, order code
        2CDS251001R0634: 17.5 mm per module; S203-C32, 2CDS253001R0324: 3
        spacings / 52.5 mm). No width originates here.

        ``usable_rail_mm`` is supplied by the caller from the enclosure's own
        drawing. It is deliberately not derived from the enclosure's width:
        PD-001 established that no ingested enclosure publishes a rail
        capacity, and that the six rails publishing both a count and a width
        imply a pitch drifting from 17.9 to 19.9 mm, so no constant recovers
        it.

    Groups are packed independently and each rounds up to a whole row. Packing
    two groups onto one rail would save space on paper and produce a panel
    whose incoming and outgoing sections share a row, which is not how a
    schedule is wired.
    """
    if not components:
        raise ValidationError("cannot size an enclosure for an empty component list")

    if usable_rail_mm <= 0:
        raise ValidationError(f"usable rail length must be positive, got {usable_rail_mm} mm")

    totals: dict[str, Decimal] = {}
    for component in components:
        width = _component_width(component)
        single = width / component.quantity
        if single > usable_rail_mm:
            # No number of rows accommodates a part wider than a row. Refusing
            # is the honest answer; rounding up would report a fit that cannot
            # be built.
            raise ValidationError(
                f"a single {component.category.value} is {single} mm wide, which "
                f"exceeds the {usable_rail_mm} mm usable rail length"
            )
        totals[component.group] = totals.get(component.group, Decimal(0)) + width

    requirements = []
    for group, width in totals.items():
        # `math.ceil`, not `-(-a // b)`. Decimal's floor division truncates
        # toward zero rather than flooring, so the negation trick returns 0
        # for 210 mm on a 465 mm rail and 1 for 500 mm -- a panel sized a row
        # short, which builds as a component with nowhere to go.
        rows = math.ceil(width / usable_rail_mm)
        requirements.append(RowRequirement(group=group, width_mm=width, rows=rows))
    return tuple(requirements)


def size_enclosure(
    components: list[ComponentSpec],
    *,
    catalogue: list[ProductRecord],
    usable_rail_mm: Decimal,
    row_pitch_mm: Decimal,
) -> EnclosureSizingResult:
    """Select the smallest standard enclosure that holds a component list.

    Args:
        components: The panel schedule.
        catalogue: Ingested product records, from PD-001.
        usable_rail_mm: Usable rail length per row, from the enclosure drawing.
        row_pitch_mm: Vertical spacing between rail rows, also from the
            drawing.

    Returns:
        The chosen enclosure with the working behind it.

    Raises:
        ValidationError: If no standard enclosure fits, if the schedule is
            empty, if a required width is unsourced, or if either rail figure
            is not positive.

    Source:
        Enclosure dimensions come from PD-001's ingested catalogue -- 266 real
        records filtered from an EPLAN Data Portal export, external W x H x D
        exactly as the manufacturer publishes them. Rail length and row pitch
        are caller-supplied from the enclosure drawing, for the reason given
        in ``rail_requirements``. This function selects; it computes no
        dimension of its own.

    **A refusal is a real outcome.** The specification is explicit that a list
    which fits nothing must say so rather than being force-fitted into the
    closest available size, and this returns the largest candidate's dimensions
    in the message so the caller can see how far short it fell.

    Covers are not candidates. `sizing_candidates` excludes them, because a
    cover is a door rather than somewhere to mount a component — see
    ``app.models.schemas.products``.
    """
    if row_pitch_mm <= 0:
        raise ValidationError(f"row pitch must be positive, got {row_pitch_mm} mm")

    requirements = rail_requirements(components, usable_rail_mm=usable_rail_mm)
    total_rows = sum(requirement.rows for requirement in requirements)
    total_width = sum((requirement.width_mm for requirement in requirements), Decimal(0))
    needed_height = row_pitch_mm * total_rows

    # Width and height are pulled out alongside the record so the rest of this
    # function works with plain Decimals. Both are optional on a ProductRecord
    # -- a cover legitimately publishes no depth -- and an enclosure missing
    # either cannot be sized against at all.
    measured: list[tuple[ProductRecord, Decimal, Decimal]] = []
    for record in sizing_candidates(catalogue):
        if record.product_class not in (
            ProductClass.ENCLOSURE,
            ProductClass.FLUSH_ENCLOSURE,
        ):
            continue
        width, height = record.width_mm, record.height_mm
        if width is None or height is None:
            continue
        measured.append((record, width, height))

    if not measured:
        raise ValidationError("the catalogue holds no enclosure with both a width and a height")

    # The rail has to fit inside the enclosure, and the rows have to fit above
    # one another. Dropping either check selects a cabinet whose parts do not
    # assemble.
    fitting = [
        entry for entry in measured if entry[1] >= usable_rail_mm and entry[2] >= needed_height
    ]
    if not fitting:
        record, width, height = max(measured, key=lambda e: e[1] * e[2])
        raise ValidationError(
            f"no standard enclosure fits: {total_rows} row(s) need "
            f"{needed_height} mm of height, and the largest catalogue enclosure "
            f"({record.sku}) is {width} x {height} mm. This needs custom sizing."
        )

    # Smallest by face area, then by depth, then by SKU. The last key is not
    # cosmetic: two catalogue entries can share a face, and a selection that
    # varied between runs would make a BOM irreproducible.
    chosen = min(
        fitting,
        key=lambda e: (
            e[1] * e[2],
            e[0].depth_mm if e[0].depth_mm is not None else Decimal(0),
            e[0].sku,
        ),
    )[0]

    return EnclosureSizingResult(
        enclosure=chosen,
        requirements=requirements,
        total_rows=total_rows,
        total_width_mm=total_width,
    )
