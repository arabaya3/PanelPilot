"""Tests for `app/ingestion/eplan_catalogue.py`.

Mirrors the module 1:1.

The filter's job is to keep a matrix code reader's optical resolution out of
the table PD-003 reads enclosure widths from. So the cases that carry the
weight are the exclusions and the exact values — a row that is wrongly kept
becomes a number a real panel is sized against, and nothing downstream can tell
it apart from a correct one.

Fixtures are copied verbatim from the real export, including the SICK camera
text that motivated the structural test. Values are asserted exactly rather
than by range: "roughly the right size" is not a property this data has.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.eplan_catalogue import (
    MAX_DIMENSION_MM,
    PANEL_CLASSES,
    CatalogueItem,
    classify,
    dimensions_of,
    filter_export,
    summarise,
)

_HEADER = (
    "Quantity,Description from basket,Part number,Manufacturer (abbreviation),"
    "Manufacturer (full name),Type number,Order number,Product group,Description 1,"
    "Description 2,Description 3,Long description,Link to graphic file,"
    "Link to external document 1,Link to external document 2,"
    "Link to external document 3,Relative path of the DXF file\n"
)

#: An ETI enclosure, exactly as the export writes it.
_ETI_ENCLOSURE = (
    '1,,ETI.001327510,ETI,ETI,"HXS400 4-13",001327510,,"Enclosure, HXS400 4-13",'
    '"Solid GSX - Metal enclosures system",,"ETI Code: 001327510\n'
    "Description: HXS400 4-13\n"
    "Class name: Enclosure\n"
    "IP rating: IP55\n"
    "Height (mm): 2000\n"
    "Width (mm): 1050\n"
    'Depth (mm): 400",,https://www.etigroup.eu/products-services/001327510,,,\n'
)

#: A Rittal enclosure, whose dimensions are a triple in prose.
_RITTAL_ENCLOSURE = (
    "1,,RIT.5846600,RIT,Rittal,SE.5846600,5846600,,"
    '"VX SE free-standing enclosure system",,,'
    '"VX SE free-standing enclosure system, Carbon steel, '
    'Width/height/depth: 1800 x 2000 x 500, Number of doors: 2, IP 55, NEMA 12",,'
    "https://www.rittal.com/pdf-creator/variant/uk-en/5846600,,,\n"
)

#: A SICK camera. Its text is full of millimetres, none of them its size.
_SICK_CAMERA = (
    "1,,SICK.1071728,SICK,SICK,V2D652R,1071728,,"
    '"Matrix code reader",,,"Code resolution: 0.1 mm\n'
    "Working range: 300 mm ... 2,200 mm\n"
    'Focal length: 54 mm",,https://cdn.sick.com/x.pdf,,,\n'
)

#: An ETI screw: a real panel accessory that informs no sizing calculation.
_ETI_SCREW = (
    "1,,ETI.001343408,ETI,ETI,AS-SH,001343408,,"
    '"Screw, AS-SH 5x50 SET",,,"ETI Code: 001343408\n'
    "Class name: Screw\n"
    'Dimensions: M5x50mm",,https://www.etigroup.eu/products-services/001343408,,,\n'
)

#: A Rittal enclosure that publishes no dimensions at all.
_RITTAL_NO_DIMS = (
    "1,,RIT.5866600,RIT,Rittal,VX.5866600,5866600,,"
    '"VX SE free-standing enclosure system",,,"VX SE Floormount Enclosure",,'
    "https://www.rittal.com/pdf-creator/variant/us-en/5866600,,,\n"
)


def _export(tmp_path: Path, *rows: str) -> Path:
    """Write a CSV in the portal's own shape, separator lines included."""
    path = tmp_path / "export.csv"
    path.write_text(_HEADER + "#\n#\n" + "".join(rows), encoding="utf-8")
    return path


# --- the exclusion that motivated the whole filter ---------------------------


def test_a_camera_is_excluded_despite_being_full_of_millimetres() -> None:
    """The false positive this module exists to prevent.

    SICK's datasheet says "Code resolution: 0.1 mm" and "Working range: 300 mm
    ... 2,200 mm". Those are optical specifications, not the object's size, and
    a filter testing for the substring "mm" admits 41 such rows.
    """
    row = {"Description 1": "Matrix code reader", "Long description": "Working range: 300 mm"}

    assert classify(row) is None


def test_a_camera_row_is_dropped_end_to_end(tmp_path: Path) -> None:
    report = filter_export(_export(tmp_path, _SICK_CAMERA))

    assert report.kept == ()
    assert report.not_panel == 1


def test_a_screw_is_excluded_even_though_eti_makes_panels(tmp_path: Path) -> None:
    """Manufacturer is not the test; product class is.

    ETI supplies both enclosures and the screws that hold them together, and
    only one of those informs a sizing calculation.
    """
    report = filter_export(_export(tmp_path, _ETI_SCREW))

    assert report.kept == ()
    assert report.not_panel == 1


def test_an_enclosure_with_no_published_dimensions_is_dropped(tmp_path: Path) -> None:
    """Panel-relevant but unusable.

    Seven such Rittal rows are in the real export. Keeping one would put a
    record with no numbers into a table PD-003 reads numbers from.
    """
    report = filter_export(_export(tmp_path, _RITTAL_NO_DIMS))

    assert report.kept == ()
    assert report.no_dimension == 1


# --- exact values, both published formats ------------------------------------


def test_an_eti_enclosures_dimensions_are_read_exactly(tmp_path: Path) -> None:
    """ETI 001327510 (HXS400 4-13): 1050 wide, 2000 high, 400 deep."""
    report = filter_export(_export(tmp_path, _ETI_ENCLOSURE))

    assert len(report.kept) == 1
    item = report.kept[0]
    assert item.width_mm == Decimal("1050")
    assert item.height_mm == Decimal("2000")
    assert item.depth_mm == Decimal("400")


def test_a_rittal_triple_is_read_in_the_right_order(tmp_path: Path) -> None:
    """Rittal 5846600: "Width/height/depth: 1800 x 2000 x 500".

    Order is the whole risk here. Transposing width and height yields three
    numbers that are individually real and collectively describe a different
    enclosure, which no range check would catch.
    """
    report = filter_export(_export(tmp_path, _RITTAL_ENCLOSURE))

    item = report.kept[0]
    assert item.width_mm == Decimal("1800")
    assert item.height_mm == Decimal("2000")
    assert item.depth_mm == Decimal("500")


def test_a_comma_decimal_is_parsed(tmp_path: Path) -> None:
    """The export is European and writes 48,5 rather than 48.5.

    Read as an integer, this becomes 485 -- a tenfold error in the direction of
    an enclosure that appears to fit.
    """
    width, _, _ = dimensions_of("Width (mm): 48,5")

    assert width == Decimal("48.5")


def test_a_labelled_dimension_wins_over_a_triple() -> None:
    """A row carrying both is one where the explicit label is more specific."""
    width, height, depth = dimensions_of(
        "Width/height/depth: 100 x 200 x 300\nWidth (mm): 550\n" "Height (mm): 600\nDepth (mm): 700"
    )

    assert (width, height, depth) == (Decimal("550"), Decimal("600"), Decimal("700"))


def test_a_partial_dimension_set_is_kept(tmp_path: Path) -> None:
    """Covers publish a width and height but no depth.

    Dropping them would lose 130 records from the real export -- half the
    surviving set -- over a field they have no reason to carry.
    """
    width, height, depth = dimensions_of("Width (mm): 550\nHeight (mm): 300")

    assert width == Decimal("550")
    assert height == Decimal("300")
    assert depth is None


# --- the sanity bound ---------------------------------------------------------


def test_an_implausible_dimension_is_refused() -> None:
    """Nothing in a control panel is four metres on a side.

    A larger figure means the pattern matched something that is not a
    dimension, and the row is refused rather than trusted.
    """
    width, _, _ = dimensions_of(f"Width (mm): {int(MAX_DIMENSION_MM) + 1}")

    assert width is not None
    assert width > MAX_DIMENSION_MM


def test_an_implausible_row_is_dropped_and_counted(tmp_path: Path) -> None:
    row = _ETI_ENCLOSURE.replace("Width (mm): 1050", "Width (mm): 99000")
    report = filter_export(_export(tmp_path, row))

    assert report.kept == ()
    assert report.implausible == 1


def test_a_zero_dimension_is_not_a_dimension() -> None:
    """Zero width is a parsing artefact, not a product."""
    width, _, _ = dimensions_of("Width (mm): 0")

    assert width is None


# --- classification -----------------------------------------------------------


def test_the_declared_class_is_used_when_present() -> None:
    row = {"Long description": "Class name: Mounting plate\nWidth (mm): 500"}

    assert classify(row) == "mounting plate"


def test_an_unlisted_class_is_refused() -> None:
    """An allow-list, not a deny-list.

    A deny-list only excludes the irrelevant products someone already thought
    of; the next export will contain different ones.
    """
    row = {"Long description": "Class name: Pocket"}

    assert classify(row) is None


def test_rittal_rows_are_classified_from_their_description() -> None:
    """Rittal publishes no `Class name:` field."""
    row = {"Description 1": "VX SE free-standing enclosure system"}

    assert classify(row) == "enclosure"


def test_a_bare_system_description_does_not_match() -> None:
    """The description phrases are narrow on purpose.

    Matching "system" alone would admit the cable-carrier rows ("TRAXLINE
    SYSTEM M 700 C") and the AS-Interface enclosures.
    """
    assert classify({"Description 1": "PUR-Systemcable-TRAXLINE SYSTEM M 700 C"}) is None


def test_every_allowed_class_is_lowercase() -> None:
    """Classification lowercases before comparing.

    Classification lowercases before comparing, so an uppercase entry in the
    allow-list would silently never match.
    """
    assert all(name == name.lower() for name in PANEL_CLASSES)


# --- the export's own shape ---------------------------------------------------


def test_separator_rows_are_not_data(tmp_path: Path) -> None:
    """The portal writes two `#` lines under the header."""
    report = filter_export(_export(tmp_path, _ETI_ENCLOSURE))

    assert report.total == 1


def test_a_mixed_export_is_split_correctly(tmp_path: Path) -> None:
    """One of each, which is the shape of the real file."""
    report = filter_export(
        _export(tmp_path, _ETI_ENCLOSURE, _SICK_CAMERA, _ETI_SCREW, _RITTAL_NO_DIMS)
    )

    assert report.total == 4
    assert len(report.kept) == 1
    assert report.not_panel == 2
    assert report.no_dimension == 1


def test_the_kept_record_carries_its_provenance(tmp_path: Path) -> None:
    """A dimension with no way back to the manufacturer's page is one a.

    A dimension with no way back to the manufacturer's page is one a
    reviewer cannot check.
    """
    item = filter_export(_export(tmp_path, _ETI_ENCLOSURE)).kept[0]

    assert item.order_number == "001327510"
    assert item.manufacturer == "ETI"
    assert item.source_url.startswith("https://")


def test_summarise_reports_every_outcome(tmp_path: Path) -> None:
    report = filter_export(_export(tmp_path, _ETI_ENCLOSURE, _SICK_CAMERA, _RITTAL_NO_DIMS))
    summary = summarise(report)

    assert summary["total"] == 3
    assert summary["kept"] == 1
    assert summary["dropped_not_panel"] == 1
    assert summary["dropped_no_dimension"] == 1
    assert summary["by_class"] == {"enclosure": 1}


def test_an_empty_export_is_not_an_error(tmp_path: Path) -> None:
    """A search that matched nothing is a real outcome, not a failure."""
    report = filter_export(_export(tmp_path))

    assert report.total == 0
    assert report.kept == ()


def test_a_catalogue_item_is_immutable() -> None:
    """These become inputs to a sizing calculation.

    These become inputs to a sizing calculation; nothing downstream should
    be able to adjust a published dimension in place.
    """
    item = CatalogueItem(
        part_number="X",
        manufacturer="ETI",
        order_number="1",
        description="d",
        product_class="enclosure",
        width_mm=Decimal("500"),
        height_mm=None,
        depth_mm=None,
        source_url="https://example.invalid",
    )

    with pytest.raises(AttributeError):
        item.width_mm = Decimal("600")  # type: ignore[misc]


# --- conversion into product records ------------------------------------------
#
# The filter decides what is usable; this decides what it means. The risk here
# is quieter than a wrong dimension: a class that filters in but has no mapped
# role would be dropped at conversion, which looks from the outside exactly
# like the product not being in the export.


def test_every_allowed_class_has_a_mapped_role() -> None:
    """The two lists must stay exhaustive over each other.

    A class added to `PANEL_CLASSES` without a role here would pass the filter
    and then fail conversion — 'the export did not contain any', with no
    indication the row was seen and discarded.
    """
    from app.ingestion.eplan_catalogue import _CLASS_TO_ROLE

    assert set(PANEL_CLASSES) <= set(_CLASS_TO_ROLE)


def test_an_unmapped_class_raises_rather_than_dropping(tmp_path: Path) -> None:
    """Loudly, because the alternative is a silent gap."""
    from app.core.errors import ValidationError as DomainValidationError
    from app.ingestion.eplan_catalogue import CatalogueItem, to_product_record

    item = CatalogueItem(
        part_number="X",
        manufacturer="ETI",
        order_number="1",
        description="d",
        product_class="something-new",
        width_mm=Decimal("500"),
        height_mm=None,
        depth_mm=None,
        source_url="https://example.invalid",
    )

    with pytest.raises(DomainValidationError, match="no mapped product role"):
        to_product_record(item)


def test_conversion_preserves_the_published_figures(tmp_path: Path) -> None:
    """No rounding and no unit conversion between filter and record."""
    from app.ingestion.eplan_catalogue import product_records

    records, _ = product_records(_export(tmp_path, _ETI_ENCLOSURE))

    assert len(records) == 1
    record = records[0]
    assert record.sku == "001327510"
    assert record.width_mm == Decimal("1050")
    assert record.height_mm == Decimal("2000")
    assert record.depth_mm == Decimal("400")


def test_conversion_carries_provenance(tmp_path: Path) -> None:
    """A dimension a reviewer cannot trace is one they cannot verify."""
    from app.ingestion.eplan_catalogue import product_records

    records, _ = product_records(_export(tmp_path, _ETI_ENCLOSURE))

    assert records[0].manufacturer == "ETI"
    assert records[0].source_url.startswith("https://")


def test_the_report_is_returned_alongside_the_records(tmp_path: Path) -> None:
    """How much was dropped is what tells a reviewer whether the export was.

    How much was dropped is what tells a reviewer whether the export was
    the right one.
    """
    from app.ingestion.eplan_catalogue import product_records

    records, report = product_records(
        _export(tmp_path, _ETI_ENCLOSURE, _SICK_CAMERA, _RITTAL_NO_DIMS)
    )

    assert len(records) == 1
    assert report.total == 3
    assert report.not_panel == 1
    assert report.no_dimension == 1


def test_a_cover_converts_and_is_not_a_sizing_candidate(tmp_path: Path) -> None:
    """The end-to-end shape of the instruction: ingested, tagged, excluded."""
    from app.ingestion.eplan_catalogue import product_records
    from app.models.schemas.products import ProductClass, sizing_candidates

    cover = (
        "1,,ETI.001101439,ETI,ETI,CP,001101439,,"
        '"Cover, CP 2.2-2 F",,,"ETI Code: 001101439\n'
        "Class name: Cover\n"
        "Height (mm): 300\n"
        'Width (mm): 550",,https://www.etigroup.eu/products-services/001101439,,,\n'
    )
    records, _ = product_records(_export(tmp_path, cover))

    assert records[0].product_class is ProductClass.COVER
    assert records[0].width_mm == Decimal("550")
    assert sizing_candidates(list(records)) == []


def test_each_catalogue_class_maps_to_its_own_role() -> None:
    """Pinned per class, not just as a set.

    A mutation mapping "flush mounted enclosure" onto COVER survived every
    other test here: the row still converts, still carries its dimensions, and
    silently stops being something PD-003 can size against. The classes that
    must be sizing candidates are asserted individually for that reason.
    """
    from app.ingestion.eplan_catalogue import _CLASS_TO_ROLE
    from app.models.schemas.products import SIZING_CLASSES, ProductClass

    assert _CLASS_TO_ROLE["enclosure"] is ProductClass.ENCLOSURE
    assert _CLASS_TO_ROLE["flush mounted enclosure"] is ProductClass.FLUSH_ENCLOSURE
    assert _CLASS_TO_ROLE["mounting plate"] is ProductClass.MOUNTING_PLATE
    assert _CLASS_TO_ROLE["mounting rail"] is ProductClass.MOUNTING_RAIL
    assert _CLASS_TO_ROLE["cover"] is ProductClass.COVER

    # And the four that must remain fittable actually are.
    for name in ("enclosure", "flush mounted enclosure", "mounting plate", "mounting rail"):
        assert _CLASS_TO_ROLE[name] in SIZING_CLASSES, f"{name} stopped being sizeable"
