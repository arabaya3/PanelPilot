"""Tests for `app/ai/tools/vfd_selection.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The motor current is checked against Technical guide No. 7's own Example 3.4;
the selections against rows of the ACS880-01 hardware manual's IEC ratings
table (pp. 234-235), each named so a reviewer can open the page.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.ai.tools import vfd_selection
from app.core.errors import ValidationError
from app.models.schemas.calculations import DutyClass


def test_motor_current_matches_the_guides_example_3_4() -> None:
    """Guide No. 7 Example 3.4: 37 kW, 380 V, 71 A, cos 0.85 -> η ≈ 0.931.

    Run backwards: 37 kW at 380 V, η 0.931 and cos 0.85 draws 71 A.
    """
    current = vfd_selection.required_drive_current_a(
        motor_power_kw=Decimal("37"),
        supply_voltage_v=Decimal("380"),
        motor_efficiency=Decimal("0.931"),
        motor_power_factor=Decimal("0.85"),
        duty_class=DutyClass.NORMAL,
    )
    assert current.quantize(Decimal("1")) == Decimal("71")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("motor_efficiency", "0"),
        ("motor_efficiency", "1.2"),
        ("motor_power_factor", "NaN"),
        ("motor_power_kw", "-1"),
    ],
)
def test_an_impossible_motor_is_refused(name: str, value: str) -> None:
    arguments = {
        "motor_power_kw": Decimal("22"),
        "supply_voltage_v": Decimal("400"),
        "motor_efficiency": Decimal("0.94"),
        "motor_power_factor": Decimal("0.86"),
    }
    arguments[name] = Decimal(value)
    with pytest.raises(ValidationError):
        vfd_selection.required_drive_current_a(**arguments, duty_class=DutyClass.NORMAL)


def test_altitude_is_one_point_per_100_m_above_1000() -> None:
    # The manual's own example: 1500 m -> 0.95.
    assert vfd_selection.altitude_derate(altitude_m=Decimal("1500")) == Decimal("0.95")
    assert vfd_selection.altitude_derate(altitude_m=Decimal("1000")) == 1
    assert vfd_selection.altitude_derate(altitude_m=Decimal("4000")) == Decimal("0.70")
    with pytest.raises(ValidationError, match="4000"):
        vfd_selection.altitude_derate(altitude_m=Decimal("4001"))


def test_temperature_is_one_percent_per_degree_above_40() -> None:
    assert vfd_selection.temperature_derate(ambient_temp_c=Decimal("40")) == 1
    assert vfd_selection.temperature_derate(ambient_temp_c=Decimal("50")) == Decimal("0.90")
    with pytest.raises(ValidationError):
        vfd_selection.temperature_derate(ambient_temp_c=Decimal("56"))


def _select(current: str, duty: DutyClass = DutyClass.NORMAL, **site: Decimal) -> str:
    return vfd_selection.select_frame(
        required_current_a=Decimal(current),
        supply_voltage_v=Decimal("400"),
        duty_class=duty,
        altitude_m=site.get("altitude_m", Decimal(0)),
        ambient_temp_c=site.get("ambient_temp_c", Decimal(40)),
    ).frame_reference


def test_normal_duty_reads_i2() -> None:
    # 045A-3: I2 45 A. 45 fits it; 45.1 needs 061A-3.
    assert _select("45") == "ACS880-01-045A-3 (R4)"
    assert _select("45.1") == "ACS880-01-061A-3 (R4)"


def test_heavy_duty_reads_ihd() -> None:
    # 061A-3: IHd 45 A. The same 45 A heavy-duty motor needs the next size.
    assert _select("45", DutyClass.HEAVY) == "ACS880-01-061A-3 (R4)"


def test_types_without_a_50_percent_overload_are_skipped_for_heavy_duty() -> None:
    # 293A-3's IHd is footnoted to 30 % overload; 250 A heavy goes to 363A-3.
    assert _select("250", DutyClass.HEAVY) == "ACS880-01-363A-3 (R9)"


def test_derating_can_push_to_the_next_size() -> None:
    # 045A-3 at 50 °C gives 45 x 0.90 = 40.5 A; 42 A needs 061A-3.
    assert _select("40", ambient_temp_c=Decimal(50)) == "ACS880-01-045A-3 (R4)"
    assert _select("42", ambient_temp_c=Decimal(50)) == "ACS880-01-061A-3 (R4)"


def test_what_the_catalogue_does_not_cover_is_refused() -> None:
    with pytest.raises(ValidationError, match="no ACS880-01"):
        _select("700")
    for supply in ("230", "510", "620", "720", "NaN"):
        with pytest.raises(ValidationError, match="supply ranges"):
            vfd_selection.select_frame(
                required_current_a=Decimal("10"),
                supply_voltage_v=Decimal(supply),
                duty_class=DutyClass.NORMAL,
                altitude_m=Decimal(0),
                ambient_temp_c=Decimal(40),
            )


def _select_at(supply: str, current: str, duty: DutyClass = DutyClass.NORMAL) -> str:
    return vfd_selection.select_frame(
        required_current_a=Decimal(current),
        supply_voltage_v=Decimal(supply),
        duty_class=duty,
        altitude_m=Decimal(0),
        ambient_temp_c=Decimal(40),
    ).frame_reference


@pytest.mark.parametrize(
    ("supply", "current", "duty", "expected"),
    [
        # Un = 500 V, p. 237: 052A-5 is I2 52 A, IHd 40 A.
        ("500", "52", DutyClass.NORMAL, "ACS880-01-052A-5 (R4)"),
        ("480", "41", DutyClass.HEAVY, "ACS880-01-065A-5 (R5)"),
        # 260A-5's IHd is footnoted (30 % overload): heavy duty skips to 361A-5.
        ("500", "200", DutyClass.HEAVY, "ACS880-01-361A-5 (R9)"),
        # Un = 690 V, p. 238: 061A-7 is I2 61 A, IHd 49 A.
        ("690", "61", DutyClass.NORMAL, "ACS880-01-061A-7 (R6)"),
        ("660", "50", DutyClass.HEAVY, "ACS880-01-084A-7 (R6)"),
        # Un = 575 V, UL, p. 240: 035A-7 is ILd 41 A, IHd 32 A.
        ("575", "41", DutyClass.NORMAL, "ACS880-01-035A-7 (R5)"),
        ("525", "33", DutyClass.HEAVY, "ACS880-01-042A-7 (R5)"),
        # 271A-7's IHd is footnoted: nothing at 575 V carries 200 A heavy duty.
        ("600", "192", DutyClass.HEAVY, "ACS880-01-210A-7 (R9)"),
        # 400 V stays on the -3 range.
        ("400", "52", DutyClass.NORMAL, "ACS880-01-061A-3 (R4)"),
    ],
)
def test_the_supply_picks_the_range(
    supply: str, current: str, duty: DutyClass, expected: str
) -> None:
    assert _select_at(supply, current, duty) == expected


def test_the_ratings_citation_follows_the_supply() -> None:
    assert vfd_selection.ratings_citation(Decimal("400")).page == 234
    assert (
        vfd_selection.ratings_citation(Decimal("500")).section
        == "Electrical ratings, IEC, Un = 500 V"
    )
    assert (
        vfd_selection.ratings_citation(Decimal("690")).section
        == "Electrical ratings, IEC, Un = 690 V"
    )
    assert vfd_selection.ratings_citation(Decimal("575")).page == 240
    with pytest.raises(ValidationError):
        vfd_selection.ratings_citation(Decimal("630"))


@pytest.mark.parametrize(
    "ratings",
    [
        vfd_selection._RATINGS_400V,
        vfd_selection._RATINGS_500V,
        vfd_selection._RATINGS_575V,
        vfd_selection._RATINGS_690V,
    ],
)
def test_the_catalogue_rises_with_the_type(ratings: tuple[vfd_selection._Rating, ...]) -> None:
    nominal = [Decimal(r.nominal_a) for r in ratings]
    heavy = [Decimal(r.heavy_duty_a) for r in ratings if r.heavy_duty_a]
    assert nominal == sorted(nominal)
    assert heavy == sorted(heavy)
    for rating in ratings:
        if rating.heavy_duty_a is not None:
            assert Decimal(rating.heavy_duty_a) < Decimal(rating.nominal_a), rating.type_code


def test_every_public_function_is_keyword_only() -> None:
    for name in (
        "required_drive_current_a",
        "altitude_derate",
        "temperature_derate",
        "select_frame",
    ):
        signature = inspect.signature(getattr(vfd_selection, name))
        positional = [
            p.name
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, f"{name} accepts positional arguments: {positional}"
