"""Structured product records for panel sizing (PD-001).

Distinct from ``DocumentChunk`` on purpose. A chunk is a passage of prose with
an embedding, retrieved by similarity and cited in an answer. A product record
is a row of measured facts, looked up by exact match and used as an input to a
calculation. Forcing one into the other's shape would mean inventing a page
number and a section heading for a dimension table, and embedding a width so it
can be found by cosine similarity to a question — neither of which is what
PD-003 does with it.

Both still pass through staging and human verification. The architecture is
shared; the storage shape is not.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, model_validator


class ProductClass(StrEnum):
    """What a catalogue record is, for the purpose of sizing a panel.

    Named for the role the part plays in a calculation rather than for the
    manufacturer's own taxonomy, because the taxonomies differ between vendors
    and the calculations do not.
    """

    #: A cabinet a panel is built into. PD-003 sizes against these.
    ENCLOSURE = "enclosure"
    #: A wall-recessed enclosure. Same role, different mounting.
    FLUSH_ENCLOSURE = "flush-enclosure"
    #: The backplate components mount onto.
    MOUNTING_PLATE = "mounting-plate"
    #: DIN rail. Its length bounds how many modules fit in a row.
    MOUNTING_RAIL = "mounting-rail"
    #: A front panel closing an opening.
    #:
    #: **Excluded from fitting and sizing.** A cover is chosen to match an
    #: enclosure that has already been selected; it does not constrain what
    #: fits inside one, and letting it into the sizing candidate set would
    #: offer a door as somewhere to mount a breaker. Retained in the dataset
    #: so a later BOM-completion step can look up the cover matching a chosen
    #: enclosure. See ``sizing_candidates``.
    COVER = "cover"
    #: Structural brackets and holders.
    BRACKET = "bracket"
    VERTICAL_BRACKET = "vertical-bracket"
    HOLDER = "holder"
    #: A base raising an enclosure off the floor. Adds height, not capacity.
    PEDESTAL = "pedestal"
    #: Busbar mounting hardware.
    BUSBAR_SUPPORT = "busbar-support"


#: The classes PD-003 may size against.
#:
#: An allow-list rather than "everything except covers", so a class added later
#: has to be considered explicitly instead of silently becoming a candidate.
#: A pedestal and a bracket are real parts with real dimensions that are not
#: places to put components either.
SIZING_CLASSES: frozenset[ProductClass] = frozenset(
    {
        ProductClass.ENCLOSURE,
        ProductClass.FLUSH_ENCLOSURE,
        ProductClass.MOUNTING_PLATE,
        ProductClass.MOUNTING_RAIL,
    }
)


class ProductRecord(BaseModel):
    """One catalogue part with its published dimensions.

    Attributes:
        sku: The manufacturer's order number, which is what a BOM quotes.
        manufacturer: Who makes it.
        product_class: Its role in a sizing calculation.
        description: What the catalogue calls it.
        width_mm: External width, when published.
        height_mm: External height, when published.
        depth_mm: External depth, when published.
        source_url: Where the figures can be checked by hand.

    Every dimension is optional and every one that is present is the
    manufacturer's own figure. A part that publishes only a width is stored
    with only a width rather than having the other two inferred — PD-001's
    edge case is explicit that ambiguous or missing dimension data is flagged,
    never guessed at.
    """

    sku: str
    manufacturer: str
    product_class: ProductClass
    description: str
    width_mm: Decimal | None = None
    height_mm: Decimal | None = None
    depth_mm: Decimal | None = None
    source_url: str = ""

    @model_validator(mode="after")
    def _has_at_least_one_dimension(self) -> ProductRecord:
        """Reject a record carrying no dimension at all.

        Returns:
            The validated record.

        Raises:
            ValueError: If width, height and depth are all absent.

        A part with no measurements is not a sizing input; it is a catalogue
        entry that happens to exist. Admitting one would put a row into the
        table PD-003 reads that can never satisfy a fit, and the reason would
        not be visible from the row.
        """
        if self.width_mm is None and self.height_mm is None and self.depth_mm is None:
            raise ValueError(f"product {self.sku!r} publishes no dimension")
        return self

    @property
    def is_sizing_candidate(self) -> bool:
        """Whether PD-003 may size against this part.

        Returns:
            ``True`` for the classes in ``SIZING_CLASSES``.
        """
        return self.product_class in SIZING_CLASSES


def sizing_candidates(records: list[ProductRecord]) -> list[ProductRecord]:
    """Return only the records PD-003 may size against.

    Args:
        records: Every ingested product record.

    Returns:
        Those whose class is a sizing class, in the order given.

    Covers are the largest excluded group and the reason this function exists:
    they are half the ingested set, they carry real dimensions, and they are
    not places to mount anything. Filtering here rather than at ingestion keeps
    them available for BOM completion, which needs the cover that matches a
    chosen enclosure.
    """
    return [record for record in records if record.is_sizing_candidate]


def covers_for_width(records: list[ProductRecord], width_mm: Decimal) -> list[ProductRecord]:
    """Return covers matching a given enclosure width.

    Args:
        records: Every ingested product record.
        width_mm: The chosen enclosure's width.

    Returns:
        Covers whose published width equals it exactly.

    Exact equality, not a tolerance. Catalogue widths are discrete published
    values rather than measurements, so a cover that is "close" to the opening
    is one that does not fit it.
    """
    return [
        record
        for record in records
        if record.product_class is ProductClass.COVER and record.width_mm == width_mm
    ]
