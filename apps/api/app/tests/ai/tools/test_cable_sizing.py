"""Tests for `app/ai/tools/cable_sizing.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

`voltage_drop` is checked against worked examples **published in the source
guide itself**, not against values computed alongside the code. Schneider
Electric, *Electrical Installation Guide* 2010, Chapter G: §3 Examples 1 and 2,
and §8 Fig. G68. Each case below names the example it came from, so a reviewer
can open the guide and check the arithmetic without reading the implementation.

That distinction is the whole point of this task. A test written from the same
understanding that produced the code proves the two agree, not that either is
right — and the spec is explicit that "close enough is not an acceptable test
result, given real cable/fire safety is downstream of this number."

`size_conductor` and `derating_factor` are checked the same way, against the
two worked sizing examples in ABB's *Electrical installation handbook* Vol. 2
(1SDC010001D0204), pp. 52-55.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import cable_sizing
from app.ai.tools.cable_sizing import LoadType
from app.core.errors import ValidationError
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod

# --- ABB handbook worked examples ---------------------------------------------


def test_abb_example_bunched_on_a_tray_at_40_c() -> None:
    """ABB Vol. 2 pp. 52-53: 100 A, Cu/PVC multi-core, method E, 40 °C, 7 circuits.

    The handbook reads k1 = 0.87 and k2 = 0.54, so I'b = 212.85 A, and selects
    95 mm2 (I0 238 A); Iz = 238 x 0.87 x 0.54 = 111.81 A.
    """
    result = cable_sizing.size_conductor(
        design_current_a=Decimal("100"),
        installation_method=InstallationMethod.E,
        ambient_temp_c=Decimal("40"),
        grouped_circuits=7,
        conductor_material=ConductorMaterial.COPPER,
        insulation_rating_c=70,
    )

    assert result.cross_section_mm2 == Decimal("95")
    assert [f.value for f in result.applied_factors] == [Decimal("0.87"), Decimal("0.54")]
    assert result.derated_ampacity_a.quantize(Decimal("0.01")) == Decimal("111.81")
    assert all(f.source.manufacturer == "ABB" for f in result.applied_factors)


def test_abb_example_single_circuit_at_reference_conditions() -> None:
    """ABB Vol. 2 pp. 54-55: 115 A, Cu/PVC, method E, 30 °C, alone -> 35 mm2, 126 A."""
    result = cable_sizing.size_conductor(
        design_current_a=Decimal("115"),
        installation_method=InstallationMethod.E,
        ambient_temp_c=Decimal("30"),
        grouped_circuits=1,
        conductor_material=ConductorMaterial.COPPER,
        insulation_rating_c=70,
    )

    assert result.cross_section_mm2 == Decimal("35")
    assert result.derated_ampacity_a == Decimal("126")


def test_the_combined_factor_is_the_handbooks_k1_times_k2() -> None:
    # The same example's factors: 0.87 x 0.54.
    assert cable_sizing.derating_factor(
        ambient_temp_c=Decimal("40"), grouped_circuits=7, insulation_rating_c=70
    ) == Decimal("0.87") * Decimal("0.54")


def test_between_rows_the_harsher_row_is_read() -> None:
    # 37 °C reads the 40 °C row, 10 circuits the 12 column: never interpolated,
    # never the kinder neighbour.
    assert cable_sizing.derating_factor(
        ambient_temp_c=Decimal("37"), grouped_circuits=10, insulation_rating_c=90
    ) == Decimal("0.91") * Decimal("0.45")


def test_the_selected_section_is_the_smallest_that_carries_the_load() -> None:
    # Method C, XLPE, three loaded: 2.5 mm2 carries 30 A, 4 mm2 40 A.
    def size(current: str) -> Decimal:
        return cable_sizing.size_conductor(
            design_current_a=Decimal(current),
            installation_method=InstallationMethod.C,
            ambient_temp_c=Decimal("30"),
            grouped_circuits=1,
            conductor_material=ConductorMaterial.COPPER,
            insulation_rating_c=90,
        ).cross_section_mm2

    assert size("30") == Decimal("2.5")
    assert size("30.1") == Decimal("4")


def test_single_phase_reads_the_two_loaded_column() -> None:
    # Method C, XLPE, 2.5 mm2: 33 A with two loaded conductors, 30 A with three.
    result = cable_sizing.size_conductor(
        design_current_a=Decimal("33"),
        installation_method=InstallationMethod.C,
        ambient_temp_c=Decimal("30"),
        grouped_circuits=1,
        conductor_material=ConductorMaterial.COPPER,
        insulation_rating_c=90,
        three_phase=False,
    )
    assert result.cross_section_mm2 == Decimal("2.5")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"conductor_material": ConductorMaterial.ALUMINIUM}, "copper only"),
        ({"installation_method": InstallationMethod.D1}, "not supported"),
        ({"insulation_rating_c": 105}, "not tabulated"),
        ({"ambient_temp_c": Decimal("65"), "insulation_rating_c": 70}, "outside Table 4"),
        ({"ambient_temp_c": Decimal("5")}, "outside Table 4"),
        ({"grouped_circuits": 21}, "outside Table 5"),
        ({"grouped_circuits": 0}, "outside Table 5"),
        ({"design_current_a": Decimal("2000")}, "parallel conductors"),
        ({"design_current_a": Decimal("-1")}, "positive"),
        ({"ambient_temp_c": Decimal("NaN")}, "finite"),
    ],
)
def test_what_the_tables_do_not_cover_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    arguments: dict[str, object] = {
        "design_current_a": Decimal("63"),
        "installation_method": InstallationMethod.C,
        "ambient_temp_c": Decimal("35"),
        "grouped_circuits": 2,
        "conductor_material": ConductorMaterial.COPPER,
        "insulation_rating_c": 90,
    }
    arguments.update(overrides)
    with pytest.raises(ValidationError, match=message):
        cable_sizing.size_conductor(**arguments)  # type: ignore[arg-type]


def test_every_ampacity_column_rises_with_the_section() -> None:
    # A transcription slip that swapped two cells would show as a column that
    # does not increase.
    for method, rows in cable_sizing._AMPACITY_CU.items():
        for column in range(4):
            values = [Decimal(row[column]) for row in rows.values()]
            assert values == sorted(values), (method, column)


# --- EIG §3 Example 1: 35 mm2 Cu, three-phase, 50 m ---------------------------


def test_example_1_normal_service() -> None:
    """EIG §3 Example 1: 100 A at cos phi 0.8 over 50 m of 35 mm2 -> 5 V.

    The guide reads 1 V/A/km from Fig. G28 and computes 1 x 100 x 0.05 = 5 V.
    """
    assert cable_sizing.voltage_drop(
        current_a=Decimal("100"),
        length_m=Decimal("50"),
        cross_section_mm2=Decimal("35"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=True,
    ) == Decimal("5")


def test_example_1_during_motor_start_up() -> None:
    """EIG §3 Example 1: 500 A at cos phi 0.35 over the same run -> 13 V.

    The guide reads 0.52 V/A/km and computes 0.52 x 500 x 0.05 = 13 V. This is
    the case that makes the start-up column load-bearing rather than
    decorative: at five times the current it is the drop that decides whether
    the motor starts.
    """
    assert cable_sizing.voltage_drop(
        current_a=Decimal("500"),
        length_m=Decimal("50"),
        cross_section_mm2=Decimal("35"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.35"),
        three_phase=True,
    ) == Decimal("13")


# --- EIG §3 Example 2: a 70 mm2 line feeding 2.5 mm2 lighting circuits --------


def test_example_2_three_phase_line() -> None:
    """EIG §3 Example 2: 150 A over 50 m of 70 mm2 -> 4.125 V.

    The guide reads **0.55** V/A/km here, which is Fig. G28's *lighting*
    column — the motor column at 70 mm2 is 0.56. That is not a typo in the
    guide: the line "supplies, among other loads, 3 single-phase lighting
    circuits". Reading the motor column instead gives 4.2 V, and the guide
    prints 4.125.

    Worth stating because it is exactly the error a plausible implementation
    makes: one column per cross-section looks obviously right and is wrong.
    """
    assert cable_sizing.voltage_drop(
        current_a=Decimal("150"),
        length_m=Decimal("50"),
        cross_section_mm2=Decimal("70"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=True,
        load_type=LoadType.LIGHTING,
    ) == Decimal("4.125")


def test_example_2_single_phase_lighting_circuit() -> None:
    """EIG §3 Example 2: 20 A over 20 m of 2.5 mm2 single-phase -> 7.2 V.

    18 V/A/km from the single-phase lighting column: 18 x 20 x 0.02 = 7.2 V.
    """
    assert cable_sizing.voltage_drop(
        current_a=Decimal("20"),
        length_m=Decimal("20"),
        cross_section_mm2=Decimal("2.5"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=False,
        load_type=LoadType.LIGHTING,
    ) == Decimal("7.2")


# --- EIG §8 Fig. G68: checked to the guide's own precision --------------------
#
# Three more cases from the full worked example. They are NOT asserted for
# exact equality, and that is a finding rather than a convenience:
#
#   C1  2x240 mm2, 433 A/conductor, 5 m   -> computes 0.4546, guide prints 0.45
#   C3  2x95 mm2,  254.5 A/conductor, 20 m -> computes 2.1378, guide prints 2.1
#   C7  1x95 mm2,  255 A, 5 m              -> computes 0.5355, guide prints 0.53
#
# C7 is the telling one: 0.5355 rounds to 0.54, and the guide prints 0.53 — it
# truncates. Its percentage column is inconsistent with either reading. So
# Fig. G68's figures are rounded editorial output, not exact worked results,
# and asserting 2dp equality against them would be pinning Schneider's
# rounding convention rather than the calculation.
#
# They are kept, at 1 significant figure of tolerance, because they still catch
# a wrong table column or a factor-of-1000 error — the failures that matter.
# The four §3 examples above are the exact-match evidence.


@pytest.mark.parametrize(
    ("label", "per_conductor_a", "length_m", "csa", "published_v"),
    [
        ("C1 2x240 mm2", Decimal("433"), Decimal("5"), Decimal("240"), Decimal("0.45")),
        ("C3 2x95 mm2", Decimal("254.5"), Decimal("20"), Decimal("95"), Decimal("2.1")),
        ("C7 1x95 mm2", Decimal("255"), Decimal("5"), Decimal("95"), Decimal("0.53")),
    ],
)
def test_fig_g68_worked_example_cases(
    label: str,
    per_conductor_a: Decimal,
    length_m: Decimal,
    csa: Decimal,
    published_v: Decimal,
) -> None:
    """EIG §8 Fig. G68, balanced three-phase motor circuits at cos phi 0.8.

    The parallel cases are entered per conductor, because that is what the
    guide does: C1 carries 866 A over two conductors and the table's V/A/km is
    a per-conductor figure. Feeding the full 866 A would double the answer.
    """
    del label
    computed = cable_sizing.voltage_drop(
        current_a=per_conductor_a,
        length_m=length_m,
        cross_section_mm2=csa,
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=True,
    )

    # Within 2% of the printed value: tight enough to catch a wrong column or
    # a unit error, loose enough not to assert the guide's rounding.
    assert abs(computed - published_v) <= published_v * Decimal("0.02")


# --- refusals, which are the safety behaviour --------------------------------


def test_an_untabulated_cross_section_is_refused() -> None:
    # AI-005's stated edge case: outside the table raises rather than
    # extrapolating, so BE-011/BE-008 can turn it into a refusal.
    with pytest.raises(ValidationError, match="not a cross-section tabulated"):
        cable_sizing.voltage_drop(
            current_a=Decimal("100"),
            length_m=Decimal("50"),
            cross_section_mm2=Decimal("42"),
            conductor_material=ConductorMaterial.COPPER,
            power_factor=Decimal("0.8"),
            three_phase=True,
        )


def test_an_untabulated_power_factor_is_refused_rather_than_interpolated() -> None:
    # Fig. G28 gives two power factors, and the relationship between them
    # includes reactance and is not linear. Interpolating would produce a
    # confident number the guide does not support.
    with pytest.raises(ValidationError, match="not tabulated"):
        cable_sizing.voltage_drop(
            current_a=Decimal("100"),
            length_m=Decimal("50"),
            cross_section_mm2=Decimal("35"),
            conductor_material=ConductorMaterial.COPPER,
            power_factor=Decimal("0.6"),
            three_phase=True,
        )


def test_aluminium_is_refused_rather_than_guessed() -> None:
    # The guide's aluminium column is offset against the copper one — its first
    # row pairs 6 mm2 Cu with 10 mm2 Al. Transcribing that offset wrongly is a
    # silent error in a safety number, so it is not transcribed at all.
    with pytest.raises(ValidationError, match="copper only"):
        cable_sizing.voltage_drop(
            current_a=Decimal("100"),
            length_m=Decimal("50"),
            cross_section_mm2=Decimal("35"),
            conductor_material=ConductorMaterial.ALUMINIUM,
            power_factor=Decimal("0.8"),
            three_phase=True,
        )


_VALID_DROP: dict[str, Decimal] = {
    "current_a": Decimal("100"),
    "length_m": Decimal("50"),
    "cross_section_mm2": Decimal("35"),
    "power_factor": Decimal("0.8"),
}


def _drop_with(argument: str, value: str) -> Decimal:
    """A tabulated three-phase copper case with one argument replaced."""
    arguments = {**_VALID_DROP, argument: Decimal(value)}
    return cable_sizing.voltage_drop(
        current_a=arguments["current_a"],
        length_m=arguments["length_m"],
        cross_section_mm2=arguments["cross_section_mm2"],
        power_factor=arguments["power_factor"],
        conductor_material=ConductorMaterial.COPPER,
        three_phase=True,
    )


@pytest.mark.parametrize("argument", ["current_a", "length_m"])
@pytest.mark.parametrize("value", ["-1", "0", "NaN", "sNaN", "Infinity", "-Infinity"])
def test_a_non_positive_or_non_finite_quantity_is_refused(argument: str, value: str) -> None:
    # A negative current returned a negative drop, NaN and Infinity came back
    # as results, and sNaN escaped as a TypeError. Each must be the documented
    # refusal instead: a number here goes on a drawing.
    with pytest.raises(ValidationError, match=argument):
        _drop_with(argument, value)


@pytest.mark.parametrize("argument", ["cross_section_mm2", "power_factor"])
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity"])
def test_a_non_finite_lookup_key_is_refused_before_the_lookup(argument: str, value: str) -> None:
    # sNaN cannot even be hashed into the table, and NaN compares as nothing;
    # both are refused as inputs rather than surfacing as a crash or as "not
    # tabulated" for a value that was never a number.
    with pytest.raises(ValidationError, match=f"{argument} must be a finite number"):
        _drop_with(argument, value)


def test_lighting_and_motor_columns_actually_differ() -> None:
    # Pinned because it is the distinction Example 2 turns on. If these ever
    # returned the same value, the load_type parameter would be silently
    # decorative and Example 2 would still pass for the wrong reason.
    motor = cable_sizing.voltage_drop(
        current_a=Decimal("100"),
        length_m=Decimal("1000"),
        cross_section_mm2=Decimal("70"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=True,
    )
    lighting = cable_sizing.voltage_drop(
        current_a=Decimal("100"),
        length_m=Decimal("1000"),
        cross_section_mm2=Decimal("70"),
        conductor_material=ConductorMaterial.COPPER,
        power_factor=Decimal("0.8"),
        three_phase=True,
        load_type=LoadType.LIGHTING,
    )

    assert motor != lighting
