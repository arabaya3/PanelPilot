"""Tests for `app/ai/tools/drive_ranges.py`.

The module is data, transcribed from each manufacturer's manual. The checks
that hold for every range are structural: a table that cannot be selected
from correctly is a transcription error whatever the manual says. Each range
then gets selections whose expected type was read off its manual's table by
hand, so a row shifted by one column would be caught.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from app.ai.tools import vfd_selection
from app.ai.tools.drive_ranges import RANGES
from app.core.errors import ValidationError
from app.models.schemas.calculations import DutyClass

KEYS = [r.key for r in RANGES]


def test_every_range_has_a_distinct_key() -> None:
    assert len(set(KEYS)) == len(KEYS)
    assert vfd_selection.DEFAULT_RANGE not in KEYS


@pytest.mark.parametrize("drive_range", RANGES, ids=KEYS)
def test_bands_are_ordered_and_do_not_overlap(drive_range: vfd_selection.DriveRange) -> None:
    bands = [(c.low_v, c.high_v) for c in drive_range.catalogues]
    assert bands == sorted(bands)
    for (low, high), (next_low, _) in itertools.pairwise(bands):
        assert low < high
        # Bands may share an endpoint, never more.
        assert next_low >= high


@pytest.mark.parametrize("drive_range", RANGES, ids=KEYS)
def test_every_rating_is_a_positive_current_with_a_page(
    drive_range: vfd_selection.DriveRange,
) -> None:
    for catalogue in drive_range.catalogues:
        assert catalogue.ratings
        currents = [Decimal(r.nominal_a) for r in catalogue.ratings]
        assert currents == sorted(currents)
        for rating in catalogue.ratings:
            assert Decimal(rating.nominal_a) > 0
            assert rating.page
            assert rating.page > 0
            assert rating.type_code.strip()
            if rating.heavy_duty_a is not None:
                # A heavy-duty rating never exceeds the same type's normal one.
                assert Decimal(rating.heavy_duty_a) <= Decimal(rating.nominal_a)


@pytest.mark.parametrize("drive_range", RANGES, ids=KEYS)
def test_deratings_are_well_formed(drive_range: vfd_selection.DriveRange) -> None:
    for rule in (drive_range.temperature, drive_range.altitude):
        assert rule.floor <= rule.full_up_to <= rule.limit
        assert rule.page > 0
        assert rule.section
        if rule.percent_per_step is not None:
            assert rule.percent_per_step > 0
            # Never derated to nothing within the range's own limits.
            span = (rule.limit - rule.full_up_to) / rule.step
            assert span * rule.percent_per_step < 100


def _select(key: str, supply: str, current: str, duty: DutyClass = DutyClass.NORMAL) -> str:
    return vfd_selection.select_frame(
        required_current_a=Decimal(current),
        supply_voltage_v=Decimal(supply),
        duty_class=duty,
        altitude_m=Decimal(0),
        ambient_temp_c=Decimal(25),
        drive_range=key,
    ).frame_reference


@pytest.mark.parametrize(
    ("key", "supply", "current", "duty", "expected"),
    [
        # Danfoss FC 302 OG, Table 36 (p. 66), 525-550 V: P11K HO 19 A, NO 23 A.
        (
            "danfoss-fc302",
            "550",
            "23",
            DutyClass.NORMAL,
            "FC-302P11KT6 (B3 (IP20); B1 (IP21/IP55/IP66))",
        ),
        (
            "danfoss-fc302",
            "550",
            "19",
            DutyClass.HEAVY,
            "FC-302P11KT6 (B3 (IP20); B1 (IP21/IP55/IP66))",
        ),
        # 551-600 V reads the 551-600 V row of the same table: NO 22 A.
        (
            "danfoss-fc302",
            "575",
            "22",
            DutyClass.NORMAL,
            "FC-302P11KT6 (B3 (IP20); B1 (IP21/IP55/IP66))",
        ),
        # Danfoss FC 51 OG, Table 14 (p. 29), 380-440 V: P5K5 12 A, one rating.
        ("danfoss-fc51", "400", "12", DutyClass.HEAVY, "FC-051P5K5T4 (M3)"),
        # Delta C2000 Plus UM, Table 9-2 (p. 331), 460 V: VFD750C43A heavy duty 150 A.
        ("delta-c2000plus", "400", "150", DutyClass.HEAVY, "VFD750C43A (D)"),
        # Schneider ATV630/650 IM, HD tables pp. 81-84: U07M3 3.3 A at 200-240 V,
        # C25N4 387 A as printed, U22Y6 2.4 A at 500-690 V.
        ("schneider-atv630", "230", "3.3", DutyClass.HEAVY, "ATV630U07M3 (1)"),
        ("schneider-atv630", "400", "387", DutyClass.HEAVY, "ATV630C25N4 (7B)"),
        ("schneider-atv630", "690", "2.4", DutyClass.HEAVY, "ATV630U22Y6 (3Y)"),
        # Fuji FRENIC-Mini (C2) IM, p. 219: 0001 is held at its bracketed 0.7 A,
        # so 0.8 A needs 0002 (bracketed 1.4 A).
        ("fuji-frenic-mini-c2", "230", "0.7", DutyClass.NORMAL, "FRN0001C2S-2"),
        ("fuji-frenic-mini-c2", "230", "0.8", DutyClass.NORMAL, "FRN0002C2S-2"),
        # Yaskawa GA800 TR, p. 534: 4568 is held at its >= 460 V rating, ND 515 A,
        # so 520 A normal duty needs the next size.
        ("yaskawa-ga800", "400", "515", DutyClass.NORMAL, "CIPR-GA80U4568"),
        ("yaskawa-ga800", "400", "520", DutyClass.NORMAL, "CIPR-GA80U4605"),
        # Mitsubishi FR-E800 Connection manual, p. 171: FR-E840-0095 LD 11.1 A,
        # ND 9.5 A; 11.2 A normal duty needs the next size.
        ("mitsubishi-fr-e800", "400", "11.1", DutyClass.NORMAL, "FR-E840-0095(3.7K)"),
        ("mitsubishi-fr-e800", "400", "9.5", DutyClass.HEAVY, "FR-E840-0095(3.7K)"),
        ("mitsubishi-fr-e800", "400", "11.2", DutyClass.NORMAL, "FR-E840-0120(5.5K)"),
        # Siemens V20 OI, p. 24: FSB 3.0 kW, 7.3 A.
        ("siemens-v20", "400", "7.3", DutyClass.NORMAL, "6SL3210-5BE23-0UV0 (FSB)"),
    ],
)
def test_a_selection_reads_the_manuals_table(
    key: str, supply: str, current: str, duty: DutyClass, expected: str
) -> None:
    assert _select(key, supply, current, duty) == expected


def test_a_curve_derating_is_not_read_as_a_rate() -> None:
    """Danfoss gives only limits; above 45 °C it refers to its Design Guide."""
    with pytest.raises(ValidationError, match="curve rather than a rate"):
        vfd_selection.select_frame(
            required_current_a=Decimal(10),
            supply_voltage_v=Decimal(400),
            duty_class=DutyClass.NORMAL,
            altitude_m=Decimal(0),
            ambient_temp_c=Decimal(50),
            drive_range="danfoss-fc302",
        )


def test_a_linear_derating_applies_the_manuals_rate() -> None:
    """Delta MS300: 2.5 % per °C above 40 °C (IP40 threshold, held for all)."""
    assert vfd_selection.temperature_derate(
        ambient_temp_c=Decimal(44), drive_range="delta-ms300"
    ) == Decimal("0.9")


def test_a_supply_a_range_is_not_rated_for_is_refused() -> None:
    with pytest.raises(ValidationError, match="SINAMICS V20 supply ranges"):
        _select("siemens-v20", "230", "5")
