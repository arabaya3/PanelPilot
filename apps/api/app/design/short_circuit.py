"""A cable's protection against short circuit along its whole length.

ABB's handbook (Vol. 2, §2.4) asks that the let-through energy of the
protective device not exceed what the cable withstands, ``I²t <= k²S²``, for
every short circuit the cable can see, and simplifies the check to its two
ends:

* At the far end, the smallest short circuit must still trip the breaker
  instantaneously. Its current is the handbook's approximation (2.2), for a
  distributed neutral of the line conductors' section (``m = 1``):
  ``Ikmin = 0.8 U0 ksec / (1.5 rho 2 L / S)``, rho at 20 °C (0.018 copper,
  0.027 aluminium), ``ksec`` for the reactance of sections above 95 mm². It
  must be at least the top of the breaker's instantaneous band (``Ia``,
  IEC 60898-1, as ``disconnection`` takes it). A cable too long for that is
  stepped up through the tabulated sections. Unlike the earth fault check,
  this holds under a residual current device too, which does not see a
  fault between line and neutral.
* At the near end, the breaker's let-through energy at the board's fault
  level must not exceed the cable's ``k²S²``. ``k`` is from the handbook's
  Table 1 (115 copper and 76 aluminium in PVC, 103 and 68 above 300 mm²;
  143 and 94 in XLPE). Conductors in parallel are each checked alone, and
  their far-end short circuit is raised by the handbook's ``kpar``. A breaker's let-through energy is read from its maker's curve,
  which is not held until a part is selected, so each cable's ``k²S²`` is
  given for that comparison and the board says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.design.disconnection import INSTANTANEOUS_MULTIPLE
from app.design.voltage_drop import SECTIONS
from app.models.schemas.calculations import ConductorMaterial

#: ``k`` for a conductor up to 300 mm², by insulation rating (°C) and material
#: (ABB handbook Vol. 2, §2.4 Table 1).
K_FACTOR: dict[tuple[int, ConductorMaterial], Decimal] = {
    (70, ConductorMaterial.COPPER): Decimal(115),
    (70, ConductorMaterial.ALUMINIUM): Decimal(76),
    (90, ConductorMaterial.COPPER): Decimal(143),
    (90, ConductorMaterial.ALUMINIUM): Decimal(94),
}

#: Resistivity at 20 °C for the minimum short circuit, Ω mm²/m (handbook §2.4).
RESISTIVITY: dict[ConductorMaterial, Decimal] = {
    ConductorMaterial.COPPER: Decimal("0.018"),
    ConductorMaterial.ALUMINIUM: Decimal("0.027"),
}

#: ``kpar`` for conductors in parallel per phase (handbook §2.4); 1 for one.
PARALLEL_FACTOR: dict[int, Decimal] = {
    1: Decimal(1),
    2: Decimal(2),
    3: Decimal("2.7"),
    4: Decimal(3),
    5: Decimal("3.2"),
}

#: ``k`` for a PVC-insulated conductor above 300 mm² (handbook §2.4 Table 1).
_K_PVC_ABOVE_300: dict[ConductorMaterial, Decimal] = {
    ConductorMaterial.COPPER: Decimal(103),
    ConductorMaterial.ALUMINIUM: Decimal(68),
}

#: ``ksec`` for the reactance of a large section (handbook §2.4); 1 up to 95 mm².
REACTANCE_FACTOR: dict[Decimal, Decimal] = {
    Decimal(120): Decimal("0.9"),
    Decimal(150): Decimal("0.85"),
    Decimal(185): Decimal("0.80"),
    Decimal(240): Decimal("0.75"),
    Decimal(300): Decimal("0.72"),
}


def withstand_ka2s(
    section_mm2: Decimal, material: ConductorMaterial, insulation_rating_c: int
) -> Decimal | None:
    """The let-through energy a cable withstands, ``k²S²``.

    Args:
        section_mm2: Its cross-section.
        material: Copper or aluminium.
        insulation_rating_c: 70 (PVC) or 90 (XLPE/EPR).

    Returns:
        ``k²S²`` in (kA)²s, to a thousandth, of one conductor (in parallel,
        each is checked alone); ``None`` for an insulation not held.
    """
    k = K_FACTOR.get((insulation_rating_c, material))
    if k is None:
        return None
    if insulation_rating_c == 70 and section_mm2 > 300:
        k = _K_PVC_ABOVE_300[material]
    return ((k * section_mm2) ** 2 / 1_000_000).quantize(Decimal("0.001"))


def min_current_a(
    length_m: Decimal,
    section_mm2: Decimal,
    material: ConductorMaterial,
    phase_voltage_v: Decimal,
    parallel: int = 1,
) -> Decimal:
    """The smallest short circuit at a cable's far end, line to neutral.

    Args:
        length_m: The route length.
        section_mm2: Of the line and neutral conductors alike.
        material: Copper or aluminium.
        phase_voltage_v: ``U0``.
        parallel: Conductors in parallel per phase, each of ``section_mm2``.

    Returns:
        ``Ikmin`` in amperes (handbook formula 2.2, ``m = 1``).
    """
    ksec = REACTANCE_FACTOR.get(section_mm2, Decimal(1))
    kpar = PARALLEL_FACTOR[parallel]
    return (
        Decimal("0.8")
        * phase_voltage_v
        * ksec
        * kpar
        / (Decimal("1.5") * RESISTIVITY[material] * 2 * length_m / section_mm2)
    )


@dataclass(frozen=True)
class Checked:
    """A cable after its far-end short circuit is checked.

    Attributes:
        section_mm2: Its section, enlarged where the check needed it.
        min_current_a: ``Ikmin`` at its far end, to the ampere.
        trip_a: The current that trips its breaker at once.
        within: Whether ``Ikmin`` reaches it.
    """

    section_mm2: Decimal
    min_current_a: Decimal
    trip_a: Decimal
    within: bool


def fit(
    *,
    length_m: Decimal,
    section_mm2: Decimal,
    material: ConductorMaterial,
    phase_voltage_v: Decimal,
    rated_a: Decimal,
    curve: str,
    parallel: int = 1,
) -> Checked | None:
    """Enlarge a cable until a short circuit at its far end trips its breaker at once.

    Args:
        length_m: The route length.
        section_mm2: Its section as sized so far.
        material: Copper or aluminium.
        phase_voltage_v: ``U0``.
        rated_a: The breaker's In.
        curve: Its tripping characteristic.
        parallel: Conductors in parallel per phase (``kpar``).

    Returns:
        The smallest section at or above ``section_mm2`` that is within; or,
        where none is, ``section_mm2`` itself with ``within`` false. ``None``
        for a curve not held or a section beyond the tables.
    """
    multiple = INSTANTANEOUS_MULTIPLE.get(curve.upper())
    if multiple is None or section_mm2 not in SECTIONS:
        return None
    trip = multiple * rated_a
    for candidate in SECTIONS[SECTIONS.index(section_mm2) :]:
        current = min_current_a(length_m, candidate, material, phase_voltage_v, parallel)
        if current >= trip:
            return Checked(candidate, current.quantize(Decimal(1)), trip, within=True)
    current = min_current_a(length_m, section_mm2, material, phase_voltage_v, parallel)
    return Checked(section_mm2, current.quantize(Decimal(1)), trip, within=False)
