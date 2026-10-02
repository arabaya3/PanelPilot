"""Distribution board design from a load schedule.

What is sized here is sized from a cited rule; what is a company's choice
comes from its profile; what neither settles is left on the board as a note.

* Each load's design current Ib is P / (k Ur cosφ) (ABB handbook Annex B).
* Its circuit-breaker is the company's fixed rating for that kind of load
  where the profile sets one and it carries Ib, otherwise the smallest
  curve C rating at or above Ib (Table 2.3). Its cable is the smallest that
  carries In as installed (ABB handbook §2.2.1), no smaller than the
  company's minimum, so Ib <= In <= Iz holds by construction (§2.3).
* Loads the profile puts under a residual current device are grouped by
  sensitivity, at most ``max_circuits_per_rcd`` to a group, in schedule
  order. A residual current circuit-breaker has no overcurrent protection
  of its own (handbook §5.7), so each group is fed through a group
  circuit-breaker rated for the group's most loaded line conductor (and no
  lower than the largest breaker it feeds), and the RCCB's rated current is
  the smallest preferred value at or above it.
* Discrimination: each breaker is rated at least the company's ratio (1.6 by
  default) times the largest breaker after it, so it holds on overload: a
  group breaker, as far as its RCCB's ratings allow; the incomer; and a
  feeder, against the breakers its sub-board's switch feeds (``project``).
  A sub-board's incomer is a switch-disconnector rated no lower than the
  feeder breaker that protects it: a breaker there would be one more level
  for the ratio to multiply through. On short circuit,
  miniature breakers discriminate only up to the upstream one's instantaneous
  trip (5 In for curve C, IEC 60898-1), which the board says.
* Single-phase loads are spread over L1-L3 largest first onto the least
  loaded conductor. The resulting imbalance is checked against the profile.
* The incomer is rated for the most loaded line conductor with no diversity
  applied; a company's demand factors are not assumed.
* Every breaker but a motor starter's is given the smallest standard breaking
  capacity at or above the board's prospective fault current (a starter's
  coordination tables hold to 50 kA, ``motors``). Beyond the largest held, a
  breaker's capacity is left open and the board says back-up protection is
  needed.
* A circuit whose cable length is given has its voltage drop checked against
  the company's limit from the origin, less what the board's own feeders
  dropped, and its cable enlarged where that is needed (``voltage_drop``).
* Such a circuit's cable is also enlarged until a short circuit at its far
  end trips its breaker at once, and each cable carries the ``k²S²`` it
  withstands for the breaker's let-through energy (``short_circuit``).
* Where the board's ``Ze`` is known too, its breaker is checked to disconnect
  an earth fault at the cable's far end, and the cable enlarged where it
  would not (``disconnection``). A circuit under a residual current device
  disconnects by it; in a TT system every circuit needs one.

No part is selected here: devices carry ratings, not articles, until a
catalogue is chosen, and the parts list says so rather than naming one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from app.ai.tools import cable_sizing, feeder_protection
from app.core.errors import ValidationError
from app.design import disconnection, motors, short_circuit, voltage_drop
from app.design.notes import note
from app.models.schemas.calculations import ConductorMaterial
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    CompanyProfile,
    DesignNote,
    Device,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    MotorStarter,
    Phase,
)

#: The power factor assumed when a load gives none: the handbook's Annex B
#: load-current table is drawn up for cosφ = 0.9.
DEFAULT_POWER_FACTOR = Decimal("0.9")

#: Rated currents (AC-1) of modular installation contactors, as the common
#: ranges list them; the contactor is rated no lower than its breaker.
CONTACTOR_RATINGS: tuple[Decimal, ...] = tuple(Decimal(r) for r in ("20", "25", "40", "63"))

#: Preferred rated currents of residual current circuit-breakers, IEC 61008-1.
RCCB_RATINGS: tuple[Decimal, ...] = tuple(
    Decimal(r) for r in ("16", "25", "40", "63", "80", "100", "125")
)

#: Standard rated breaking capacities, kA: Icn of IEC 60898-1 miniature
#: breakers (6, 10, 15, 25) and the common Icu steps of moulded-case ones.
BREAKING_CAPACITIES: tuple[Decimal, ...] = tuple(
    Decimal(r) for r in ("6", "10", "15", "25", "36", "50")
)

#: Rated currents of switch-disconnectors (IEC 60947-3), as the common
#: ranges list them.
ISOLATOR_RATINGS: tuple[Decimal, ...] = tuple(
    Decimal(r) for r in ("16", "25", "32", "40", "63", "80", "100", "125", "160", "200", "250")
)

_LINES = (Phase.L1, Phase.L2, Phase.L3)
_SQRT3 = Decimal(3).sqrt()


@dataclass
class _Sized:
    load: LoadInput
    index: int
    current_a: Decimal
    rated_a: Decimal
    curve: str
    section_mm2: Decimal
    phase: Phase = Phase.L1
    motor: motors.MotorCircuit | None = None
    #: False when no miniature breaker carries Ib: a moulded-case breaker is
    #: needed, rated_a is then Ib itself, and the breaker is left unselected.
    selected: bool = True
    #: The drop along its own cable, where its length is given and tabulated.
    drop_percent: Decimal | None = None
    #: Zs at its far end, where its breaker was checked to disconnect by it.
    earth_loop_ohm: Decimal | None = None
    #: How it disconnects on an earth fault: "breaker", "rcd" or "unchecked".
    earth_fault: str = "unchecked"


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _rating(current: Decimal) -> Decimal | None:
    """The smallest miniature breaker rating carrying ``current``; none beyond the table."""
    try:
        return Decimal(feeder_protection.smallest_rating(current))
    except ValidationError:
        return None


def _phase_voltage(request: DistributionBoardRequest) -> Decimal:
    supply = request.supply
    if supply.phases == 1:
        return supply.voltage_v
    return (supply.voltage_v / _SQRT3).quantize(Decimal(1))


def _size(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    load: LoadInput,
    index: int,
    notes: list[DesignNote],
    budget: voltage_drop.Budget,
    sub_board_after_a: dict[str, Decimal],
) -> _Sized:
    three_phase = load.phases == 3
    if three_phase and request.supply.phases == 1:
        raise ValidationError(
            "a three-phase load on a single-phase supply", code="three_phase_on_single_phase"
        ).about(load.description)
    if load.starter is not None:
        return _size_motor(request, profile, load, index, notes, budget)
    power_factor = load.power_factor or DEFAULT_POWER_FACTOR
    current = feeder_protection.load_current(
        power_kw=load.power_kw,
        voltage_v=request.supply.voltage_v if three_phase else _phase_voltage(request),
        power_factor=power_factor,
        three_phase=three_phase,
    )
    rule = profile.circuit_rules.get(load.load)
    fixed = rule.breaker_a if rule else None
    # A feeder is rated for discrimination with the sub-board's breakers.
    after = sub_board_after_a.get(load.feeds) if load.feeds else None
    needed = max(current, after * profile.discrimination_ratio) if after else current
    selected = True
    if fixed is not None and fixed >= needed:
        rated = fixed
    else:
        rating = _rating(needed)
        if rating is not None:
            rated = rating
        else:
            # A main board's large feeders are moulded-case breakers, which
            # the miniature-breaker table does not hold: left unselected,
            # as the incomer is, rather than refusing the whole board.
            rated, selected = needed, False
            notes.append(note("mccb_needed", load=load.description, current=_plain(needed)))
        if after and selected and rated != _rating(current):
            notes.append(
                note(
                    "discrimination_feeder_raised",
                    board=load.feeds,
                    rated=_plain(rated),
                    ratio=_plain(profile.discrimination_ratio),
                    after=_plain(after),
                )
            )
        if fixed is not None and selected:
            notes.append(
                note(
                    "above_company_rating",
                    load=load.description,
                    current=_plain(current),
                    fixed=_plain(fixed),
                    kind=load.load.value,
                    rated=_plain(rated),
                )
            )
    conditions = request.conditions
    try:
        cable = cable_sizing.size_conductor(
            design_current_a=rated,
            installation_method=conditions.installation_method,
            ambient_temp_c=conditions.ambient_temp_c,
            grouped_circuits=conditions.grouped_circuits,
            conductor_material=conditions.conductor_material,
            insulation_rating_c=conditions.insulation_rating_c,
            three_phase=three_phase,
        )
    except ValidationError as exc:
        raise exc.about(load.description) from exc
    section = cable.cross_section_mm2
    if rule and rule.cable_mm2 is not None and rule.cable_mm2 > section:
        section = rule.cable_mm2
    voltage = request.supply.voltage_v if three_phase else _phase_voltage(request)
    run = voltage_drop.Run(current, load.length_m, three_phase, voltage) if load.length_m else None
    section, drop = _check_drop(request, profile, budget, load, run, section, notes)
    sized = _Sized(
        load=load,
        index=index,
        current_a=current,
        rated_a=rated,
        curve=rule.curve if rule else "C",
        section_mm2=section,
        selected=selected,
        drop_percent=drop,
    )
    # Short circuit first: the earth fault loop, checked last, only ever
    # enlarges the cable further, which a short circuit trips on sooner.
    _check_short_circuit(request, sized, notes)
    _check_disconnection(request, profile, sized, notes)
    if sized.section_mm2 != section:
        # A larger cable drops less; the drop shown is the one it has.
        _redo_drop(request, sized)
    return sized


def _under_rcd(profile: CompanyProfile, load: LoadInput) -> bool:
    rule = profile.circuit_rules.get(load.load)
    return rule is not None and rule.residual_current_ma is not None


def _check_disconnection(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    item: _Sized,
    notes: list[DesignNote],
) -> None:
    """Make sure an earth fault at a circuit's far end disconnects it in time.

    A circuit under a residual current device disconnects by it. Otherwise,
    in a TN system, its breaker must see enough current through the loop
    (``disconnection``), and its cable is enlarged until it does; in a TT
    system the board says the circuit needs one. Sets the item's
    ``earth_fault``, ``earth_loop_ohm`` and, where enlarged, its section.
    """
    load = item.load
    if _under_rcd(profile, load):
        item.earth_fault = "rcd"
        return
    if request.supply.earthing.upper() == "TT":
        notes.append(note("earth_fault_tt_no_rcd", load=load.description))
        return
    external = request.supply.earth_loop_ohm
    if external is None or load.length_m is None or not item.selected or item.motor:
        return
    conditions = request.conditions
    checked = disconnection.fit(
        external_ohm=external,
        length_m=load.length_m,
        section_mm2=item.section_mm2,
        material=conditions.conductor_material,
        insulation_rating_c=conditions.insulation_rating_c,
        phase_voltage_v=_phase_voltage(request),
        rated_a=item.rated_a,
        curve=item.curve,
    )
    if checked is None:
        return
    item.earth_fault = "breaker"
    item.earth_loop_ohm = checked.loop_ohm
    if not checked.within:
        notes.append(
            note(
                "earth_fault_exceeded",
                load=load.description,
                loop=_plain(checked.loop_ohm),
                limit=_plain(checked.max_ohm),
                rated=_plain(item.rated_a),
                curve=item.curve,
            )
        )
    elif checked.section_mm2 != item.section_mm2:
        notes.append(
            note(
                "earth_fault_upsized",
                load=load.description,
                sized=_plain(item.section_mm2),
                section=_plain(checked.section_mm2),
                loop=_plain(checked.loop_ohm),
                limit=_plain(checked.max_ohm),
            )
        )
        item.section_mm2 = checked.section_mm2


def _check_short_circuit(
    request: DistributionBoardRequest, item: _Sized, notes: list[DesignNote]
) -> None:
    """Make sure a short circuit at a circuit's far end trips its breaker at once.

    Enlarges the item's cable where it would not (``short_circuit``).
    """
    load = item.load
    if load.length_m is None or not item.selected or item.motor:
        return
    checked = short_circuit.fit(
        length_m=load.length_m,
        section_mm2=item.section_mm2,
        material=request.conditions.conductor_material,
        phase_voltage_v=_phase_voltage(request),
        rated_a=item.rated_a,
        curve=item.curve,
    )
    if checked is None:
        return
    if not checked.within:
        notes.append(
            note(
                "short_circuit_min_exceeded",
                load=load.description,
                current=_plain(checked.min_current_a),
                trip=_plain(checked.trip_a),
                rated=_plain(item.rated_a),
                curve=item.curve,
            )
        )
    elif checked.section_mm2 != item.section_mm2:
        notes.append(
            note(
                "short_circuit_min_upsized",
                load=load.description,
                sized=_plain(item.section_mm2),
                section=_plain(checked.section_mm2),
                current=_plain(checked.min_current_a),
                trip=_plain(checked.trip_a),
            )
        )
        item.section_mm2 = checked.section_mm2


def _redo_drop(request: DistributionBoardRequest, item: _Sized) -> None:
    """The drop along an item's cable again, after the cable was enlarged."""
    load = item.load
    if item.drop_percent is None or load.length_m is None:
        return
    three_phase = load.phases == 3
    run = voltage_drop.Run(
        item.current_a,
        load.length_m,
        three_phase,
        request.supply.voltage_v if three_phase else _phase_voltage(request),
    )
    item.drop_percent = voltage_drop.percent(run, item.section_mm2, request.conditions).quantize(
        Decimal("0.01")
    )


def _earth_fault_notes(request: DistributionBoardRequest, sized: list[_Sized]) -> list[DesignNote]:
    """What the board says of its earth fault protection as a whole."""
    made: list[DesignNote] = []
    if request.supply.earthing.upper() == "TT":
        return made
    external = request.supply.earth_loop_ohm
    if external is None:
        if any(item.earth_fault != "rcd" for item in sized):
            made.append(note("earth_fault_no_ze"))
        return made
    if any(item.earth_fault == "breaker" for item in sized):
        made.append(
            note(
                "earth_fault_basis",
                ze=_plain(external),
                voltage=_plain(_phase_voltage(request)),
            )
        )
    unchecked = sum(1 for item in sized if item.earth_fault == "unchecked")
    if unchecked:
        made.append(note("earth_fault_unchecked", count=unchecked))
    return made


def _check_drop(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    budget: voltage_drop.Budget,
    load: LoadInput,
    run: voltage_drop.Run | None,
    section: Decimal,
    notes: list[DesignNote],
) -> tuple[Decimal, Decimal | None]:
    """Hold a cable's voltage drop within the limit, enlarging it if need be.

    Returns:
        The cable's section, and the drop along it where it was checked.
    """
    if run is None:
        return section, None
    checked = voltage_drop.fit(
        run,
        section,
        budget.limit(profile, load.load, load.feeds) - budget.upstream_percent,
        request.conditions,
    )
    if checked is None:
        notes.append(
            note("voltage_drop_untabulated", load=load.description, section=_plain(section))
        )
        return section, None
    limit = budget.limit(profile, load.load, load.feeds)
    total = checked.percent + budget.upstream_percent
    if not checked.within:
        notes.append(
            note(
                "voltage_drop_exceeded",
                load=load.description,
                total=_plain(total),
                own=_plain(checked.percent),
                upstream=_plain(budget.upstream_percent),
                limit=_plain(limit),
                section=_plain(section),
            )
        )
    elif checked.section_mm2 != section:
        notes.append(
            note(
                "voltage_drop_upsized",
                load=load.description,
                sized=_plain(section),
                section=_plain(checked.section_mm2),
                length=_plain(run.length_m),
                total=_plain(total),
                limit=_plain(limit),
            )
        )
    return checked.section_mm2, checked.percent


def _size_motor(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    load: LoadInput,
    index: int,
    notes: list[DesignNote],
    budget: voltage_drop.Budget,
) -> _Sized:
    """Size a motor's circuit: its devices from the starter tables, its cable for them.

    The breaker in a coordinated starter trips on short circuit only; the
    overload relay protects the cable, so each conductor is sized to carry
    what the relay is set to (Ib <= Ir <= Iz). Its voltage drop is taken at
    Ir running; a star-delta motor's at the winding current Ir/√3 in each of
    three loops across the line voltage.
    """
    conditions = request.conditions
    motor = motors.motor_circuit(index, load, request.supply, ambient_c=conditions.ambient_temp_c)
    try:
        cable = cable_sizing.size_conductor(
            design_current_a=motor.cable_current_a,
            installation_method=conditions.installation_method,
            ambient_temp_c=conditions.ambient_temp_c,
            grouped_circuits=conditions.grouped_circuits,
            conductor_material=conditions.conductor_material,
            insulation_rating_c=conditions.insulation_rating_c,
            three_phase=True,
        )
    except ValidationError as exc:
        raise exc.about(load.description) from exc
    run = None
    if load.length_m:
        star_delta = load.starter is MotorStarter.STAR_DELTA
        run = voltage_drop.Run(
            current_a=motor.current_a / _SQRT3 if star_delta else motor.current_a,
            length_m=load.length_m,
            three_phase=not star_delta,
            voltage_v=request.supply.voltage_v,
        )
    section, drop = _check_drop(request, profile, budget, load, run, cable.cross_section_mm2, notes)
    if load.length_m and load.starter is not None:
        section = _check_starting(request, profile, budget, load, motor, section, notes)
    sized = _Sized(
        load=load,
        index=index,
        current_a=motor.current_a,
        rated_a=motor.current_a,
        curve="",
        section_mm2=section,
        motor=motor,
        drop_percent=drop,
    )
    # A starter's breaker trips on short circuit only, at a setting its
    # tables do not give here: such a circuit is counted as unchecked.
    _check_disconnection(request, profile, sized, notes)
    return sized


#: Starting current as a multiple of Ir, by starter: a direct-on-line
#: start draws the locked-rotor current (IEC 60034-12 design N motors, about
#: 6 to 8 Ir; 6 is taken as typical), a star-delta start a third of it, and a
#: drive no more than the motor's rated current.
STARTING_MULTIPLE: dict[MotorStarter, Decimal] = {
    MotorStarter.DIRECT_ON_LINE: Decimal(6),
    MotorStarter.STAR_DELTA: Decimal(2),
}


def _check_starting(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    budget: voltage_drop.Budget,
    load: LoadInput,
    motor: motors.MotorCircuit,
    section: Decimal,
    notes: list[DesignNote],
) -> Decimal:
    """Hold the drop while a motor starts within the company's limit.

    The starting current flows in the three line conductors only (a star-delta
    motor starts in star, its second set of conductors idle), at cos phi 0.35.
    A drive limits it to the running current, which the running check covers.

    Returns:
        The cable's section, enlarged where starting needs it.
    """
    multiple = STARTING_MULTIPLE.get(load.starter) if load.starter else None
    if multiple is None or load.length_m is None:
        return section
    if request.conditions.conductor_material is not ConductorMaterial.COPPER:
        notes.append(note("starting_drop_unchecked", load=load.description))
        return section
    run = voltage_drop.Run(
        current_a=motor.current_a * multiple,
        length_m=load.length_m,
        three_phase=True,
        voltage_v=request.supply.voltage_v,
    )
    limit = profile.max_starting_voltage_drop_percent
    checked = voltage_drop.fit(
        run,
        section,
        limit - budget.upstream_percent,
        request.conditions,
        voltage_drop.starting_percent,
    )
    if checked is None:
        return section
    total = checked.percent + budget.upstream_percent
    if not checked.within:
        notes.append(
            note(
                "starting_drop_exceeded",
                load=load.description,
                total=_plain(total),
                limit=_plain(limit),
                section=_plain(section),
            )
        )
    elif checked.section_mm2 != section:
        notes.append(
            note(
                "starting_drop_upsized",
                load=load.description,
                sized=_plain(section),
                section=_plain(checked.section_mm2),
                multiple=_plain(multiple),
                total=_plain(total),
                limit=_plain(limit),
            )
        )
    return checked.section_mm2


def balance_phases(currents: list[tuple[int, Decimal, bool]]) -> dict[int, Phase]:
    """Assign single-phase loads to line conductors, largest first.

    Each single-phase load goes to whichever conductor carries least so far;
    a three-phase load adds its current to all three. Ties go to the lower
    conductor, so the result is the same every time.

    Args:
        currents: ``(index, Ib, three_phase)`` for each load.

    Returns:
        The phase of each load, by index.
    """
    totals = dict.fromkeys(_LINES, Decimal(0))
    assigned: dict[int, Phase] = {}
    for index, current, three_phase in currents:
        if three_phase:
            assigned[index] = Phase.THREE_PHASE
            for line in _LINES:
                totals[line] += current
    singles = sorted(
        ((i, c) for i, c, three in currents if not three), key=lambda item: (-item[1], item[0])
    )
    for index, current in singles:
        line = min(_LINES, key=lambda candidate: (totals[candidate], _LINES.index(candidate)))
        assigned[index] = line
        totals[line] += current
    return assigned


def phase_currents(circuits: list[Circuit]) -> dict[Phase, Decimal]:
    """Return the design current on each line conductor.

    Args:
        circuits: Circuits with their phases set.

    Returns:
        Sum of Ib on L1, L2 and L3.
    """
    totals = dict.fromkeys(_LINES, Decimal(0))
    for circuit in circuits:
        lines = _LINES if circuit.phase is Phase.THREE_PHASE else (circuit.phase,)
        for line in lines:
            totals[line] += circuit.design_current_a
    return totals


def _motor_circuit(
    request: DistributionBoardRequest, item: _Sized, upstream: str | None
) -> tuple[Circuit, Cable]:
    motor = item.motor
    assert motor is not None
    motor.devices[0].upstream_id = upstream or "incomer"
    number = item.index + 1
    cable = Cable(
        id=f"c{number}-cable",
        cores=motor.cable_cores,
        cross_section_mm2=item.section_mm2,
        material=(
            "Cu" if request.conditions.conductor_material is ConductorMaterial.COPPER else "Al"
        ),
        insulation="XLPE" if request.conditions.insulation_rating_c == 90 else "PVC",
        length_m=item.load.length_m,
        withstand_ka2s=_withstand(request, item.section_mm2),
    )
    circuit = Circuit(
        id=f"c{number}",
        description=item.load.description,
        load=item.load.load,
        power_kw=item.load.power_kw,
        design_current_a=item.current_a,
        phase=item.phase,
        upstream_id=upstream,
        device_ids=[device.id for device in motor.devices],
        cable_id=cable.id,
        starter=item.load.starter,
        voltage_drop_percent=item.drop_percent,
        earth_loop_ohm=item.earth_loop_ohm,
    )
    return circuit, cable


def _withstand(request: DistributionBoardRequest, section_mm2: Decimal) -> Decimal | None:
    conditions = request.conditions
    return short_circuit.withstand_ka2s(
        section_mm2, conditions.conductor_material, conditions.insulation_rating_c
    )


def breaking_capacity(fault_level_ka: Decimal) -> Decimal | None:
    """The smallest standard breaking capacity that clears a fault.

    Args:
        fault_level_ka: The prospective short-circuit current at the board.

    Returns:
        A value from :data:`BREAKING_CAPACITIES` at or above it; ``None``
        beyond the largest.
    """
    return next((rating for rating in BREAKING_CAPACITIES if rating >= fault_level_ka), None)


def _rate_breaking_capacity(
    devices: list[Device], starters: set[str], fault_level_ka: Decimal | None
) -> list[DesignNote]:
    """Give every breaker but a starter's the breaking capacity the fault needs."""
    if fault_level_ka is None:
        return [note("no_fault_level")]
    required = breaking_capacity(fault_level_ka)
    for device in devices:
        if device.kind is DeviceKind.CIRCUIT_BREAKER and device.id not in starters:
            device.breaking_capacity_ka = required
    if required is None:
        return [
            note(
                "breaking_capacity_beyond",
                fault=_plain(fault_level_ka),
                largest=_plain(BREAKING_CAPACITIES[-1]),
            )
        ]
    return [note("breaking_capacity", rating=_plain(required), fault=_plain(fault_level_ka))]


def _group_rating(carrying: Decimal, after: Decimal, ratio: Decimal) -> Decimal | None:
    """A group breaker's rating for discrimination with the breakers after it.

    Args:
        carrying: The rating that carries the group.
        after: The largest breaker it feeds.
        ratio: The company's discrimination ratio.

    Returns:
        The smallest rating at or above both ``carrying`` and ``ratio`` times
        ``after`` that a miniature breaker and an RCCB are held in; ``None``
        if there is none.
    """
    rating = _rating(max(carrying, after * ratio))
    if rating is None or rating > RCCB_RATINGS[-1]:
        return None
    return rating


def _rccb_rating(current: Decimal) -> Decimal:
    for rating in RCCB_RATINGS:
        if rating >= current:
            return rating
    raise ValidationError(
        f"a residual current group carrying {_plain(current)} A exceeds the largest "
        f"preferred RCCB rating held ({_plain(RCCB_RATINGS[-1])} A)",
        code="rccb_group_too_large",
        params={"current": _plain(current), "largest": _plain(RCCB_RATINGS[-1])},
    )


def _incomer(
    request: DistributionBoardRequest,
    most: Decimal,
    after: Decimal,
    ratio: Decimal,
    supply_breaker_a: Decimal | None,
    notes: list[DesignNote],
) -> Device:
    """The board's incomer.

    A board at the origin has a circuit-breaker, rated to discriminate with
    what it feeds. A sub-board fed from a board in the project has a
    switch-disconnector: the feeder breaker protects it, so a second breaker
    there would only be one more level for discrimination to climb. The
    switch is rated no lower than that breaker.
    """
    poles = 2 if request.supply.phases == 1 else 4
    if request.fed_from is not None:
        needed = max(most, supply_breaker_a or Decimal(0))
        rated = next((r for r in ISOLATOR_RATINGS if r >= needed), None)
        notes.append(note("incomer_isolator", board=request.fed_from))
        if rated is None:
            notes.append(note("isolator_unselected", current=_plain(needed)))
        return Device(
            id="incomer",
            kind=DeviceKind.SWITCH_DISCONNECTOR,
            poles=poles,
            rated_current_a=rated,
            description="Main switch",
        )
    incomer_carrying = _rating(most)
    incomer_rated = _rating(max(most, after * ratio))
    if incomer_carrying is None:
        notes.append(note("incomer_mccb", current=_plain(most)))
    elif incomer_rated is None:
        # A moulded-case incomer only for discrimination is not assumed.
        incomer_rated = incomer_carrying
        notes.append(
            note(
                "discrimination_incomer_not_met",
                rated=_plain(incomer_carrying),
                ratio=_plain(ratio),
                after=_plain(after),
            )
        )
    elif incomer_rated != incomer_carrying:
        notes.append(
            note(
                "discrimination_incomer_raised",
                rated=_plain(incomer_rated),
                ratio=_plain(ratio),
                after=_plain(after),
            )
        )
    return Device(
        id="incomer",
        kind=DeviceKind.CIRCUIT_BREAKER,
        poles=poles,
        rated_current_a=incomer_rated,
        curve="C" if incomer_rated is not None else None,
        description="Main incomer",
    )


def design_distribution_board(
    request: DistributionBoardRequest,
    profile: CompanyProfile,
    budget: voltage_drop.Budget | None = None,
    sub_board_after_a: dict[str, Decimal] | None = None,
    supply_breaker_a: Decimal | None = None,
) -> Board:
    """Design a distribution board's protection and cables from its load schedule.

    Args:
        request: The board's name, supply, load schedule and cable conditions.
        profile: The company whose rules apply.
        budget: What its feeders dropped already, and what each of its own
            feeders is held to; none for a board at the origin.
        sub_board_after_a: For each sub-board it feeds, by name, the largest
            breaker that sub-board's switch feeds, which the feeder
            discriminates with.
        supply_breaker_a: For a sub-board, the rating of the feeder breaker
            that protects it, which its switch is rated no lower than.

    Returns:
        The board, undesignated: designations are assigned when it is issued
        under a profile (``designations.designate_board``).

    Raises:
        ValidationError: If a load cannot be protected or cabled from the
            tables held, naming the load.
    """
    budget = budget or voltage_drop.Budget()
    notes: list[DesignNote] = []
    if budget.upstream_percent > 0:
        notes.append(note("voltage_drop_upstream", percent=_plain(budget.upstream_percent)))
    sized = [
        _size(request, profile, load, i, notes, budget, sub_board_after_a or {})
        for i, load in enumerate(request.loads)
    ]
    ratio = profile.discrimination_ratio
    single_phase_supply = request.supply.phases == 1
    if single_phase_supply:
        phases = {s.index: Phase.L1 for s in sized}
    else:
        phases = balance_phases([(s.index, s.current_a, s.load.phases == 3) for s in sized])
    for item in sized:
        item.phase = phases[item.index]

    devices: list[Device] = []
    cables: list[Cable] = []
    circuits: list[Circuit] = []
    group_poles = 2 if single_phase_supply else 4
    breaker_poles = 1 if single_phase_supply else 3

    # Residual current groups, by sensitivity, in schedule order.
    groups: dict[Decimal, list[list[_Sized]]] = {}
    ungrouped: list[_Sized] = []
    for item in sized:
        rule = profile.circuit_rules.get(item.load.load)
        sensitivity = rule.residual_current_ma if rule else None
        if sensitivity is None:
            ungrouped.append(item)
            continue
        chunks = groups.setdefault(sensitivity, [])
        if not chunks or len(chunks[-1]) >= profile.max_circuits_per_rcd:
            chunks.append([])
        chunks[-1].append(item)

    upstream: dict[int, str] = {}
    group_ratings: list[Decimal] = []
    group_number = 0
    for sensitivity, chunks in groups.items():
        for chunk in chunks:
            group_number += 1
            group_circuits = [
                Circuit(
                    id=f"tmp{s.index}",
                    description=s.load.description,
                    load=s.load.load,
                    power_kw=s.load.power_kw,
                    design_current_a=s.current_a,
                    phase=s.phase,
                )
                for s in chunk
            ]
            loaded = max(phase_currents(group_circuits).values())
            # Never below the largest breaker it feeds: Ib alone could rate a
            # group breaker under its own outgoing circuits.
            loaded = max(loaded, *(s.rated_a for s in chunk))
            try:
                carrying = Decimal(feeder_protection.smallest_rating(loaded))
            except ValidationError as exc:
                raise exc.about(f"RCD group {group_number}") from exc
            group_rated = _group_rating(carrying, max(s.rated_a for s in chunk), ratio)
            if group_rated is None:
                group_rated = carrying
                notes.append(
                    note(
                        "discrimination_group_not_met",
                        group=group_number,
                        rated=_plain(carrying),
                        ratio=_plain(ratio),
                        after=_plain(max(s.rated_a for s in chunk)),
                    )
                )
            elif group_rated != carrying:
                notes.append(
                    note(
                        "discrimination_group_raised",
                        group=group_number,
                        rated=_plain(group_rated),
                        ratio=_plain(ratio),
                        after=_plain(max(s.rated_a for s in chunk)),
                    )
                )
            group_ratings.append(group_rated)
            breaker_id = f"g{group_number}-breaker"
            rcd_id = f"g{group_number}-rcd"
            devices.append(
                Device(
                    id=breaker_id,
                    kind=DeviceKind.CIRCUIT_BREAKER,
                    poles=breaker_poles,
                    rated_current_a=group_rated,
                    curve="C",
                    description=f"Group {group_number} supply",
                    upstream_id="incomer",
                )
            )
            devices.append(
                Device(
                    id=rcd_id,
                    kind=DeviceKind.RESIDUAL_CURRENT_DEVICE,
                    poles=group_poles,
                    rated_current_a=_rccb_rating(group_rated),
                    residual_current_ma=sensitivity,
                    description=f"Group {group_number} residual current",
                    upstream_id=breaker_id,
                )
            )
            for item in chunk:
                upstream[item.index] = rcd_id

    for item in sorted(sized, key=lambda s: s.index):
        three_phase = item.load.phases == 3
        if item.motor is not None:
            circuit, cable = _motor_circuit(request, item, upstream.get(item.index))
            devices.extend(item.motor.devices)
            notes.extend(item.motor.notes)
            cables.append(cable)
            circuits.append(circuit)
            continue
        breaker = Device(
            id=f"c{item.index + 1}-breaker",
            kind=DeviceKind.CIRCUIT_BREAKER,
            poles=3 if three_phase else 1,
            rated_current_a=item.rated_a if item.selected else None,
            curve=item.curve if item.selected else None,
            description=item.load.description,
            upstream_id=upstream.get(item.index, "incomer"),
        )
        cable = Cable(
            id=f"c{item.index + 1}-cable",
            cores=5 if three_phase else 3,
            cross_section_mm2=item.section_mm2,
            material=(
                "Cu" if request.conditions.conductor_material is ConductorMaterial.COPPER else "Al"
            ),
            insulation="XLPE" if request.conditions.insulation_rating_c == 90 else "PVC",
            length_m=item.load.length_m,
            withstand_ka2s=_withstand(request, item.section_mm2),
        )
        devices.append(breaker)
        circuit_devices = [breaker.id]
        if item.load.controlled:
            rating = next((r for r in CONTACTOR_RATINGS if r >= item.rated_a), None)
            if rating is None:
                notes.append(
                    note(
                        "contactor_unselected",
                        load=item.load.description,
                        current=_plain(item.rated_a),
                    )
                )
            contactor = Device(
                id=f"c{item.index + 1}-contactor",
                kind=DeviceKind.CONTACTOR,
                poles=4 if three_phase else 2,
                rated_current_a=rating,
                description=f"{item.load.description} (PLC)",
                upstream_id=breaker.id,
            )
            devices.append(contactor)
            circuit_devices.append(contactor.id)
        cables.append(cable)
        circuits.append(
            Circuit(
                id=f"c{item.index + 1}",
                description=item.load.description,
                load=item.load.load,
                power_kw=item.load.power_kw,
                design_current_a=item.current_a,
                phase=item.phase,
                upstream_id=upstream.get(item.index),
                device_ids=circuit_devices,
                cable_id=cable.id,
                feeds=item.load.feeds,
                voltage_drop_percent=item.drop_percent,
                earth_loop_ohm=item.earth_loop_ohm,
            )
        )

    # Draw each residual current group's circuits together, groups in order,
    # then the circuits fed straight from the busbar.
    group_of = {f"g{n}-rcd": n for n in range(1, group_number + 1)}
    circuits.sort(key=lambda c: group_of.get(c.upstream_id or "", group_number + 1))

    totals = phase_currents(circuits)
    most = max(totals.values())
    least = min(totals.values())
    if not single_phase_supply and most > 0:
        imbalance = (most - least) / most * 100
        if imbalance > profile.max_phase_imbalance_percent:
            notes.append(
                note(
                    "phase_imbalance",
                    imbalance=_plain(imbalance.quantize(Decimal("0.1"))),
                    limit=_plain(profile.max_phase_imbalance_percent),
                )
            )
    # What the incomer feeds directly: group breakers and ungrouped circuits.
    after = max(
        [
            *group_ratings,
            *(s.rated_a for s in sized if s.index not in upstream),
        ]
    )
    incomer = _incomer(request, most, after, ratio, supply_breaker_a, notes)
    devices.insert(0, incomer)
    starters = {device.id for item in sized if item.motor for device in item.motor.devices}
    notes.extend(_rate_breaking_capacity(devices, starters, request.supply.fault_level_ka))

    spare = (len(circuits) * profile.spare_ways_percent / 100).to_integral_value(ROUND_CEILING)
    notes.append(
        note("spare_ways", count=_plain(spare), percent=_plain(profile.spare_ways_percent))
    )
    if any(s.load.power_factor is None and s.motor is None for s in sized):
        notes.append(note("default_power_factor"))
    if any(load.controlled for load in request.loads):
        notes.append(note("contactor_ac1"))
    notes.append(note("discrimination", ratio=_plain(ratio)))
    unchecked = sum(1 for load in request.loads if load.length_m is None)
    if unchecked:
        notes.append(note("voltage_drop_unchecked", count=unchecked))
    notes.extend(_earth_fault_notes(request, sized))
    if request.supply.fault_level_ka is not None:
        notes.append(note("short_circuit_withstand", fault=_plain(request.supply.fault_level_ka)))
    conditions = request.conditions
    notes.append(
        note(
            "cable_conditions",
            method=conditions.installation_method.value,
            ambient=_plain(conditions.ambient_temp_c),
            grouped=conditions.grouped_circuits,
            insulation=conditions.insulation_rating_c,
        )
    )
    if not profile.rules_confirmed_by:
        notes.append(note("rules_unconfirmed"))
    return Board(
        id=request.name,
        name=request.name,
        location=request.location,
        supply=request.supply,
        incomer_ids=["incomer"],
        devices=devices,
        cables=cables,
        circuits=circuits,
        notes=notes,
        fed_from=request.fed_from,
    )
