"""The prospective short-circuit current at the end of a feeder.

IEC 60909-0's method for a three-phase fault, as Schneider's Electrical
Installation Guide applies it to a feeder (Chapter G, §4):

* The supply's impedance comes from the fault level at the board that feeds
  the cable: ``Zs = c Un / (√3 Ik)``. It is taken as pure reactance, which
  gives the highest current at the far end for a given ``|Zs|``; with the
  voltage factor ``cmax = 1.10`` (IEC 60909-0 Table 1, LV).
* The cable adds ``R = rho L / S`` at 20 °C, the conductor's coldest and so the
  highest-current case (rho = 1/56 Ω mm²/m copper, 1/34 aluminium, IEC 60909-0
  §6.2), and ``X = 0.08 mΩ/m``, the guide's value for a cable.
* ``Ik = c Un / (√3 |Zs + Zc|)``.

Each choice errs towards a higher current, which rates breakers for more
than they will see rather than less.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from app.models.schemas.calculations import ConductorMaterial

#: IEC 60909-0 voltage factor for the maximum current in a LV system.
C_MAX = Decimal("1.10")

#: Conductor resistivity at 20 °C, Ω mm²/m (IEC 60909-0 §6.2).
RESISTIVITY: dict[ConductorMaterial, Decimal] = {
    ConductorMaterial.COPPER: Decimal(1) / Decimal(56),
    ConductorMaterial.ALUMINIUM: Decimal(1) / Decimal(34),
}

#: Reactance of a cable, Ω/m (Schneider EIG Chapter G, §4.2).
REACTANCE_PER_M = Decimal("0.00008")

_SQRT3 = Decimal(3).sqrt()
_TENTH = Decimal("0.1")


def at_feeder_end(
    *,
    upstream_ka: Decimal,
    voltage_v: Decimal,
    length_m: Decimal,
    section_mm2: Decimal,
    material: ConductorMaterial,
    parallel: int = 1,
) -> Decimal:
    """The three-phase fault level where a feeder ends.

    Args:
        upstream_ka: The fault level at the board the feeder leaves.
        voltage_v: The line voltage.
        length_m: The feeder's route length.
        section_mm2: Its line conductors' cross-section.
        material: Copper or aluminium.
        parallel: Identical cables in parallel, which divide both the cable's
            resistance and its reactance.

    Returns:
        The fault level at the far end in kA, rounded up to 0.1 kA, and never
        above ``upstream_ka``.
    """
    source = C_MAX * voltage_v / (_SQRT3 * upstream_ka * 1000)
    resistance = RESISTIVITY[material] * length_m / section_mm2 / parallel
    reactance = source + REACTANCE_PER_M * length_m / parallel
    impedance = (resistance**2 + reactance**2).sqrt()
    current = C_MAX * voltage_v / (_SQRT3 * impedance) / 1000
    return min(upstream_ka, current.quantize(_TENTH, rounding=ROUND_CEILING))
