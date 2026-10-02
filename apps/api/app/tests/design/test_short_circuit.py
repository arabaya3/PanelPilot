"""Tests for `app/design/short_circuit.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import short_circuit
from app.models.schemas.calculations import ConductorMaterial

_CU = ConductorMaterial.COPPER


def test_withstand_is_k_squared_s_squared() -> None:
    # Handbook Table 2: 2.5 mm² copper PVC, 8.27·10⁻² (kA)²s; 95 mm² XLPE 1.85·10².
    assert short_circuit.withstand_ka2s(Decimal("2.5"), _CU, 70) == Decimal("0.083")
    assert short_circuit.withstand_ka2s(Decimal(95), _CU, 90) == Decimal("184.552")
    assert short_circuit.withstand_ka2s(Decimal(16), ConductorMaterial.ALUMINIUM, 70) == Decimal(
        "1.479"
    )
    # Above 300 mm² PVC copper takes k = 103: 103² x 400² = 1697.44 (kA)²s.
    assert short_circuit.withstand_ka2s(Decimal(400), _CU, 70) == Decimal("1697.440")
    assert short_circuit.withstand_ka2s(Decimal(4), _CU, 105) is None


def test_min_current_is_the_handbook_approximation() -> None:
    # 0.8 x 230 / (1.5 x 0.018 x 2 x 50 / 2.5) = 170.4 A.
    current = short_circuit.min_current_a(Decimal(50), Decimal("2.5"), _CU, Decimal(230))
    assert current.quantize(Decimal("0.1")) == Decimal("170.4")
    # Above 95 mm² the reactance lowers it by ksec.
    large = short_circuit.min_current_a(Decimal(100), Decimal(120), _CU, Decimal(230))
    plain = Decimal("0.8") * 230 / (Decimal("1.5") * Decimal("0.018") * 2 * 100 / 120)
    assert large == plain * Decimal("0.9")


def _fit(length: str, section: str = "2.5", curve: str = "C") -> short_circuit.Checked | None:
    return short_circuit.fit(
        length_m=Decimal(length),
        section_mm2=Decimal(section),
        material=_CU,
        phase_voltage_v=Decimal(230),
        rated_a=Decimal(16),
        curve=curve,
    )


def test_fit_keeps_a_short_cable() -> None:
    checked = _fit("50")
    assert checked is not None
    assert checked.within
    assert checked.section_mm2 == Decimal("2.5")
    assert checked.trip_a == 160
    assert checked.min_current_a == 170


def test_fit_enlarges_a_long_cable() -> None:
    checked = _fit("80")
    assert checked is not None
    assert checked.within
    assert checked.section_mm2 == 4
    assert checked.min_current_a >= checked.trip_a


def test_fit_says_when_no_section_is_enough_or_none_is_held() -> None:
    checked = _fit("9000", curve="D")
    assert checked is not None
    assert not checked.within
    assert _fit("50", curve="K") is None
    assert _fit("50", section="3") is None


def test_parallel_conductors_raise_the_far_end_current_by_kpar() -> None:
    one = short_circuit.min_current_a(Decimal(100), Decimal(240), _CU, Decimal(230))
    three = short_circuit.min_current_a(Decimal(100), Decimal(240), _CU, Decimal(230), 3)
    assert three == one * Decimal("2.7")
