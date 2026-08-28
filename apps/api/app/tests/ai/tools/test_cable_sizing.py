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

`size_conductor` and `derating_factor` remain unimplemented, and their tests
remain tripwires. See the module docstring and the tracker for why: the guide
publishes two sizing results but no worked combined-derating example, so a
`derating_factor` test would be one I invented.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import cable_sizing
from app.ai.tools.cable_sizing import LoadType
from app.core.errors import ValidationError
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod

# --- still stubs, still tripwires ---------------------------------------------


def test_size_conductor_accepts_documented_arguments_and_is_not_implemented() -> None:
    # Deliberately failing the moment someone implements this without a
    # published sizing example to check it against.
    with pytest.raises(NotImplementedError):
        cable_sizing.size_conductor(
            design_current_a=Decimal("63"),
            installation_method=InstallationMethod.C,
            ambient_temp_c=Decimal("35"),
            grouped_circuits=2,
            conductor_material=ConductorMaterial.COPPER,
            insulation_rating_c=90,
        )


def test_derating_factor_accepts_documented_arguments_and_is_not_implemented() -> None:
    # The guide tabulates k1 (Fig. G12) and k2 (Fig. G16) but prints no worked
    # combined result, so there is nothing published to check an implementation
    # against. Implementing it would mean writing the assertion myself.
    with pytest.raises(NotImplementedError):
        cable_sizing.derating_factor(
            ambient_temp_c=Decimal("35"),
            grouped_circuits=2,
            insulation_rating_c=90,
        )


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
