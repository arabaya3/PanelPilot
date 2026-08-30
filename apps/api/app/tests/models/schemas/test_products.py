"""Tests for `app/models/schemas/products.py`.

Mirrors the module 1:1.

Two properties carry the weight here, and both are about what must NOT reach a
sizing calculation.

A **cover** is half the ingested catalogue, carries real published dimensions,
and is a door — not somewhere to mount a breaker. It has to stay in the dataset
so a BOM step can find the cover matching a chosen enclosure, and stay out of
the candidate set PD-003 fits components into. Those two requirements pull in
opposite directions, which is why the split is a function with its own tests
rather than a filter applied at ingestion.

A **record with no dimensions** is not a sizing input at all. Admitting one
puts a row into the table PD-003 reads that can never satisfy a fit, for a
reason invisible from the row itself.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.schemas.products import (
    SIZING_CLASSES,
    ProductClass,
    ProductRecord,
    covers_for_width,
    sizing_candidates,
)


def _record(
    *,
    sku: str = "X1",
    product_class: ProductClass = ProductClass.ENCLOSURE,
    width: str | None = "550",
    height: str | None = "2000",
    depth: str | None = "400",
) -> ProductRecord:
    return ProductRecord(
        sku=sku,
        manufacturer="ETI",
        product_class=product_class,
        description="test part",
        width_mm=Decimal(width) if width is not None else None,
        height_mm=Decimal(height) if height is not None else None,
        depth_mm=Decimal(depth) if depth is not None else None,
        source_url="https://example.invalid/x",
    )


# --- covers: kept in the dataset, kept out of sizing -------------------------


def test_a_cover_is_not_a_sizing_candidate() -> None:
    """The instruction this split exists to honour.

    A cover is chosen to match an enclosure already selected. Offering one as
    somewhere to fit components would propose mounting a breaker on a door.
    """
    assert _record(product_class=ProductClass.COVER).is_sizing_candidate is False


def test_covers_are_excluded_from_the_candidate_set() -> None:
    records = [
        _record(sku="ENC", product_class=ProductClass.ENCLOSURE),
        _record(sku="COV", product_class=ProductClass.COVER),
    ]

    assert [r.sku for r in sizing_candidates(records)] == ["ENC"]


def test_covers_remain_in_the_dataset() -> None:
    """Excluded from sizing is not excluded from ingestion.

    Filtering them out at ingestion would be simpler and would make the BOM
    step impossible: there would be no cover to look up.
    """
    records = [
        _record(sku="ENC", product_class=ProductClass.ENCLOSURE, width="550"),
        _record(sku="COV", product_class=ProductClass.COVER, width="550"),
    ]

    assert len(records) == 2
    assert covers_for_width(records, Decimal("550"))[0].sku == "COV"


def test_a_cover_is_matched_to_an_enclosure_width() -> None:
    records = [
        _record(sku="COV-550", product_class=ProductClass.COVER, width="550"),
        _record(sku="COV-750", product_class=ProductClass.COVER, width="750"),
    ]

    assert [r.sku for r in covers_for_width(records, Decimal("750"))] == ["COV-750"]


def test_a_near_miss_width_does_not_match() -> None:
    """Exact equality, not a tolerance.

    Catalogue widths are discrete published values, not measurements. A cover
    that is "close" to the opening is a cover that does not fit it.
    """
    records = [_record(sku="COV", product_class=ProductClass.COVER, width="550")]

    assert covers_for_width(records, Decimal("551")) == []


def test_only_covers_are_returned_by_the_cover_lookup() -> None:
    """An enclosure of the same width is not a cover for it."""
    records = [
        _record(sku="ENC", product_class=ProductClass.ENCLOSURE, width="550"),
        _record(sku="COV", product_class=ProductClass.COVER, width="550"),
    ]

    assert [r.sku for r in covers_for_width(records, Decimal("550"))] == ["COV"]


# --- what else is and is not a sizing candidate ------------------------------


@pytest.mark.parametrize(
    "product_class",
    sorted(SIZING_CLASSES, key=str),
    ids=str,
)
def test_every_sizing_class_is_a_candidate(product_class: ProductClass) -> None:
    assert _record(product_class=product_class).is_sizing_candidate is True


@pytest.mark.parametrize(
    "product_class",
    [
        ProductClass.COVER,
        ProductClass.BRACKET,
        ProductClass.VERTICAL_BRACKET,
        ProductClass.HOLDER,
        ProductClass.PEDESTAL,
        ProductClass.BUSBAR_SUPPORT,
    ],
    ids=str,
)
def test_non_sizing_classes_are_not_candidates(product_class: ProductClass) -> None:
    """Brackets.

    Brackets, pedestals and busbar supports carry real dimensions and are
    not places to put components either. The allow-list is not a cover-specific
    special case.
    """
    assert _record(product_class=product_class).is_sizing_candidate is False


def test_the_sizing_allow_list_is_a_subset_of_the_classes() -> None:
    """A typo'd member would silently never match anything."""
    assert set(ProductClass) >= SIZING_CLASSES


# --- dimension completeness ---------------------------------------------------


def test_a_record_with_no_dimension_is_refused() -> None:
    """PD-001's edge case: missing dimension data is flagged, never guessed."""
    with pytest.raises(ValidationError, match="publishes no dimension"):
        _record(width=None, height=None, depth=None)


def test_a_width_only_record_is_accepted() -> None:
    """Covers and rails publish a width and often no depth.

    Requiring all three would discard most of the real catalogue over a field
    those parts have no reason to carry.
    """
    record = _record(width="550", height=None, depth=None)

    assert record.width_mm == Decimal("550")
    assert record.depth_mm is None


def test_dimensions_are_kept_exact() -> None:
    """No rounding, no unit conversion.

    The portal publishes millimetres and PD-003 works in millimetres; anything
    in between is a place for a factor to be wrong.
    """
    record = _record(width="48.5")

    assert record.width_mm == Decimal("48.5")
