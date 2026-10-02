"""Tests for `app/design/fault_level.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.design import fault_level
from app.models.schemas.calculations import ConductorMaterial


def _at(
    length: str, section: str, material: ConductorMaterial = ConductorMaterial.COPPER
) -> Decimal:
    return fault_level.at_feeder_end(
        upstream_ka=Decimal(25),
        voltage_v=Decimal(400),
        length_m=Decimal(length),
        section_mm2=Decimal(section),
        material=material,
    )


def test_at_feeder_end_by_hand() -> None:
    # Zs = 1.1 x 400 / (√3 x 25 kA) = 10.16 mΩ, reactive.
    # 60 m of 16 mm² Cu: R = 60 / (56 x 16) = 66.96 mΩ, X = 4.8 mΩ.
    # |Z| = √(66.96² + 14.96²) = 68.61 mΩ; Ik = 254 V / 68.61 mΩ = 3.70 kA.
    assert _at("60", "16") == Decimal("3.8")


def test_a_longer_or_thinner_feeder_lets_through_less() -> None:
    assert _at("150", "16") < _at("60", "16") < _at("60", "50")
    assert _at("60", "50", ConductorMaterial.ALUMINIUM) < _at("60", "50")


@pytest.mark.parametrize("length", ["0.01", "1"])
def test_it_never_exceeds_the_supply(length: str) -> None:
    assert _at(length, "300") <= 25
