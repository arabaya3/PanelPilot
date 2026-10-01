"""Overload protection for a plain feeder: a load with neither a drive nor a starter.

Pure functions. Each rule cites the manufacturer guide it came from.

ABB's *Electrical installation handbook* Vol. 2 (1SDC010001D0204), §2.3
"Protection against overload", applies IEC 60364-4-43: the protective
device's rated current In must satisfy Ib <= In <= Iz. For a circuit-breaker
to IEC 60898, I2 = 1.45 In, so In <= Iz also meets I2 <= 1.45 Iz without a
separate check. The rated currents offered are the curve C miniature
circuit-breakers of the handbook's Table 2.3, 3 to 125 A.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.search import Citation

HANDBOOK_ID = "abb-1SDC010001D0204"
HANDBOOK_TITLE = "Electrical installation handbook, Vol. 2: Electrical devices (4th ed., 2006)"

#: PDF page of §2.3 "Protection against overload" (p. 67 as printed).
_RULE_PAGE = 70

#: PDF page of Annex B "Calculation of load current Ib" (p. 242 as printed).
_LOAD_CURRENT_PAGE = 245

#: Curve C MCB rated currents, Table 2.3 (PDF p. 194, p. 191 as printed).
_CURVE_C_RATINGS: tuple[str, ...] = (
    "3", "4", "6", "8", "10", "13", "16", "20", "25", "32", "40", "50", "63", "80", "100", "125",
)  # fmt: skip


@dataclass(frozen=True)
class FeederBreaker:
    """The breaker chosen for a feeder.

    Attributes:
        rated_current_a: In.
        curve: The tripping curve.
        source: The rule it was chosen by.
    """

    rated_current_a: str
    curve: str
    source: Citation


def select_feeder_breaker(*, design_current_a: Decimal, cable_ampacity_a: Decimal) -> FeederBreaker:
    """Select the smallest curve C MCB with Ib <= In <= Iz.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        §2.3 "Protection against overload", conditions (1) and (2), p. 67 as
        printed; rated currents from Table 2.3 (curve C), p. 191 as printed.

    Args:
        design_current_a: Ib, the current the circuit is dimensioned for.
        cable_ampacity_a: Iz, the cable's capacity as installed.

    Returns:
        The breaker.

    Raises:
        ValidationError: If a current is not positive, Ib exceeds the largest
            curve C rating, or no rating fits between Ib and Iz.
    """
    for name, value in (
        ("design_current_a", design_current_a),
        ("cable_ampacity_a", cable_ampacity_a),
    ):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"{name} must be positive, got {value}")
    for rating in _CURVE_C_RATINGS:
        rated = Decimal(rating)
        if rated >= design_current_a:
            if rated > cable_ampacity_a:
                raise ValidationError(
                    f"no curve C rating lies between Ib {design_current_a} A and Iz "
                    f"{cable_ampacity_a} A: the next rating, {rating} A, exceeds the cable"
                )
            return FeederBreaker(
                rated_current_a=rating,
                curve="C",
                source=Citation(
                    document_id=HANDBOOK_ID,
                    document_title=HANDBOOK_TITLE,
                    manufacturer="ABB",
                    page=_RULE_PAGE,
                    section="2.3 Protection against overload: Ib <= In <= Iz",
                ),
            )
    raise ValidationError(
        f"{design_current_a} A exceeds the largest curve C rating held ({_CURVE_C_RATINGS[-1]} A)"
    )


def load_current(
    *, power_kw: Decimal, voltage_v: Decimal, power_factor: Decimal, three_phase: bool
) -> Decimal:
    """Return a load's design current Ib from its active power.

    Ib = P / (k Ur cosφ), k = 1 single-phase and √3 three-phase, with Ur the
    line voltage for a three-phase load and the phase voltage for a
    single-phase one.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        Annex B "Calculation of load current Ib", p. 242 as printed.

    Args:
        power_kw: Active power P.
        voltage_v: Ur: line voltage if three-phase, phase voltage if not.
        power_factor: cosφ, in (0, 1].
        three_phase: Whether the load is three-phase.

    Returns:
        Ib in amperes, to two decimals.

    Raises:
        ValidationError: If a value is out of range.
    """
    for name, value in (("power_kw", power_kw), ("voltage_v", voltage_v)):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"{name} must be positive, got {value}")
    if not power_factor.is_finite() or not 0 < power_factor <= 1:
        raise ValidationError(f"power_factor must be in (0, 1], got {power_factor}")
    k = Decimal(3).sqrt() if three_phase else Decimal(1)
    return (power_kw * 1000 / (k * voltage_v * power_factor)).quantize(Decimal("0.01"))


def smallest_rating(design_current_a: Decimal) -> str:
    """Return the smallest curve C MCB rated current at or above Ib.

    For a device that protects no cable of its own (a group breaker ahead of
    a residual current device, an incomer whose cable is not designed here),
    only Ib <= In applies; Iz is checked where the cable is.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        §2.3 condition (1), p. 67 as printed; rated currents from Table 2.3
        (curve C), p. 191 as printed.

    Args:
        design_current_a: Ib.

    Returns:
        The rated current, as Table 2.3 prints it.

    Raises:
        ValidationError: If Ib is not positive or exceeds 125 A.
    """
    if not design_current_a.is_finite() or design_current_a <= 0:
        raise ValidationError(f"design_current_a must be positive, got {design_current_a}")
    for rating in _CURVE_C_RATINGS:
        if Decimal(rating) >= design_current_a:
            return rating
    raise ValidationError(
        f"{design_current_a} A exceeds the largest curve C rating held ({_CURVE_C_RATINGS[-1]} A)"
    )


def load_current_citation() -> Citation:
    """Cite the load-current formula.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        Annex B, p. 242 as printed.

    Returns:
        The citation.
    """
    return Citation(
        document_id=HANDBOOK_ID,
        document_title=HANDBOOK_TITLE,
        manufacturer="ABB",
        page=_LOAD_CURRENT_PAGE,
        section="Annex B: Ib = P / (k Ur cos phi)",
    )
