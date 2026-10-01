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
  the smallest preferred value at or above it. Discrimination between the
  group and outgoing breakers is not checked, and the board says so.
* Single-phase loads are spread over L1-L3 largest first onto the least
  loaded conductor. The resulting imbalance is checked against the profile.
* The incomer is rated for the most loaded line conductor with no diversity
  applied; a company's demand factors are not assumed.

No part is selected here: devices carry ratings, not articles, until a
catalogue is chosen, and the parts list says so rather than naming one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from app.ai.tools import cable_sizing, feeder_protection
from app.core.errors import ValidationError
from app.models.schemas.calculations import ConductorMaterial
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    CompanyProfile,
    Device,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    Phase,
)

#: The power factor assumed when a load gives none: the handbook's Annex B
#: load-current table is drawn up for cosφ = 0.9.
DEFAULT_POWER_FACTOR = Decimal("0.9")

#: Preferred rated currents of residual current circuit-breakers, IEC 61008-1.
RCCB_RATINGS: tuple[Decimal, ...] = tuple(
    Decimal(r) for r in ("16", "25", "40", "63", "80", "100", "125")
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


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


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
    notes: list[str],
) -> _Sized:
    three_phase = load.phases == 3
    if three_phase and request.supply.phases == 1:
        raise ValidationError(f"{load.description}: a three-phase load on a single-phase supply")
    power_factor = load.power_factor or DEFAULT_POWER_FACTOR
    current = feeder_protection.load_current(
        power_kw=load.power_kw,
        voltage_v=request.supply.voltage_v if three_phase else _phase_voltage(request),
        power_factor=power_factor,
        three_phase=three_phase,
    )
    rule = profile.circuit_rules.get(load.load)
    fixed = rule.breaker_a if rule else None
    if fixed is not None and fixed >= current:
        rated = fixed
    else:
        try:
            rated = Decimal(feeder_protection.smallest_rating(current))
        except ValidationError as exc:
            raise ValidationError(f"{load.description}: {exc}") from exc
        if fixed is not None:
            notes.append(
                f"{load.description}: Ib {_plain(current)} A exceeds the company's "
                f"{_plain(fixed)} A for {load.load.value}; rated {_plain(rated)} A instead."
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
        raise ValidationError(f"{load.description}: {exc}") from exc
    section = cable.cross_section_mm2
    if rule and rule.cable_mm2 is not None and rule.cable_mm2 > section:
        section = rule.cable_mm2
    return _Sized(
        load=load,
        index=index,
        current_a=current,
        rated_a=rated,
        curve=rule.curve if rule else "C",
        section_mm2=section,
    )


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


def _rccb_rating(current: Decimal) -> Decimal:
    for rating in RCCB_RATINGS:
        if rating >= current:
            return rating
    raise ValidationError(
        f"a residual current group carrying {_plain(current)} A exceeds the largest "
        f"preferred RCCB rating held ({_plain(RCCB_RATINGS[-1])} A)"
    )


def design_distribution_board(request: DistributionBoardRequest, profile: CompanyProfile) -> Board:
    """Design a distribution board's protection and cables from its load schedule.

    Args:
        request: The board's name, supply, load schedule and cable conditions.
        profile: The company whose rules apply.

    Returns:
        The board, undesignated: designations are assigned when it is issued
        under a profile (``designations.designate_board``).

    Raises:
        ValidationError: If a load cannot be protected or cabled from the
            tables held, naming the load.
    """
    notes: list[str] = []
    sized = [_size(request, profile, load, i, notes) for i, load in enumerate(request.loads)]
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
                group_rated = Decimal(feeder_protection.smallest_rating(loaded))
            except ValidationError as exc:
                raise ValidationError(f"residual current group {group_number}: {exc}") from exc
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
        breaker = Device(
            id=f"c{item.index + 1}-breaker",
            kind=DeviceKind.CIRCUIT_BREAKER,
            poles=3 if three_phase else 1,
            rated_current_a=item.rated_a,
            curve=item.curve,
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
        )
        devices.append(breaker)
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
                device_ids=[breaker.id],
                cable_id=cable.id,
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
                f"Phase imbalance {_plain(imbalance.quantize(Decimal('0.1')))} % exceeds the "
                f"company's {_plain(profile.max_phase_imbalance_percent)} %: the loads cannot be "
                "spread more evenly as given."
            )
    try:
        incomer_rated = Decimal(feeder_protection.smallest_rating(most))
    except ValidationError:
        incomer_rated = None
        notes.append(
            f"Incomer: {_plain(most)} A on the most loaded conductor exceeds 125 A; a moulded-case "
            "breaker is needed and none is selected here."
        )
    incomer = Device(
        id="incomer",
        kind=DeviceKind.CIRCUIT_BREAKER,
        poles=2 if single_phase_supply else 4,
        rated_current_a=incomer_rated,
        curve="C" if incomer_rated is not None else None,
        breaking_capacity_ka=request.supply.fault_level_ka,
        description="Main incomer",
    )
    devices.insert(0, incomer)

    spare = (len(circuits) * profile.spare_ways_percent / 100).to_integral_value(ROUND_CEILING)
    notes.append(
        f"Leave {_plain(spare)} spare outgoing ways ({_plain(profile.spare_ways_percent)} %)."
    )
    if any(s.load.power_factor is None for s in sized):
        notes.append("Loads without a power factor were taken at cos phi 0.9.")
    if groups:
        notes.append(
            "Discrimination between each group breaker and its outgoing breakers is not checked."
        )
    if request.supply.fault_level_ka is None:
        notes.append(
            "No fault level given: every breaker's breaking capacity is left to be confirmed."
        )
    conditions = request.conditions
    notes.append(
        f"Cables sized for method {conditions.installation_method.value}, "
        f"{_plain(conditions.ambient_temp_c)} °C, {conditions.grouped_circuits} grouped circuit(s), "
        f"{conditions.insulation_rating_c} °C insulation."
    )
    if not profile.rules_confirmed_by:
        notes.append(
            "Circuit rules are this software's defaults, not confirmed by the company's engineers."
        )
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
    )
