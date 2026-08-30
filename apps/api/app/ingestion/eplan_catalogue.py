"""Filter an EPLAN Data Portal export down to panel-sizing-relevant records.

A Data Portal search returns whatever matched the search term, not whatever is
useful. The export this was built against ran on "enclosure system" and came
back with 487 rows, of which roughly half are products that have nothing to do
with sizing a panel: vision sensors, fire-suppression cartridges, cable
carriers, PCB headers. Ingesting them uniformly would put a matrix code
reader's *optical resolution* into the same table PD-003 reads enclosure widths
from.

**Two independent tests, and a row must pass both.**

1. *It publishes a real physical dimension.* Not "the text contains mm" — that
   admits 41 rows in this export alone, led by SICK cameras whose datasheets
   say "Code resolution: 0.1 mm" and "Working range: 300 mm ... 2,200 mm".
   Those are optical specifications measured in millimetres, which is a
   different thing from an object's size. So the test requires a *structured*
   dimension: a labelled `Width (mm):`, `Height (mm):`, `Depth (mm):`, or a
   `Width/height/depth:` / `WHD:` triple.

2. *It is a panel component.* Checked against an allow-list of classes rather
   than a deny-list of contaminants, because a deny-list only excludes the
   irrelevant products someone already thought of, and the next export will
   have different ones.

Both tests are conservative in the same direction: a row that cannot be
confidently classified is dropped, not admitted. A missing enclosure is a gap
someone notices when a size is unavailable; a wrong one is a number PD-003
sizes a real panel from.

**This module extracts, it does not verify.** A surviving record still carries
the manufacturer's own figures and still enters staging for human review like
any other ingested content. What the filter guarantees is only that the number
in the `width_mm` field is the object's width.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.models.schemas.products import ProductClass, ProductRecord

#: Product classes that can inform panel sizing.
#:
#: An allow-list, deliberately. The export contained twelve manufacturers and
#: at least six product families that are irrelevant here; enumerating what IS
#: relevant is stable, while enumerating what is not would need editing every
#: time a search term pulls in a new category.
PANEL_CLASSES: frozenset[str] = frozenset(
    {
        "enclosure",
        "flush mounted enclosure",
        "mounting plate",
        "mounting rail",
        "cover",
        "vertical bracket",
        "bracket",
        "holder",
        "pedestal",
        "busbar support",
        "insert holder",
        "separating element",
    }
)

#: Phrases in `Description 1` that mark a panel product when no class is given.
#:
#: Rittal's rows carry no `Class name:` field, so they are matched on their
#: product-line description instead. Kept narrow: "enclosure system" and
#: "baying enclosure" are unambiguous, where a bare "system" would match the
#: cable-carrier and AS-Interface rows.
PANEL_DESCRIPTIONS: tuple[str, ...] = (
    "enclosure system",
    "baying enclosure",
    "compact enclosure",
)

#: Labelled single dimensions, e.g. `Width (mm): 1050`.
_LABELLED = re.compile(r"(Width|Height|Depth)\s*\(mm\)\s*:\s*(\d+(?:[.,]\d+)?)", re.I)

#: A W x H x D triple, e.g. `Width/height/depth: 1800 x 2000 x 500`.
_TRIPLE = re.compile(
    r"(?:Width/height/depth|WHD)\s*:\s*"
    # Both separators: the portal writes ASCII 'x' in the Rittal rows seen so
    # far, but U+00D7 appears in European catalogue text, and a miss would
    # silently drop the whole triple rather than fail loudly.
    r"(\d+(?:[.,]\d+)?)\s*[x\u00d7]\s*(\d+(?:[.,]\d+)?)\s*[x\u00d7]\s*(\d+(?:[.,]\d+)?)",
    re.I,
)

#: `Class name: Enclosure`, as ETI writes it in its long description.
_CLASS = re.compile(r"Class name\s*:\s*([^\n]+)")

#: The largest dimension any row may claim, in millimetres.
#:
#: Nothing in a control panel is four metres on a side. A larger figure means
#: the pattern matched something that is not a dimension -- a part number, a
#: pressure rating, a wavelength -- and the row is refused rather than trusted.
MAX_DIMENSION_MM = Decimal("4000")


@dataclass(frozen=True)
class CatalogueItem:
    """One panel component with its published dimensions.

    Attributes:
        part_number: The portal's part identifier.
        manufacturer: Manufacturer's full name.
        order_number: The manufacturer's own order code.
        description: What the product is.
        product_class: Normalised class, e.g. ``enclosure``.
        width_mm: Width in millimetres, when published.
        height_mm: Height in millimetres, when published.
        depth_mm: Depth in millimetres, when published.
        source_url: Where the figures can be checked.
    """

    part_number: str
    manufacturer: str
    order_number: str
    description: str
    product_class: str
    width_mm: Decimal | None
    height_mm: Decimal | None
    depth_mm: Decimal | None
    source_url: str


@dataclass(frozen=True)
class FilterReport:
    """What a filtering run kept and what it dropped, and why.

    Attributes:
        kept: The records that passed both tests.
        no_dimension: Rows with no structured dimension.
        not_panel: Rows whose class is not panel-relevant.
        implausible: Rows whose dimension failed the sanity bound.
        total: How many data rows were examined.
    """

    kept: tuple[CatalogueItem, ...]
    no_dimension: int
    not_panel: int
    implausible: int
    total: int


def _number(raw: str) -> Decimal | None:
    """Parse a dimension, accepting the comma decimal separator.

    Args:
        raw: The matched digits, e.g. ``48,5``.

    Returns:
        The value, or ``None`` if it is not a usable number.
    """
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def dimensions_of(text: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Extract width, height and depth from a record's text.

    Args:
        text: The row's combined text.

    Returns:
        ``(width, height, depth)``, each ``None`` when not published.

    The labelled form wins over the triple when both appear. ETI publishes
    labelled fields and Rittal publishes triples, so in practice they do not
    collide -- but a row carrying both is one where the explicit label is the
    more specific statement.
    """
    width = height = depth = None

    for axis, raw in _LABELLED.findall(text):
        value = _number(raw)
        if value is None:
            continue
        axis = axis.lower()
        if axis == "width" and width is None:
            width = value
        elif axis == "height" and height is None:
            height = value
        elif axis == "depth" and depth is None:
            depth = value

    if width is None and height is None and depth is None:
        triple = _TRIPLE.search(text)
        if triple is not None:
            width = _number(triple.group(1))
            height = _number(triple.group(2))
            depth = _number(triple.group(3))

    return width, height, depth


def classify(row: dict[str, str]) -> str | None:
    """Return a row's panel-component class, or ``None`` if it is not one.

    Args:
        row: One export record.

    Returns:
        The normalised class name, or ``None`` when the product is not
        panel-relevant.

    Classification reads the manufacturer's own `Class name:` field where one
    exists rather than inferring from prose. Inference is what admits a matrix
    code reader on the strength of the word "mounting".
    """
    declared = _CLASS.search(row.get("Long description") or "")
    if declared is not None:
        name = declared.group(1).strip().lower()
        return name if name in PANEL_CLASSES else None

    description = (row.get("Description 1") or "").lower()
    for phrase in PANEL_DESCRIPTIONS:
        if phrase in description:
            return "enclosure"
    return None


def _plausible(*values: Decimal | None) -> bool:
    """Report whether every published dimension is within the sanity bound."""
    return all(value <= MAX_DIMENSION_MM for value in values if value is not None)


def filter_export(path: Path) -> FilterReport:
    """Filter one EPLAN export down to usable panel components.

    Args:
        path: The exported CSV.

    Returns:
        The surviving records and per-reason drop counts.

    Raises:
        OSError: If the file cannot be read.

    Rows are dropped rather than repaired. A record whose dimensions cannot be
    read is not a record with unknown dimensions -- it is a record this code
    did not understand, and guessing at it is how a cable carrier's bend radius
    becomes an enclosure width.
    """
    kept: list[CatalogueItem] = []
    no_dimension = not_panel = implausible = total = 0

    for row in _rows(path):
        total += 1
        text = "\n".join(value for value in row.values() if value)

        product_class = classify(row)
        if product_class is None:
            not_panel += 1
            continue

        width, height, depth = dimensions_of(text)
        if width is None and height is None and depth is None:
            no_dimension += 1
            continue

        if not _plausible(width, height, depth):
            implausible += 1
            continue

        kept.append(
            CatalogueItem(
                part_number=(row.get("Part number") or "").strip(),
                manufacturer=(row.get("Manufacturer (full name)") or "").strip(),
                order_number=(row.get("Order number") or "").strip(),
                description=(row.get("Description 1") or "").strip(),
                product_class=product_class,
                width_mm=width,
                height_mm=height,
                depth_mm=depth,
                source_url=(row.get("Link to external document 1") or "").strip(),
            )
        )

    return FilterReport(
        kept=tuple(kept),
        no_dimension=no_dimension,
        not_panel=not_panel,
        implausible=implausible,
        total=total,
    )


def _rows(path: Path) -> Iterator[dict[str, str]]:
    """Yield the export's real data rows.

    Args:
        path: The exported CSV.

    Yields:
        Each record, skipping the ``#`` separator lines the portal emits.

    A large field limit is set because a single product's long description can
    run to several kilobytes -- the SICK datasheets in this export exceed the
    csv module's default and would otherwise raise mid-file.
    """
    csv.field_size_limit(10_000_000)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            part = (row.get("Part number") or "").strip()
            if part and part != "#":
                yield {key: (value or "") for key, value in row.items() if key}


#: Maps the portal's class names onto the roles PD-003 reasons about.
#:
#: Exhaustive over ``PANEL_CLASSES`` by construction, and a test asserts it: an
#: entry added to the allow-list without a mapping here would otherwise be
#: filtered in and then silently dropped at conversion, which looks identical
#: to the product not being in the export at all.
_CLASS_TO_ROLE: dict[str, ProductClass] = {
    "enclosure": ProductClass.ENCLOSURE,
    "flush mounted enclosure": ProductClass.FLUSH_ENCLOSURE,
    "mounting plate": ProductClass.MOUNTING_PLATE,
    "mounting rail": ProductClass.MOUNTING_RAIL,
    "cover": ProductClass.COVER,
    "bracket": ProductClass.BRACKET,
    "vertical bracket": ProductClass.VERTICAL_BRACKET,
    "holder": ProductClass.HOLDER,
    "pedestal": ProductClass.PEDESTAL,
    "busbar support": ProductClass.BUSBAR_SUPPORT,
    "insert holder": ProductClass.HOLDER,
    "separating element": ProductClass.BRACKET,
}


def to_product_record(item: CatalogueItem) -> ProductRecord:
    """Convert a filtered catalogue row into a product record.

    Args:
        item: A row that passed both filter tests.

    Returns:
        The record, carrying the manufacturer's own figures unchanged.

    Raises:
        ValidationError: If the class has no mapped role, or the row carries no
            dimension. Neither can happen for a row that came through
            ``filter_export`` -- both are guarded there -- so either means the
            two have drifted apart, which is worth failing on rather than
            absorbing.

    No unit conversion and no rounding. The portal publishes millimetres and
    PD-003 works in millimetres; a conversion step here would be a place for a
    factor to be wrong in a way no test of this function would notice.
    """
    role = _CLASS_TO_ROLE.get(item.product_class)
    if role is None:
        raise ValidationError(
            f"catalogue class {item.product_class!r} has no mapped product role; "
            "PANEL_CLASSES and _CLASS_TO_ROLE have drifted apart"
        )

    try:
        return ProductRecord(
            sku=item.order_number,
            manufacturer=item.manufacturer,
            product_class=role,
            description=item.description,
            width_mm=item.width_mm,
            height_mm=item.height_mm,
            depth_mm=item.depth_mm,
            source_url=item.source_url,
        )
    except PydanticValidationError as exc:
        raise ValidationError(
            f"catalogue row {item.order_number!r} is not a usable record: {exc}"
        ) from exc


def product_records(path: Path) -> tuple[tuple[ProductRecord, ...], FilterReport]:
    """Filter an export and convert what survives into product records.

    Args:
        path: The exported CSV.

    Returns:
        The records, and the filtering report they came from.

    Raises:
        ValidationError: If a surviving row cannot be converted.

    The report is returned alongside rather than discarded: how many rows were
    dropped, and for which reason, is the number a reviewer needs to decide
    whether an export was the right one.
    """
    report = filter_export(path)
    return tuple(to_product_record(item) for item in report.kept), report


def summarise(report: FilterReport) -> dict[str, Any]:
    """Return a report as counts, for logging or a CLI.

    Args:
        report: A filtering run's result.

    Returns:
        Totals by outcome and by product class.
    """
    by_class: dict[str, int] = {}
    for item in report.kept:
        by_class[item.product_class] = by_class.get(item.product_class, 0) + 1

    return {
        "total": report.total,
        "kept": len(report.kept),
        "dropped_not_panel": report.not_panel,
        "dropped_no_dimension": report.no_dimension,
        "dropped_implausible": report.implausible,
        "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
    }
