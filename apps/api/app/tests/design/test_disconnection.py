"""Tests for `app/design/disconnection.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import disconnection
from app.models.schemas.calculations import ConductorMaterial


def test_max_loop_ohm_is_cmin_u0_over_the_instantaneous_trip() -> None:
    # C16 at 230 V: 0.95 x 230 / 160 A.
    assert disconnection.max_loop_ohm(Decimal(230), Decimal(16), "C") == Decimal("1.365625")
    assert disconnection.max_loop_ohm(Decimal(230), Decimal(16), "b") == Decimal("2.73125")
    assert disconnection.max_loop_ohm(Decimal(230), Decimal(16), "K") is None


def test_loop_ohm_is_out_and_back_at_operating_temperature() -> None:
    # 2.5 mm² copper at 70 °C: 0.017241 x 1.1965 x 2 x 10 m / 2.5 = 0.165 Ω.
    loop = disconnection.loop_ohm(Decimal(10), Decimal("2.5"), ConductorMaterial.COPPER, 70)
    assert loop.quantize(Decimal("0.001")) == Decimal("0.165")
    hotter = disconnection.loop_ohm(Decimal(10), Decimal("2.5"), ConductorMaterial.COPPER, 90)
    aluminium = disconnection.loop_ohm(Decimal(10), Decimal("2.5"), ConductorMaterial.ALUMINIUM, 70)
    assert loop < hotter < aluminium


def _fit(length: str, section: str = "2.5", ze: str = "0.35") -> disconnection.Checked | None:
    return disconnection.fit(
        external_ohm=Decimal(ze),
        length_m=Decimal(length),
        section_mm2=Decimal(section),
        material=ConductorMaterial.COPPER,
        insulation_rating_c=70,
        phase_voltage_v=Decimal(230),
        rated_a=Decimal(16),
        curve="C",
    )


def test_fit_keeps_a_short_cable() -> None:
    checked = _fit("20")
    assert checked is not None
    assert checked.within
    assert checked.section_mm2 == Decimal("2.5")
    assert checked.max_ohm == Decimal("1.366")
    assert checked.loop_ohm == Decimal("0.680")


def test_fit_enlarges_a_long_cable() -> None:
    checked = _fit("80")
    assert checked is not None
    assert checked.within
    assert checked.section_mm2 > Decimal("2.5")
    assert checked.loop_ohm <= checked.max_ohm


def test_fit_says_when_no_section_is_enough() -> None:
    checked = _fit("20", ze="1.5")
    assert checked is not None
    assert not checked.within
    assert checked.section_mm2 == Decimal("2.5")
    assert _fit("20", section="3") is None


def test_parallel_runs_divide_the_cable_loop() -> None:
    def zs(parallel: int) -> Decimal:
        checked = disconnection.fit(
            external_ohm=Decimal("0.1"),
            length_m=Decimal(100),
            section_mm2=Decimal(240),
            material=ConductorMaterial.COPPER,
            insulation_rating_c=70,
            phase_voltage_v=Decimal(230),
            rated_a=Decimal(16),
            curve="C",
            parallel=parallel,
        )
        assert checked is not None
        return checked.loop_ohm - Decimal("0.1")

    assert abs(zs(2) - zs(1) / 2) <= Decimal("0.001")
