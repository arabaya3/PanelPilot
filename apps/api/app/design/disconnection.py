"""Automatic disconnection on an earth fault, for a circuit's breaker and cable.

IEC 60364-4-41 §411.4.4 asks, in a TN system, that the earth fault loop
impedance ``Zs`` let enough current flow to trip the circuit's protective
device in time: ``Zs x Ia <= U0``. Here:

* ``Ia`` is the top of a miniature breaker's instantaneous band (IEC 60898-1:
  5 In for curve B, 10 In for C, 20 In for D), which trips within 0.1 s. That
  meets the 0.4 s of Table 41.1 for a final circuit and, with room to spare,
  the 5 s of §411.3.2.3 for a distribution circuit; reading a breaker's time
  curve for 5 s would allow a longer feeder, but no curve is held here.
  A motor starter's breaker is magnetic only, and its ``Ia`` is the
  threshold ``I3`` its coordination table prints, raised by the 20 % an
  instantaneous release may trip above its setting (IEC 60947-2 §8.3.3.1.2).
* ``U0`` is reduced by ``Cmin = 0.95`` for the supply's lowest voltage
  (CENELEC TR 50480; BS 7671 Table 41.3 is drawn up the same way).
* ``Zs`` is the impedance outside the board, ``Ze``, plus the circuit's own
  loop out along a line conductor and back along its protective conductor.
  The cables here are multicore with every core the section of the line
  conductors, so the protective conductor is that section too (no smaller
  than IEC 60364-5-54 Table 54.3 asks). Each conductor's resistance is taken
  at its insulation's maximum operating temperature, 70 °C for PVC and 90 °C
  for XLPE, from its resistivity and temperature coefficient at 20 °C
  (IEC 60287-1-1 Table 1). The loop's reactance is the cable's 0.08 mΩ/m a
  conductor, as ``fault_level`` takes it. ``Ze`` and the loop are added as
  magnitudes, which errs towards a larger ``Zs``.

A cable whose loop is too long is stepped up through the tabulated sections
until it is not; where none is, the board says a residual current device is
needed. A circuit under a residual current device disconnects by it
(§411.4.5), and in a TT system only that can disconnect it (§411.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.design.fault_level import REACTANCE_PER_M
from app.design.voltage_drop import SECTIONS
from app.models.schemas.calculations import ConductorMaterial

#: The factor for the supply's lowest voltage (CENELEC TR 50480).
C_MIN = Decimal("0.95")

#: The current that trips a miniature breaker instantaneously, as a multiple
#: of In: the top of each curve's band (IEC 60898-1 Table 7).
INSTANTANEOUS_MULTIPLE: dict[str, Decimal] = {
    "B": Decimal(5),
    "C": Decimal(10),
    "D": Decimal(20),
}

#: Resistivity at 20 °C, Ω mm²/m, and its temperature coefficient, per K
#: (IEC 60287-1-1 Table 1).
RESISTIVITY_20: dict[ConductorMaterial, Decimal] = {
    ConductorMaterial.COPPER: Decimal("0.017241"),
    ConductorMaterial.ALUMINIUM: Decimal("0.028264"),
}
TEMPERATURE_COEFFICIENT: dict[ConductorMaterial, Decimal] = {
    ConductorMaterial.COPPER: Decimal("0.00393"),
    ConductorMaterial.ALUMINIUM: Decimal("0.00403"),
}

#: How far above its setting an instantaneous release may trip (IEC 60947-2
#: §8.3.3.1.2, ±20 %).
MAGNETIC_TOLERANCE = Decimal("1.2")

_THOUSANDTH = Decimal("0.001")


def max_loop_ohm(phase_voltage_v: Decimal, rated_a: Decimal, curve: str) -> Decimal | None:
    """The largest earth fault loop impedance a breaker still trips on at once.

    Args:
        phase_voltage_v: ``U0``, line to earth.
        rated_a: The breaker's In.
        curve: Its tripping characteristic.

    Returns:
        ``Cmin U0 / Ia`` in ohms; ``None`` for a curve not held.
    """
    multiple = INSTANTANEOUS_MULTIPLE.get(curve.upper())
    if multiple is None:
        return None
    return C_MIN * phase_voltage_v / (multiple * rated_a)


def loop_ohm(
    length_m: Decimal,
    section_mm2: Decimal,
    material: ConductorMaterial,
    insulation_rating_c: int,
) -> Decimal:
    """The impedance of a cable's loop out on a line and back on its protective conductor.

    Args:
        length_m: The route length, one way.
        section_mm2: Of the line and protective conductors alike.
        material: Copper or aluminium.
        insulation_rating_c: The insulation's maximum operating temperature,
            which the conductors are taken at.

    Returns:
        ``|R1 + R2 + jX|`` in ohms.
    """
    heating = 1 + TEMPERATURE_COEFFICIENT[material] * (insulation_rating_c - 20)
    resistance = 2 * RESISTIVITY_20[material] * heating * length_m / section_mm2
    reactance = 2 * REACTANCE_PER_M * length_m
    return (resistance**2 + reactance**2).sqrt()


@dataclass(frozen=True)
class Checked:
    """A cable after its earth fault loop is checked.

    Attributes:
        section_mm2: Its section, enlarged where the loop needed it.
        loop_ohm: ``Zs`` at its far end, to a thousandth of an ohm.
        max_ohm: The most ``Zs`` may be, to a thousandth of an ohm.
        within: Whether ``Zs`` is at most that.
    """

    section_mm2: Decimal
    loop_ohm: Decimal
    max_ohm: Decimal
    within: bool


def fit(
    *,
    external_ohm: Decimal,
    length_m: Decimal,
    section_mm2: Decimal,
    material: ConductorMaterial,
    insulation_rating_c: int,
    phase_voltage_v: Decimal,
    rated_a: Decimal,
    curve: str,
    parallel: int = 1,
    magnetic_trip_a: Decimal | None = None,
) -> Checked | None:
    """Enlarge a cable until its breaker disconnects an earth fault at its far end.

    Args:
        external_ohm: ``Ze`` at the board.
        length_m: The cable's route length.
        section_mm2: Its section as sized so far.
        material: Copper or aluminium.
        insulation_rating_c: 70 or 90.
        phase_voltage_v: ``U0``.
        rated_a: The breaker's In.
        curve: Its tripping characteristic.
        parallel: Identical cables run in parallel, each with its protective
            conductor; their loops share the fault, so the circuit's is one
            run's divided by their number.
        magnetic_trip_a: A magnetic-only breaker's threshold ``I3``; when
            given, ``Ia`` is it with its tolerance, and ``curve`` is not read.

    Returns:
        The smallest section at or above ``section_mm2`` that is within; or,
        where none is, ``section_mm2`` itself with ``within`` false. ``None``
        for a curve not held or a section beyond the tables.
    """
    if magnetic_trip_a is not None:
        limit: Decimal | None = C_MIN * phase_voltage_v / (MAGNETIC_TOLERANCE * magnetic_trip_a)
    else:
        limit = max_loop_ohm(phase_voltage_v, rated_a, curve)
    if limit is None or section_mm2 not in SECTIONS:
        return None

    def zs(section: Decimal) -> Decimal:
        return external_ohm + loop_ohm(length_m, section, material, insulation_rating_c) / parallel

    for candidate in SECTIONS[SECTIONS.index(section_mm2) :]:
        impedance = zs(candidate)
        if impedance <= limit:
            return Checked(
                candidate,
                impedance.quantize(_THOUSANDTH),
                limit.quantize(_THOUSANDTH),
                within=True,
            )
    return Checked(
        section_mm2,
        zs(section_mm2).quantize(_THOUSANDTH),
        limit.quantize(_THOUSANDTH),
        within=False,
    )
