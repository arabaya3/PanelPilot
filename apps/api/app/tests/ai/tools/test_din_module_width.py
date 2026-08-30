"""Tests for `app/ai/tools/din_module_width.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Every width below is quoted from the manufacturer datasheet it names, read from
the PDF rather than from a search result or a distributor listing. That is the
acceptance criterion: "looked-up module widths match published manufacturer
datasheets exactly". A test written from the same understanding that produced
the table would prove the two agree, not that either is right.

The cases carrying the most weight are the refusals. An enclosure sized from a
guessed width does not close, and that failure appears at the panel rather than
in this suite — so a missing figure has to raise here, loudly, instead of
returning something plausible.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.ai.tools import din_module_width
from app.ai.tools.din_module_width import (
    WIDTHS,
    ComponentCategory,
    ModuleWidth,
    modular_spacings_for,
    module_width_for,
    sourced_series,
)
from app.core.errors import ValidationError

# --- values, against the datasheets they came from ---------------------------


def test_an_abb_s200_mcb_is_one_module_of_17_5_mm() -> None:
    """ABB S201-C63, order code 2CDS251001R0634.

    Dimensions block, verbatim: "Width in Number of Modular Spacings: 1",
    "Product Net Width: 17.5 mm".
    """
    assert module_width_for(ComponentCategory.MCB, series="ABB S200") == Decimal("17.5")
    assert modular_spacings_for(ComponentCategory.MCB, series="ABB S200") == 1


def test_a_three_pole_abb_mcb_is_52_5_mm() -> None:
    """ABB S203-C32, order code 2CDS253001R0324: 3 spacings, 52.5 mm.

    The published 3-pole figure, not three times the 1-pole figure computed
    here — which is the point. It happens to be exactly 3x, and that is a fact
    read from a second datasheet rather than an assumption this module makes.
    """
    assert module_width_for(ComponentCategory.MCB, series="ABB S200", poles=3) == Decimal("52.5")
    assert modular_spacings_for(ComponentCategory.MCB, series="ABB S200", poles=3) == 3


def test_a_schneider_rcbo_is_18_mm() -> None:
    """Schneider Acti9 iC60H RCBO, "Dimensions (mm)" drawing: 18 mm wide.

    Eighteen, not seventeen and a half. Both conform to DIN 43880, which
    specifies a band rather than a single value, and this pair is why the table
    stores millimetres per series instead of a module count times one global
    constant.
    """
    assert module_width_for(ComponentCategory.RCBO) == Decimal("18")


def test_a_wago_terminal_block_is_5_2_mm() -> None:
    """WAGO 2002-1201, wago.com: "5.2 mm wide"."""
    assert module_width_for(ComponentCategory.TERMINAL_BLOCK) == Decimal("5.2")


def test_the_two_vendors_module_widths_actually_differ() -> None:
    """Pinned as its own assertion because it is the design's whole premise.

    If these ever converge, storing per-series millimetres is over-engineering
    and someone should simplify it. While they differ, a single constant is
    wrong for one vendor — and an enclosure sized on 17.5 mm rows that is
    filled with 18 mm devices does not close.
    """
    abb = module_width_for(ComponentCategory.MCB, series="ABB S200")
    schneider = module_width_for(ComponentCategory.RCBO)

    assert abb != schneider


# --- width does not vary with rated current ----------------------------------


def test_rated_current_is_not_a_parameter() -> None:
    """The specification asks for keying by rated current "where it varies".

    It does not vary: ABB publishes 17.5 mm for both the 16 A S201-C16 and the
    63 A S201-C63. Accepting a current argument that changed nothing would
    imply a precision the datasheets do not support, so the signature has none.
    """
    parameters = inspect.signature(module_width_for).parameters

    assert "rating" not in parameters
    assert "rated_current_a" not in parameters


# --- the refusals, which are the safety-relevant half ------------------------


def test_an_unsourced_component_type_raises() -> None:
    """The edge case the specification names.

    "A component type with no known module width must raise clearly at lookup
    time, never silently default to an assumed value." Contactors are the live
    example: every manufacturer-hosted contactor datasheet reachable from here
    returned 403, and distributor listings are not a source.
    """
    unsourced = ComponentCategory.MCB
    original = din_module_width.WIDTHS
    din_module_width.WIDTHS = tuple(entry for entry in original if entry.category is not unsourced)
    try:
        with pytest.raises(ValidationError, match="no sourced module width"):
            module_width_for(unsourced)
    finally:
        din_module_width.WIDTHS = original


def test_an_unknown_series_raises_and_names_what_is_known() -> None:
    """So a caller with a typo is corrected rather than left guessing."""
    with pytest.raises(ValidationError, match="ABB S200"):
        module_width_for(ComponentCategory.MCB, series="ABB S999")


def test_zero_poles_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        module_width_for(ComponentCategory.MCB, series="ABB S200", poles=0)


def test_negative_poles_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        module_width_for(ComponentCategory.MCB, series="ABB S200", poles=-2)


def test_a_multipole_width_is_refused_where_the_source_does_not_license_it() -> None:
    """Schneider's drawing gives one width and no module count.

    Multiplying it by three would assert a 3-pole part whose datasheet nobody
    read — the exact shape of the error this table exists to avoid.
    """
    with pytest.raises(ValidationError, match="modular spacing"):
        module_width_for(ComponentCategory.RCBO, poles=3)


def test_a_terminal_blocks_module_count_is_refused() -> None:
    """5.2 mm is not a multiple or division of 17.5 or 18.

    Converting it to "0.3 modules" would be arithmetic with no source behind
    it, and a caller laying out a rail needs to know the device is not measured
    that way.
    """
    with pytest.raises(ValidationError, match="not specified in modular spacings"):
        modular_spacings_for(ComponentCategory.TERMINAL_BLOCK)


def test_an_ambiguous_category_refuses_rather_than_picking_one() -> None:
    """Two series in one category, differing in width.

    Defaulting to the first would attribute one vendor's dimension to another
    vendor's device.
    """
    extra = ModuleWidth(
        category=ComponentCategory.MCB,
        series="Other MCB",
        width_mm=Decimal("18"),
        modular_spacings=1,
        source="test double, not a real datasheet",
    )
    original = din_module_width.WIDTHS
    din_module_width.WIDTHS = (*original, extra)
    try:
        with pytest.raises(ValidationError, match="more than one sourced series"):
            module_width_for(ComponentCategory.MCB)
    finally:
        din_module_width.WIDTHS = original


# --- the table's own integrity ------------------------------------------------


def test_every_entry_names_its_source() -> None:
    """A width with no document behind it is exactly what this task forbids."""
    for entry in WIDTHS:
        assert entry.source.strip(), f"{entry.series} has no source"
        assert len(entry.source) > 40, f"{entry.series}'s source is too vague to check"


def test_every_width_is_positive() -> None:
    for entry in WIDTHS:
        assert entry.width_mm > 0


def test_no_series_appears_twice() -> None:
    names = [entry.series for entry in WIDTHS]

    assert len(names) == len(set(names))


def test_module_pitch_entries_sit_inside_the_din_43880_band() -> None:
    """DIN 43880 puts one module between 17.5 mm and 18.0 mm inclusive.

    A module-pitch entry outside that band is either a transcription error or a
    device that does not belong in this category — both worth failing on.
    Entries with no declared spacing are exempt: terminal blocks are genuinely
    not module devices.
    """
    for entry in WIDTHS:
        if entry.modular_spacings is None:
            continue
        per_module = entry.width_mm / entry.modular_spacings
        assert (
            Decimal("17.5") <= per_module <= Decimal("18")
        ), f"{entry.series} is {per_module} mm per module, outside DIN 43880"


def test_sourced_series_lists_what_exists() -> None:
    assert sourced_series(ComponentCategory.MCB) == ("ABB S200",)


def test_sourced_series_is_empty_when_nothing_is_sourced() -> None:
    """An empty category lists nothing rather than raising.

    A caller checking availability gets an empty tuple rather than an
    exception, so it can fall back without catching.
    """
    original = din_module_width.WIDTHS
    din_module_width.WIDTHS = ()
    try:
        assert sourced_series(ComponentCategory.MCB) == ()
    finally:
        din_module_width.WIDTHS = original
