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


@pytest.mark.parametrize(
    ("kw", "volts", "pf", "three", "amps"),
    [
        # The handbook's Annex B Table 1, cos phi 0.9: 10 kW at 400 V is 16.04 A.
        ("10", "400", "0.9", True, "16.04"),
        # Its Example 4 (p. 69): 25 kW single-phase at 230 V, cos phi 0.9, 121 A.
        ("25", "230", "0.9", False, "120.77"),
        ("1", "230", "1", False, "4.35"),
    ],
)
def test_load_current(kw: str, volts: str, pf: str, three: bool, amps: str) -> None:
    assert feeder_protection.load_current(
        power_kw=Decimal(kw),
        voltage_v=Decimal(volts),
        power_factor=Decimal(pf),
        three_phase=three,
    ) == Decimal(amps)


@pytest.mark.parametrize(
    ("kw", "volts", "pf"),
    [("0", "230", "0.9"), ("1", "0", "0.9"), ("1", "230", "0"), ("1", "230", "1.1")],
)
def test_load_current_refuses_nonsense(kw: str, volts: str, pf: str) -> None:
    with pytest.raises(ValidationError):
        feeder_protection.load_current(
            power_kw=Decimal(kw),
            voltage_v=Decimal(volts),
            power_factor=Decimal(pf),
            three_phase=False,
        )


def test_smallest_rating() -> None:
    assert feeder_protection.smallest_rating(Decimal("10.5")) == "13"
    assert feeder_protection.smallest_rating(Decimal(125)) == "125"
    with pytest.raises(ValidationError, match="largest curve C rating"):
        feeder_protection.smallest_rating(Decimal(126))
    with pytest.raises(ValidationError, match="positive"):
        feeder_protection.smallest_rating(Decimal(0))


def test_load_current_citation() -> None:
    assert feeder_protection.load_current_citation().page == 245
