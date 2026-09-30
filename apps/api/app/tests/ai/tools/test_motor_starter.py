"""Tests for `app/ai/tools/motor_starter.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Checked against the two worked examples ABB prints beside the tables
(handbook Vol. 2, p. 134 as printed), and against named rows of Table 3.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import motor_starter
from app.core.errors import ValidationError
from app.models.schemas.calculations import StartType


def _select(kw: str, amps: str, start: StartType, **kw_args: Decimal) -> motor_starter.StarterRow:
    return motor_starter.select_starter(
        motor_power_kw=Decimal(kw),
        motor_current_a=Decimal(amps),
        start=start,
        supply_voltage_v=kw_args.get("supply_voltage_v", Decimal(400)),
        fault_level_ka=kw_args.get("fault_level_ka"),
    ).row


def test_abb_example_star_delta_200_kw() -> None:
    """ABB p. 134: star-delta at 400 V and 50 kA, 200 kW.

    Ir 349 A, T5S630 PR221-I In630, I3 4410 A, line A210, delta A210,
    star A185, E320DU320 (100-320 A).
    """
    row = _select("200", "349", StartType.STAR_DELTA)

    assert row.current_a == "349"
    assert row.breaker == "T5S630 PR221-I In630"
    assert row.magnetic_trip_a == "4410"
    assert row.contactors == ("A210", "A210", "A185")
    assert row.overload == "E320DU320"
    assert row.overload_range_a == ("100", "320")


def test_abb_example_heavy_duty_dol_55_kw() -> None:
    """ABB p. 134: heavy-duty DOL with MP release, 55 kW.

    Ir 98 A, T4S250 PR222MP In160, I3 960 A, contactor A145.
    """
    row = _select("55", "98", StartType.DOL_HEAVY)

    assert row.current_a == "98"
    assert row.breaker == "T4S250 PR222MP In160"
    assert row.magnetic_trip_a == "960"
    assert row.contactors == ("A145",)
    # The MP release protects against overload itself.
    assert row.overload is None


def test_dol_rows_from_table_3() -> None:
    row = _select("7.5", "15.2", StartType.DOL)
    assert (row.breaker, row.contactors, row.overload) == ("T2S160 MA 20", ("A30",), "TA25DU19")
    row = _select("110", "193", StartType.DOL)
    assert (row.breaker, row.contactors, row.overload) == (
        "T4S320 PR221-I In320",
        ("A210",),
        "E320DU320",
    )


def test_between_rows_the_larger_is_taken() -> None:
    # 6 kW sits between 5.5 and 7.5 kW; a 5.5 kW motor drawing 13 A exceeds
    # its row's 11.5 A. Both go up a row, never down.
    assert _select("6", "12", StartType.DOL).power_kw == "7.5"
    assert _select("5.5", "13", StartType.DOL).power_kw == "7.5"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"supply_voltage_v": Decimal(690)}, "400 V only"),
        ({"fault_level_ka": Decimal(65)}, "50 kA"),
    ],
)
def test_outside_the_tables_is_refused(overrides: dict[str, Decimal], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _select("7.5", "15.2", StartType.DOL, **overrides)


def test_a_motor_past_the_table_or_below_star_delta_is_refused() -> None:
    with pytest.raises(ValidationError, match="outside Table 3"):
        _select("400", "700", StartType.DOL)
    # Star-delta starts at 18.5 kW in the table; a 20 A motor of 11 kW maps
    # to its first row, which the table does give.
    assert _select("11", "22", StartType.STAR_DELTA).power_kw == "18.5"
    with pytest.raises(ValidationError, match="positive"):
        _select("0", "1", StartType.DOL)


@pytest.mark.parametrize("start", list(StartType))
def test_every_table_rises_with_the_motor(start: StartType) -> None:
    rows, _page, _section = motor_starter._TABLES[start]
    powers = [Decimal(r.power_kw) for r in rows]
    currents = [Decimal(r.current_a) for r in rows]
    trips = [Decimal(r.magnetic_trip_a) for r in rows]
    assert powers == sorted(powers)
    assert currents == sorted(currents)
    assert trips == sorted(trips)
    for row in rows:
        if row.overload_range_a is not None:
            low, high = (Decimal(x) for x in row.overload_range_a)
            assert low < high, row.power_kw
    # Not asserted: that each DOL row's Ir lies inside its relay's range. The
    # table itself prints 15 kW at 28.5 A with a 29-42 A relay; the source is
    # transcribed as printed, not corrected.
