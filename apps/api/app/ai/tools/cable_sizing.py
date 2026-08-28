"""Cable sizing calculations.

Pure functions: no I/O, no database, no settings. Every formula names the
manufacturer guide or standard clause it came from in its docstring, so a
reviewer can check the arithmetic against the paper source without reading the
call site.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from app.core.errors import ValidationError
from app.models.schemas.calculations import (
    CableSizingResult,
    ConductorMaterial,
    InstallationMethod,
)


def size_conductor(
    *,
    design_current_a: Decimal,
    installation_method: InstallationMethod,
    ambient_temp_c: Decimal,
    grouped_circuits: int,
    conductor_material: ConductorMaterial,
    insulation_rating_c: int,
) -> CableSizingResult:
    """Select the smallest conductor whose derated ampacity carries the load.

    Applies ambient and grouping derating factors to the base ampacity, then
    picks the first standard size at or above the design current.

    Source:
        Schneider Electric, *Electrical Installation Guide* 2010, Chapter G
        ("Sizing and protection of conductors"), Fig. G21a — current-carrying
        capacity in amperes, reproducing table B.52-1 of IEC 60364-5-52.
        Ambient correction from Fig. G12 (table A.52-14); grouping reduction
        from Fig. G16 (table A.52-17).

        The guide rather than the standard, per the licensing constraint in
        this task's spec: the EIG is a manufacturer engineering guide that
        republishes the IEC tables under Schneider's own publication.

    Args:
        design_current_a: Design current of the circuit, in amperes.
        installation_method: Reference installation method (A1–G).
        ambient_temp_c: Ambient temperature at the run, in degrees Celsius.
        grouped_circuits: Number of loaded circuits in the same grouping.
        conductor_material: Copper or aluminium.
        insulation_rating_c: Conductor temperature rating, 70 or 90 °C.

    Returns:
        The selected size with each derating factor applied and its source.

    Raises:
        ValidationError: If no tabulated size carries the derated current, or
            if an argument falls outside the table's range.
    """
    raise NotImplementedError


#: Phase-to-phase voltage drop, in volts per ampere per kilometre.
#:
#: Schneider Electric, *Electrical Installation Guide* 2010, Fig. G28. Keyed by
#: copper cross-section; the guide's aluminium column is offset (its first row
#: is 6 mm² Cu / 10 mm² Al) and is not reproduced here, because nothing yet
#: calls this for aluminium and a mis-transcribed offset is exactly the kind of
#: silent error this table must not carry.
#:
#: Each tuple is (single-phase motor cos 0.8, single-phase motor cos 0.35,
#: single-phase lighting, three-phase motor cos 0.8, three-phase motor
#: cos 0.35, three-phase lighting), matching the guide's column order.
_VOLTAGE_DROP_MV_PER_A_KM: dict[Decimal, tuple[str, str, str, str, str, str]] = {
    Decimal("1.5"): ("24", "10.6", "30", "20", "9.4", "25"),
    Decimal("2.5"): ("14.4", "6.4", "18", "12", "5.7", "15"),
    Decimal("4"): ("9.1", "4.1", "11.2", "8", "3.6", "9.5"),
    Decimal("6"): ("6.1", "2.9", "7.5", "5.3", "2.5", "6.2"),
    Decimal("10"): ("3.7", "1.7", "4.5", "3.2", "1.5", "3.6"),
    Decimal("16"): ("2.36", "1.15", "2.8", "2.05", "1", "2.4"),
    Decimal("25"): ("1.5", "0.75", "1.8", "1.3", "0.65", "1.5"),
    Decimal("35"): ("1.15", "0.6", "1.29", "1", "0.52", "1.1"),
    Decimal("50"): ("0.86", "0.47", "0.95", "0.75", "0.41", "0.77"),
    Decimal("70"): ("0.64", "0.37", "0.64", "0.56", "0.32", "0.55"),
    Decimal("95"): ("0.48", "0.30", "0.47", "0.42", "0.26", "0.4"),
    Decimal("120"): ("0.39", "0.26", "0.37", "0.34", "0.23", "0.31"),
    Decimal("150"): ("0.33", "0.24", "0.30", "0.29", "0.21", "0.27"),
    Decimal("185"): ("0.29", "0.22", "0.24", "0.25", "0.19", "0.2"),
    Decimal("240"): ("0.24", "0.2", "0.19", "0.21", "0.17", "0.16"),
    Decimal("300"): ("0.21", "0.19", "0.15", "0.18", "0.16", "0.13"),
}

#: The power factor the guide's "normal service" motor columns are tabulated at.
_COS_PHI_NORMAL = Decimal("0.8")

#: The power factor its "start-up" motor columns are tabulated at.
_COS_PHI_STARTUP = Decimal("0.35")


class LoadType(StrEnum):
    """Which of Fig. G28's column pairs applies.

    The guide tabulates motor and lighting circuits separately at the same
    cross-section, and they differ — a 70 mm² three-phase run is 0.56 V/A/km
    for a motor and 0.55 for lighting. Its own Example 2 uses the lighting
    column for a line supplying lighting circuits, so this is a real
    distinction the caller must make rather than a nuance to average away.
    """

    MOTOR = "motor"
    LIGHTING = "lighting"


def voltage_drop(
    *,
    current_a: Decimal,
    length_m: Decimal,
    cross_section_mm2: Decimal,
    conductor_material: ConductorMaterial,
    power_factor: Decimal,
    three_phase: bool,
    load_type: LoadType = LoadType.MOTOR,
) -> Decimal:
    """Compute the line voltage drop over a cable run, in volts.

    Uses the tabulated V/A/km method rather than a resistivity calculation, so
    reactance is included for larger cross-sections.

    Source:
        Schneider Electric, *Electrical Installation Guide* 2010, Chapter G,
        Fig. G28 ("Phase-to-phase voltage drop ΔU for a circuit, in volts per
        ampere per km"), with the method and worked examples from §3.

    Args:
        current_a: Load current, in amperes.
        length_m: One-way run length, in metres.
        cross_section_mm2: Conductor cross-sectional area, in mm².
        conductor_material: Copper or aluminium.
        power_factor: Load power factor. For a motor circuit this selects the
            guide's column and must be exactly 0.8 (normal service) or 0.35
            (start-up) — see below.
        three_phase: ``True`` for three-phase, ``False`` for single-phase.
        load_type: Motor or lighting. Fig. G28 tabulates these separately and
            they differ; the guide's Example 2 uses the lighting column.

    Returns:
        The phase-to-phase voltage drop in volts.

    Raises:
        ValidationError: If the cross-section is not tabulated, the conductor
            is not copper, or the power factor is not one the guide tabulates.

    **Interpolation is refused, not performed.** Fig. G28 is a table of
    measured values at two power factors, not a curve — the relationship
    between them includes reactance and is not linear in cos φ. Accepting
    cos φ = 0.6 and interpolating would return a confident number the guide
    does not support, for a circuit whose conductor sizing depends on it.
    A caller outside the table gets a refusal it can surface, which is the
    behaviour AI-005's spec asks for.
    """
    if conductor_material is not ConductorMaterial.COPPER:
        # The guide's aluminium column is offset against the copper one and is
        # not transcribed here. Refusing is honest; guessing the offset is the
        # error this whole sourcing discipline exists to prevent.
        raise ValidationError(
            "voltage drop is tabulated here for copper only; "
            f"{conductor_material.value} is not supported"
        )

    row = _VOLTAGE_DROP_MV_PER_A_KM.get(cross_section_mm2)
    if row is None:
        raise ValidationError(
            f"{cross_section_mm2} mm2 is not a cross-section tabulated in Fig. G28"
        )

    if load_type is LoadType.LIGHTING:
        index = 5 if three_phase else 2
    elif power_factor == _COS_PHI_NORMAL:
        index = 3 if three_phase else 0
    elif power_factor == _COS_PHI_STARTUP:
        index = 4 if three_phase else 1
    else:
        raise ValidationError(
            f"power factor {power_factor} is not tabulated; Fig. G28 gives motor "
            f"circuits at {_COS_PHI_NORMAL} (normal service) and "
            f"{_COS_PHI_STARTUP} (start-up) only, and does not support "
            "interpolation between them"
        )

    per_a_km = Decimal(row[index])
    # The guide's formula: drop = (V/A/km) x current x length in km.
    return per_a_km * current_a * (length_m / Decimal("1000"))


def derating_factor(
    *,
    ambient_temp_c: Decimal,
    grouped_circuits: int,
    insulation_rating_c: int,
) -> Decimal:
    """Return the combined ambient and grouping derating factor.

    Source:
        IEC 60364-5-52:2009, Tables B.52.14 (ambient, in air) and B.52.17
        (grouping).

    Args:
        ambient_temp_c: Ambient temperature, in degrees Celsius.
        grouped_circuits: Number of loaded circuits in the same grouping.
        insulation_rating_c: Conductor temperature rating, 70 or 90 °C.

    Returns:
        The product of both factors, in the range (0, 1].

    Raises:
        ValidationError: If the temperature or grouping count is off-table.
    """
    raise NotImplementedError
