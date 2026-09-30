"""Control panel bill-of-materials generation.

Pure functions. Each formula cites the manufacturer guide it came from.

The BOM holds only what a sourced table can size: a drive for each
variable-speed load (ACS880-01 ratings), an outgoing cable for each load with
a current (IEC 60364-5-52 via ABB's handbook), the enclosure as given, and
the heat balance (IEC 60890 via Rittal), for each motor started across the
line its Type 2 coordinated breaker, contactor and overload relay (ABB's
coordination tables), for each drive its aR input fuses (ACS880-01 hardware
manual), and for each copper outgoing cable its terminals (Siemens LV 10).
Protection for loads with neither a drive nor a starter has no sourced
selection table here, so it is named in the result's notes rather than
guessed.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.tools import cable_sizing, motor_starter, terminal_blocks, vfd_selection
from app.core.errors import ValidationError
from app.models.schemas.calculations import (
    BomLine,
    BomLineKind,
    BomNote,
    ConductorMaterial,
    DutyClass,
    EnclosureConstraints,
    EnclosurePlacement,
    LoadScheduleItem,
    PanelBomResult,
    StartType,
)
from app.models.schemas.search import Citation

RITTAL_GUIDE_ID = "rittal-enclosure-and-process-cooling-2014"
RITTAL_GUIDE_TITLE = "Enclosure and process cooling (Rittal technology library, 2014)"

#: Heat transfer coefficient of sheet steel, W/m²K (Rittal, p. 24 as printed).
SHEET_STEEL_K = Decimal("5.5")

#: Pages of the Rittal guide, as the PDF numbers them.
_AREA_TABLE_PAGE = 25
_HEAT_BALANCE_PAGE = 40

#: The conductor's chemical symbol, as a cable's designation writes it.
_METAL = {ConductorMaterial.COPPER: "Cu", ConductorMaterial.ALUMINIUM: "Al"}

#: Table 5 stops at 20 grouped circuits; more are refused by the cable sizing.
_MAX_GROUPED = 20


def _plain(value: Decimal) -> str:
    """Write a number without trailing zeros or an exponent: 10, not 1E+1."""
    return format(value.normalize(), "f")


def _rittal(page: int, section: str) -> Citation:
    """Cite a page of the Rittal guide."""
    return Citation(
        document_id=RITTAL_GUIDE_ID,
        document_title=RITTAL_GUIDE_TITLE,
        manufacturer="Rittal",
        page=page,
        section=section,
    )


def effective_area_m2(
    *,
    width_m: Decimal,
    height_m: Decimal,
    depth_m: Decimal,
    placement: EnclosurePlacement,
) -> Decimal:
    """Return the enclosure's effective heat-dissipating surface area, in m².

    Source:
        Rittal, *Enclosure and process cooling* (technology library 2014),
        "Enclosure installation type according to IEC 60 890", p. 24 as
        printed.

    Args:
        width_m: External width, in metres.
        height_m: External height, in metres.
        depth_m: External depth, in metres.
        placement: How the enclosure stands.

    Returns:
        The effective area A.

    Raises:
        ValidationError: If a dimension is not a positive number.
    """
    for name, value in (("width", width_m), ("height", height_m), ("depth", depth_m)):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"enclosure {name} must be positive, got {value}")
    w, h, d = width_m, height_m, depth_m
    k18, k14, k07 = Decimal("1.8"), Decimal("1.4"), Decimal("0.7")
    formulas = {
        EnclosurePlacement.SINGLE_FREE_STANDING: k18 * h * (w + d) + k14 * w * d,
        EnclosurePlacement.SINGLE_WALL: k14 * w * (h + d) + k18 * h * d,
        EnclosurePlacement.SUITE_END_FREE_STANDING: k14 * d * (w + h) + k18 * w * h,
        EnclosurePlacement.SUITE_END_WALL: k14 * h * (w + d) + k14 * w * d,
        EnclosurePlacement.SUITE_MIDDLE_FREE_STANDING: k18 * w * h + k14 * w * d + h * d,
        EnclosurePlacement.SUITE_MIDDLE_WALL: k14 * w * (h + d) + h * d,
        EnclosurePlacement.SUITE_MIDDLE_WALL_COVERED_ROOF: k14 * w * h + k07 * w * d + h * d,
    }
    return formulas[placement]


def enclosure_heat_load_w(
    *,
    items: list[LoadScheduleItem],
    constraints: EnclosureConstraints,
) -> tuple[Decimal, Decimal]:
    """Return the heat given off inside the enclosure, and what cooling must remove.

    The surface sheds k · A · ΔT, with ΔT the permitted rise from ambient to
    the maximum internal temperature; whatever the equipment dissipates
    beyond that needs active cooling.

    Source:
        Rittal, *Enclosure and process cooling* (technology library 2014),
        "Active heat dissipation", worked example p. 39 as printed: Qs = k ·
        A · (Ti - Tu), Qe = Qv - Qs; k = 5.5 W/m²K for sheet steel. Area by
        `effective_area_m2` (IEC 60890).

    Args:
        items: The loads, with the heat each dissipates inside the enclosure.
        constraints: Enclosure size, placement and temperatures.

    Returns:
        (Total heat load Qv, required cooling output), in watts; the second is
        zero if the surface alone suffices.

    Raises:
        ValidationError: If the maximum internal temperature is not above
            ambient, or a dissipation is negative.
    """
    rise = constraints.max_internal_temp_c - constraints.ambient_temp_c
    if not rise.is_finite() or rise <= 0:
        raise ValidationError(
            "max_internal_temp_c must be above ambient_temp_c; with no permitted "
            "rise, the enclosure surface sheds nothing and the balance is undefined"
        )
    heat = Decimal(0)
    for item in items:
        if item.dissipation_w is None:
            continue
        if not item.dissipation_w.is_finite() or item.dissipation_w < 0:
            raise ValidationError(f"{item.tag}: dissipation must not be negative")
        heat += item.dissipation_w
    area = effective_area_m2(
        width_m=Decimal(constraints.width_mm) / 1000,
        height_m=Decimal(constraints.height_mm) / 1000,
        depth_m=Decimal(constraints.depth_mm) / 1000,
        placement=constraints.placement,
    )
    shed = SHEET_STEEL_K * area * rise
    return heat, max(Decimal(0), heat - shed)


def _starter_lines(
    load: LoadScheduleItem,
    constraints: EnclosureConstraints,
    *,
    start: StartType,
    power_kw: Decimal,
    current_a: Decimal,
) -> list[BomLine]:
    """The breaker, contactor(s) and overload relay one motor's starter needs."""
    selection = motor_starter.select_starter(
        motor_power_kw=power_kw,
        motor_current_a=current_a,
        start=start,
        supply_voltage_v=constraints.supply_voltage_v,
        fault_level_ka=constraints.fault_level_ka,
    )
    row = selection.row
    base = {"tag": load.tag, "load": load.description, "start": start.value}
    lines = [
        BomLine(
            part_reference=row.breaker,
            description=f"{load.tag}: circuit-breaker, magnetic trip {row.magnetic_trip_a} A",
            quantity=1,
            source=selection.source,
            kind=BomLineKind.BREAKER,
            details={**base, "trip_a": row.magnetic_trip_a},
        )
    ]
    roles = ("line", "delta", "star") if len(row.contactors) == 3 else ("line",)
    for role, contactor in zip(roles, row.contactors, strict=True):
        lines.append(
            BomLine(
                part_reference=contactor,
                description=f"{load.tag}: {role} contactor",
                quantity=1,
                source=selection.source,
                kind=BomLineKind.CONTACTOR,
                details={**base, "role": role},
            )
        )
    if row.overload is not None and row.overload_range_a is not None:
        low, high = row.overload_range_a
        lines.append(
            BomLine(
                part_reference=row.overload,
                description=f"{load.tag}: overload relay, {low}-{high} A",
                quantity=1,
                source=selection.source,
                kind=BomLineKind.OVERLOAD,
                details={**base, "min_a": low, "max_a": high},
            )
        )
    return lines


def _terminal_lines(
    load: LoadScheduleItem, cross_section_mm2: Decimal, current_a: Decimal
) -> list[BomLine]:
    """Return a load's outgoing terminals: three phase and, where listed, one PE.

    Raises:
        ValidationError: If no terminal clamps the cable and carries its current.
    """
    try:
        terminal = terminal_blocks.select_terminal(
            cross_section_mm2=cross_section_mm2, current_a=current_a
        )
    except ValidationError as exc:
        raise ValidationError(f"{load.tag}: {exc}") from exc
    base = {"tag": load.tag, "size": terminal.size_mm2, "max_a": terminal.max_current_a}
    lines = [
        BomLine(
            part_reference=terminal.article,
            description=(
                f"{load.tag}: through-type terminal {terminal.size_mm2} mm², "
                f"up to {terminal.max_current_a} A"
            ),
            quantity=3,
            source=terminal.source,
            kind=BomLineKind.TERMINAL,
            details={**base, "role": "phase"},
        )
    ]
    if terminal.pe_article is not None:
        lines.append(
            BomLine(
                part_reference=terminal.pe_article,
                description=f"{load.tag}: PE terminal {terminal.size_mm2} mm²",
                quantity=1,
                source=terminal.source,
                kind=BomLineKind.TERMINAL,
                details={**base, "role": "pe"},
            )
        )
    return lines


def build_bom(
    *,
    loads: list[LoadScheduleItem],
    constraints: EnclosureConstraints,
) -> PanelBomResult:
    """Expand a load schedule into the sourced lines of a panel BOM.

    Each variable-speed load gets the smallest ACS880-01 carrying its current
    at the enclosure's maximum internal temperature -- the drive stands
    inside the panel, not in the room. Each load with a current gets an
    outgoing XLPE cable, copper or aluminium as the constraints say, sized
    for the room's ambient, grouped with every other outgoing cable. The enclosure is listed as given, and the
    heat balance says how much cooling it needs.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093) for drives and their
        input fuses; Siemens Catalog LV 10 (10/2022) for terminals; ABB
        *Electrical installation handbook* Vol. 2 (1SDC010001D0204) for
        cables and for motor starters (Tables 3, 5 and 6); Rittal *Enclosure and process cooling* for the heat balance
        per IEC 60890.

    Args:
        loads: The panel's load schedule.
        constraints: Enclosure size and placement, supply, and temperatures.

    Returns:
        The BOM lines, the heat load and required cooling, and notes on what
        is not included.

    Raises:
        ValidationError: If the schedule is empty or inconsistent, or a load
            falls outside the drive or cable tables.
    """
    if not loads:
        raise ValidationError("the load schedule is empty")
    tags = [load.tag for load in loads]
    if len(set(tags)) != len(tags):
        raise ValidationError("load tags must be unique")

    lines: list[BomLine] = []
    carrying = [load for load in loads if load.current_a is not None]
    grouped = len(carrying)
    if grouped > _MAX_GROUPED:
        raise ValidationError(
            f"{grouped} outgoing circuits exceed the grouping table (up to {_MAX_GROUPED})"
        )

    starters = 0
    drives = 0
    unprotected = 0
    for load in loads:
        if load.start is not None and load.variable_speed:
            raise ValidationError(
                f"{load.tag}: a drive-fed motor has no across-the-line starter; "
                "choose variable speed or a start type, not both"
            )
        if load.start is not None and (load.power_kw is None or load.current_a is None):
            raise ValidationError(
                f"{load.tag}: a starter is selected from the motor's power and nameplate current"
            )
        if load.variable_speed and load.current_a is None:
            raise ValidationError(f"{load.tag}: a variable-speed load needs its nameplate current")
        if load.power_kw is not None and load.current_a is None:
            raise ValidationError(
                f"{load.tag}: give the nameplate current; it is not inferred from power"
            )
        if load.current_a is None:
            continue
        if not load.variable_speed and load.start is None:
            unprotected += 1
        try:
            if load.variable_speed:
                drive = vfd_selection.select_frame(
                    required_current_a=load.current_a,
                    supply_voltage_v=constraints.supply_voltage_v,
                    duty_class=DutyClass.NORMAL,
                    altitude_m=Decimal(0),
                    ambient_temp_c=constraints.max_internal_temp_c,
                )
                type_code = drive.frame_reference.split(" ")[0]
                fuse = vfd_selection.input_fuse(type_code=type_code)
                lines.append(
                    BomLine(
                        part_reference=type_code,
                        description=f"{load.tag}: drive for {load.description}",
                        quantity=1,
                        source=vfd_selection.ratings_citation(constraints.supply_voltage_v),
                        kind=BomLineKind.DRIVE,
                        details={"tag": load.tag, "load": load.description},
                    )
                )
                lines.append(
                    BomLine(
                        part_reference=f"Bussmann {fuse.bussmann} {fuse.amps} A aR",
                        description=(
                            f"{load.tag}: drive input fuses, aR {fuse.amps} A, one per phase; "
                            f"needs at least {fuse.min_short_circuit_a} A prospective "
                            "short-circuit current"
                        ),
                        quantity=3,
                        source=fuse.source,
                        kind=BomLineKind.FUSE,
                        details={
                            "tag": load.tag,
                            "amps": fuse.amps,
                            "min_sc_a": fuse.min_short_circuit_a,
                        },
                    )
                )
                drives += 1
            if load.start is not None and load.power_kw is not None:
                lines.extend(
                    _starter_lines(
                        load,
                        constraints,
                        start=load.start,
                        power_kw=load.power_kw,
                        current_a=load.current_a,
                    )
                )
                starters += 1
            cable = cable_sizing.size_conductor(
                design_current_a=load.current_a,
                installation_method=constraints.cable_installation_method,
                ambient_temp_c=constraints.ambient_temp_c,
                grouped_circuits=grouped,
                conductor_material=constraints.cable_material,
                insulation_rating_c=90,
            )
        except ValidationError as exc:
            raise ValidationError(f"{load.tag}: {exc}") from exc
        section = _plain(cable.cross_section_mm2)
        lines.append(
            BomLine(
                part_reference=f"{_METAL[constraints.cable_material]} XLPE {section} mm²",
                description=(
                    f"{load.tag}: outgoing cable, method "
                    f"{constraints.cable_installation_method.value}, {grouped} grouped"
                ),
                quantity=1,
                source=cable_sizing.ampacity_citation(constraints.cable_installation_method),
                kind=BomLineKind.CABLE,
                details={
                    "tag": load.tag,
                    "load": load.description,
                    "method": constraints.cable_installation_method.value,
                    "grouped": str(grouped),
                    "material": constraints.cable_material.value,
                },
            )
        )
        if constraints.cable_material is ConductorMaterial.COPPER:
            lines.extend(_terminal_lines(load, cable.cross_section_mm2, load.current_a))

    lines.append(
        BomLine(
            part_reference=(
                f"Enclosure {constraints.width_mm}x{constraints.height_mm}x"
                f"{constraints.depth_mm} {constraints.ingress_rating}"
            ),
            description=f"Enclosure, {constraints.placement.value.replace('_', ' ')}",
            quantity=1,
            source=_rittal(_AREA_TABLE_PAGE, "Enclosure installation type to IEC 60 890"),
            kind=BomLineKind.ENCLOSURE,
            details={"placement": constraints.placement.value},
        )
    )

    heat, cooling = enclosure_heat_load_w(items=loads, constraints=constraints)
    notes: list[str] = []
    note_keys: list[BomNote] = []
    if unprotected:
        notes.append(
            "Protection for loads with neither a drive nor a starter is not included: "
            "no sourced selection table for them is held here."
        )
        note_keys.append(BomNote.NOT_INCLUDED)
    if drives:
        notes.append(
            "Drive input fuses operate fast enough only when the installation's "
            "prospective short-circuit current is at least the minimum on each line."
        )
        note_keys.append(BomNote.FUSE_MIN_SHORT_CIRCUIT)
    if constraints.cable_material is ConductorMaterial.ALUMINIUM:
        notes.append(
            "Terminals are selected from a copper terminal table; aluminium cables "
            "need terminals rated for aluminium, which are not listed."
        )
        note_keys.append(BomNote.TERMINALS_COPPER_ONLY)
    if starters and constraints.fault_level_ka is None:
        notes.append(
            "Starters are Type 2 coordinated up to 50 kA; confirm the panel's "
            "prospective short-circuit current does not exceed it."
        )
        note_keys.append(BomNote.FAULT_LEVEL_ASSUMED)
    if cooling > 0:
        rise = constraints.max_internal_temp_c - constraints.ambient_temp_c
        lines.append(
            BomLine(
                part_reference=f"Cooling {cooling.quantize(Decimal('1'))} W",
                description=(
                    f"Active cooling: {cooling.quantize(Decimal('1'))} W beyond what the "
                    f"surface sheds at {_plain(rise)} K rise; an air/air heat exchanger "
                    f"needs {(cooling / rise).quantize(Decimal('0.1'))} W/K"
                ),
                quantity=1,
                source=_rittal(_HEAT_BALANCE_PAGE, "Active heat dissipation"),
                kind=BomLineKind.COOLING,
                details={
                    "cooling_w": _plain(cooling.quantize(Decimal("1"))),
                    "rise_k": _plain(rise),
                    "qw": _plain((cooling / rise).quantize(Decimal("0.1"))),
                },
            )
        )
    if any(load.dissipation_w is None for load in loads):
        notes.append(
            "Loads without a dissipation add no heat; the cooling figure is only as "
            "complete as the schedule."
        )
        note_keys.append(BomNote.INCOMPLETE_DISSIPATION)
    return PanelBomResult(
        lines=lines,
        heat_load_w=heat,
        cooling_required_w=cooling,
        notes=notes,
        note_keys=note_keys,
    )
