"""Tests for `app/ai/tools/enclosure_sizing.py`.

Mirrors the module 1:1.

Reference figures are computed by hand from PD-002's sourced widths, which is
what the specification asks for: "exact match against hand-calculated reference
scenarios ... same discipline as AI-005/006/007's worked-example testing".

ABB S200 is 17.5 mm per module per pole (S201-C63, order code 2CDS251001R0634),
so every width below is a multiple of 17.5 and can be checked with a
calculator against the datasheet rather than against this code.

The row-count boundary carries the most weight. A first version used
``-(-width // rail)`` for ceiling division, which is correct for ints and wrong
for ``Decimal`` -- its floor division truncates toward zero, so 210 mm on a
465 mm rail returned **0 rows** and 500 mm returned **1**. That is a panel
sized a row short: it builds as a component with nowhere to go, and no other
assertion in this file would have caught it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools.din_module_width import ComponentCategory
from app.ai.tools.enclosure_sizing import (
    ComponentSpec,
    rail_requirements,
    size_enclosure,
)
from app.core.errors import ValidationError
from app.models.schemas.products import ProductClass, ProductRecord

_RAIL = Decimal("465")  # ETI TH-S 2, the real 24-module rail
_PITCH = Decimal("150")


def _mcb(quantity: int, *, poles: int = 1, group: str = "main") -> ComponentSpec:
    return ComponentSpec(
        category=ComponentCategory.MCB,
        series="ABB S200",
        quantity=quantity,
        poles=poles,
        group=group,
    )


def _enclosure(sku: str, width: str, height: str, depth: str = "200") -> ProductRecord:
    return ProductRecord(
        sku=sku,
        manufacturer="ETI",
        product_class=ProductClass.ENCLOSURE,
        description=f"enclosure {sku}",
        width_mm=Decimal(width),
        height_mm=Decimal(height),
        depth_mm=Decimal(depth),
        source_url="https://example.invalid/x",
    )


def _catalogue() -> list[ProductRecord]:
    """Three enclosures of increasing face area, smallest first."""
    return [
        _enclosure("SMALL", "500", "300"),
        _enclosure("MEDIUM", "600", "600"),
        _enclosure("LARGE", "800", "1200"),
    ]


# --- hand-calculated widths ---------------------------------------------------


def test_twelve_single_pole_mcbs_are_210_mm() -> None:
    """12 x 17.5 = 210 mm exactly, from the ABB S201 datasheet."""
    requirement = rail_requirements([_mcb(12)], usable_rail_mm=_RAIL)[0]

    assert requirement.width_mm == Decimal("210.0")


def test_a_three_pole_mcb_is_three_modules() -> None:
    """ABB publishes 3 spacings / 52.

    ABB publishes 3 spacings / 52.5 mm for the S203. Four of them is 210 mm
    — the same total as twelve single-pole devices, reached differently.
    """
    requirement = rail_requirements([_mcb(4, poles=3)], usable_rail_mm=_RAIL)[0]

    assert requirement.width_mm == Decimal("210.0")


def test_widths_across_groups_are_summed_into_the_total() -> None:
    result = size_enclosure(
        [_mcb(4, group="incoming"), _mcb(8, group="outgoing")],
        catalogue=_catalogue(),
        usable_rail_mm=_RAIL,
        row_pitch_mm=_PITCH,
    )

    assert result.total_width_mm == Decimal("210.0")


# --- the row-count boundary ----------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "width", "rows"),
    [
        (1, "17.5", 1),
        (12, "210.0", 1),
        (26, "455.0", 1),  # 455 <= 465, still one row
        (27, "472.5", 2),  # 472.5 > 465, spills
        (53, "927.5", 2),  # 930 would be 3; 927.5 is not
        (54, "945.0", 3),
    ],
)
def test_rows_round_up_at_the_right_point(quantity: int, width: str, rows: int) -> None:
    """The boundary the Decimal floor-division bug crossed silently.

    26 devices are 455 mm and fit one 465 mm rail; 27 are 472.5 mm and need
    two. A version returning 0 for the first case and 1 for the second passes
    a "returns a number" check and produces an unbuildable panel.
    """
    requirement = rail_requirements([_mcb(quantity)], usable_rail_mm=_RAIL)[0]

    assert requirement.width_mm == Decimal(width)
    assert requirement.rows == rows


def test_a_row_is_never_zero_for_a_real_component() -> None:
    """Stated separately because zero is the specific wrong answer.

    Any component at all occupies at least one row; a result of 0 means the
    arithmetic, not the panel.
    """
    for quantity in (1, 2, 5, 12, 26):
        assert rail_requirements([_mcb(quantity)], usable_rail_mm=_RAIL)[0].rows >= 1


# --- functional grouping --------------------------------------------------------


def test_groups_are_packed_independently() -> None:
    """Two groups of 12 are two rows, not one.

    Merged they would be 420 mm and fit a single 465 mm rail. A panel
    schedule's rows are a wiring decision, so packing across them saves space
    on paper and produces a panel whose incoming and outgoing sections share a
    rail.
    """
    requirements = rail_requirements(
        [_mcb(12, group="incoming"), _mcb(12, group="outgoing")],
        usable_rail_mm=_RAIL,
    )

    assert len(requirements) == 2
    assert sum(r.rows for r in requirements) == 2


def test_lines_in_the_same_group_are_combined() -> None:
    requirements = rail_requirements(
        [_mcb(6, group="main"), _mcb(6, group="main")], usable_rail_mm=_RAIL
    )

    assert len(requirements) == 1
    assert requirements[0].width_mm == Decimal("210.0")


# --- selection ------------------------------------------------------------------


def test_the_smallest_fitting_enclosure_is_chosen() -> None:
    """One row at a 150 mm pitch needs 150 mm of height.

    One row at a 150 mm pitch needs 150 mm of height; SMALL is 500x300 and
    holds it. Choosing MEDIUM would be a working answer and the wrong one.
    """
    result = size_enclosure(
        [_mcb(12)],
        catalogue=_catalogue(),
        usable_rail_mm=Decimal("450"),
        row_pitch_mm=_PITCH,
    )

    assert result.enclosure.sku == "SMALL"


def test_a_taller_requirement_selects_a_taller_enclosure() -> None:
    """Three rows need 450 mm, which SMALL's 300 mm cannot hold."""
    result = size_enclosure(
        [_mcb(12, group="a"), _mcb(12, group="b"), _mcb(12, group="c")],
        catalogue=_catalogue(),
        usable_rail_mm=Decimal("450"),
        row_pitch_mm=_PITCH,
    )

    assert result.total_rows == 3
    assert result.enclosure.sku == "MEDIUM"


def test_selection_is_deterministic_between_equal_candidates() -> None:
    """Two enclosures can share a face area.

    A selection that varied between runs would make a BOM irreproducible, so
    the tie breaks on depth and then SKU.
    """
    catalogue = [
        _enclosure("B-SKU", "600", "600", depth="300"),
        _enclosure("A-SKU", "600", "600", depth="300"),
    ]

    chosen = {
        size_enclosure(
            [_mcb(12)],
            catalogue=list(catalogue),
            usable_rail_mm=Decimal("450"),
            row_pitch_mm=_PITCH,
        ).enclosure.sku
        for _ in range(5)
    }

    assert chosen == {"A-SKU"}


def test_a_cover_is_never_selected() -> None:
    """Covers carry real dimensions and are doors.

    PD-001 keeps them in the dataset for BOM completion; offering one here
    would propose mounting a breaker on a panel front.
    """
    catalogue = [
        ProductRecord(
            sku="COVER",
            manufacturer="ETI",
            product_class=ProductClass.COVER,
            description="a very large cover",
            width_mm=Decimal("5000"),
            height_mm=Decimal("5000"),
            source_url="https://example.invalid/c",
        ),
        _enclosure("REAL", "600", "600"),
    ]

    result = size_enclosure(
        [_mcb(12)],
        catalogue=catalogue,
        usable_rail_mm=Decimal("450"),
        row_pitch_mm=_PITCH,
    )

    assert result.enclosure.sku == "REAL"


# --- refusals -------------------------------------------------------------------


def test_an_oversized_list_refuses_rather_than_force_fitting() -> None:
    """The specification's stated edge case.

    The specification's stated edge case, in its own words: "must return a
    clear 'no standard enclosure fits, needs custom sizing' refusal rather than
    being force-fit into the closest available option".
    """
    with pytest.raises(ValidationError, match="no standard enclosure fits"):
        size_enclosure(
            [_mcb(500)],
            catalogue=_catalogue(),
            usable_rail_mm=_RAIL,
            row_pitch_mm=_PITCH,
        )


def test_the_refusal_names_the_largest_candidate() -> None:
    """So the caller can see how far short it fell rather than only that it.

    So the caller can see how far short it fell rather than only that it
    did.
    """
    with pytest.raises(ValidationError, match="LARGE"):
        size_enclosure(
            [_mcb(500)],
            catalogue=_catalogue(),
            usable_rail_mm=_RAIL,
            row_pitch_mm=_PITCH,
        )


def test_a_component_wider_than_the_rail_is_refused() -> None:
    """No number of rows accommodates a part that does not fit one.

    Rounding up would report a fit that cannot be built.
    """
    with pytest.raises(ValidationError, match="exceeds"):
        rail_requirements([_mcb(1, poles=3)], usable_rail_mm=Decimal("20"))


def test_an_empty_component_list_is_refused() -> None:
    with pytest.raises(ValidationError, match="empty component list"):
        rail_requirements([], usable_rail_mm=_RAIL)


def test_a_zero_quantity_line_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        rail_requirements([_mcb(0)], usable_rail_mm=_RAIL)


def test_a_non_positive_rail_length_is_refused() -> None:
    """Zero would make every requirement infinite rows; negative is nonsense.

    Neither may be absorbed, because the rail length is the one figure the
    caller supplies from outside the sourced data.
    """
    with pytest.raises(ValidationError, match="rail length must be positive"):
        rail_requirements([_mcb(1)], usable_rail_mm=Decimal("0"))


def test_a_non_positive_row_pitch_is_refused() -> None:
    with pytest.raises(ValidationError, match="row pitch must be positive"):
        size_enclosure(
            [_mcb(1)],
            catalogue=_catalogue(),
            usable_rail_mm=_RAIL,
            row_pitch_mm=Decimal("0"),
        )


def test_an_unsourced_component_width_propagates_the_refusal() -> None:
    """PD-002 refuses a contactor because no manufacturer datasheet was.

    PD-002 refuses a contactor because no manufacturer datasheet was
    reachable. That refusal has to reach the caller rather than being defaulted
    into a plausible width here.
    """
    with pytest.raises(ValidationError):
        rail_requirements(
            [ComponentSpec(category=ComponentCategory.RCBO, quantity=1, poles=3)],
            usable_rail_mm=_RAIL,
        )


def test_a_catalogue_with_no_usable_enclosure_is_refused() -> None:
    """Distinct from "nothing fits": there was nothing to try."""
    catalogue = [
        ProductRecord(
            sku="NO-HEIGHT",
            manufacturer="ETI",
            product_class=ProductClass.ENCLOSURE,
            description="width only",
            width_mm=Decimal("600"),
            source_url="https://example.invalid/n",
        )
    ]

    with pytest.raises(ValidationError, match="no enclosure with both"):
        size_enclosure(
            [_mcb(1)],
            catalogue=catalogue,
            usable_rail_mm=_RAIL,
            row_pitch_mm=_PITCH,
        )


def test_an_enclosure_narrower_than_the_rail_is_not_selected() -> None:
    """The rail has to physically fit inside the enclosure.

    A mutation dropping the width check survived every other test here: the
    tall-enough enclosures happened to be wide enough too. A 300 mm cabinet
    cannot hold a 465 mm rail, and selecting one produces a BOM whose parts do
    not assemble.
    """
    catalogue = [
        _enclosure("TOO-NARROW", "300", "2000"),
        _enclosure("WIDE-ENOUGH", "600", "2000"),
    ]

    result = size_enclosure(
        [_mcb(12)],
        catalogue=catalogue,
        usable_rail_mm=Decimal("465"),
        row_pitch_mm=_PITCH,
    )

    assert result.enclosure.sku == "WIDE-ENOUGH"


def test_a_catalogue_of_only_narrow_enclosures_refuses() -> None:
    """Tall enough and still unusable."""
    catalogue = [_enclosure("NARROW", "300", "2000")]

    with pytest.raises(ValidationError, match="no standard enclosure fits"):
        size_enclosure(
            [_mcb(12)],
            catalogue=catalogue,
            usable_rail_mm=Decimal("465"),
            row_pitch_mm=_PITCH,
        )
