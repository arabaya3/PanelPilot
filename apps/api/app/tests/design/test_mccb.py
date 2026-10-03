"""Tests for `app/design/mccb.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import mccb

_400 = Decimal(400)


def test_ratings_run_from_160_to_800_a() -> None:
    assert mccb.ratings() == tuple(Decimal(r) for r in (160, 200, 250, 320, 400, 500, 630, 800))


def test_select_takes_the_smallest_frame_and_version_that_clears_the_fault() -> None:
    breaker = mccb.select(Decimal(140), Decimal(25), _400)
    assert breaker is not None
    assert breaker.type_number == "XT3N 250 TMD 160"
    assert breaker.magnetic_trip_a == 1600
    assert breaker.icu_ka == 36
    assert not breaker.adjustable
    assert "1SDC210033D0203" in breaker.source


def test_a_higher_fault_moves_to_xt4_and_a_larger_current_to_t5_and_t6() -> None:
    xt4 = mccb.select(Decimal(240), Decimal(60), _400)
    assert xt4 is not None
    assert xt4.type_number == "XT4H 250 TMA 250"
    assert xt4.adjustable
    t5 = mccb.select(Decimal(450), Decimal(10), _400)
    assert t5 is not None
    assert t5.type_number == "T5N 630 TMA 500"
    t6 = mccb.select(Decimal(700), None, _400)
    assert t6 is not None
    assert t6.type_number == "T6N 800 TMA 800"
    assert "1SDC210015D0208" in t6.source


def test_select_gives_none_beyond_the_tables() -> None:
    assert mccb.select(Decimal(900), Decimal(10), _400) is None
    assert mccb.select(Decimal(700), Decimal(160), _400) is None
    assert mccb.select(Decimal(200), Decimal(10), Decimal(230)) is None


def test_source_and_width_are_given_only_for_its_own_breakers() -> None:
    assert mccb.source_of("XT3N 250 TMD 160") == mccb.SOURCE_XT
    assert mccb.source_of("T6N 800 TMA 800") == mccb.SOURCE_T
    # A starter's breaker from the coordination tables is not one of them.
    assert mccb.source_of("T5H400 PR221-I In320") is None
    assert mccb.width_mm("XT3N 250 TMD 160", 3) == 105
    assert mccb.width_mm("T5N 630 TMA 500", 4) == 186
    assert mccb.width_mm("T2S160 MA 20", 3) is None
