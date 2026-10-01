"""Tests for `app/ai/tools/feeder_protection.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import feeder_protection
from app.core.errors import ValidationError


@pytest.mark.parametrize(
    ("ib", "iz", "rated"),
    [
        # The smallest rating at or above Ib that the cable still carries.
        ("10", "22", "10"),
        ("10.5", "22", "13"),
        ("40", "52", "40"),
        # Ib equal to Iz is allowed: Ib <= In <= Iz holds at In = Ib.
        ("63", "63", "63"),
        ("120", "130", "125"),
    ],
)
def test_ib_le_in_le_iz(ib: str, iz: str, rated: str) -> None:
    breaker = feeder_protection.select_feeder_breaker(
        design_current_a=Decimal(ib), cable_ampacity_a=Decimal(iz)
    )
    assert breaker.rated_current_a == rated
    assert breaker.curve == "C"
    assert breaker.source.page == 70


@pytest.mark.parametrize(
    ("ib", "iz", "message"),
    [
        # 41 A needs 50 A, which a 45 A cable cannot be protected by.
        ("41", "45", "exceeds the cable"),
        ("126", "200", "largest curve C rating"),
        ("0", "10", "positive"),
        ("10", "NaN", "positive"),
    ],
)
def test_what_the_rule_does_not_allow_is_refused(ib: str, iz: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        feeder_protection.select_feeder_breaker(
            design_current_a=Decimal(ib), cable_ampacity_a=Decimal(iz)
        )


def test_the_ratings_rise() -> None:
    ratings = [Decimal(r) for r in feeder_protection._CURVE_C_RATINGS]
    assert ratings == sorted(ratings)
