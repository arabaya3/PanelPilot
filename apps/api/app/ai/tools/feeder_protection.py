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
