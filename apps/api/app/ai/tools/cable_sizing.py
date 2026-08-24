"""Cable sizing calculations.

Pure functions: no I/O, no database, no settings. Every formula names the
manufacturer guide or standard clause it came from in its docstring, so a
reviewer can check the arithmetic against the paper source without reading the
call site.
"""

from __future__ import annotations

from decimal import Decimal

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
        IEC 60364-5-52:2009, Annex B, Tables B.52.2–B.52.5 (base ampacity by
        installation method), as reproduced in the Prysmian Wire & Cable
        Engineering Handbook, 5th ed., §4.2.

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


def voltage_drop(
    *,
    current_a: Decimal,
    length_m: Decimal,
    cross_section_mm2: Decimal,
    conductor_material: ConductorMaterial,
    power_factor: Decimal,
    three_phase: bool,
) -> Decimal:
    """Compute the line voltage drop over a cable run, in volts.

    Uses the tabulated mV/A/m method rather than a resistivity calculation, so
    reactance is included for larger cross-sections.

    Source:
        IEC 60364-5-52:2009, Annex G (voltage-drop tables); worked example
        follows Schneider Electric Electrical Installation Guide 2024, §G.6.

    Args:
        current_a: Load current, in amperes.
        length_m: One-way run length, in metres.
        cross_section_mm2: Conductor cross-sectional area, in mm².
        conductor_material: Copper or aluminium.
        power_factor: Load power factor, 0 < pf ≤ 1.
        three_phase: ``True`` for three-phase, ``False`` for single-phase.

    Returns:
        The voltage drop in volts.

    Raises:
        ValidationError: If ``cross_section_mm2`` is not a tabulated size or
            ``power_factor`` is outside (0, 1].
    """
    raise NotImplementedError


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
