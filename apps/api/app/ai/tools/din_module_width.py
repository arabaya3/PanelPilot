"""DIN-rail module width reference data (PD-002).

The width a device occupies on a DIN rail is the number that turns enclosure
sizing (PD-003) into a calculation rather than a guess. Every figure here comes
from a manufacturer datasheet that was downloaded and read, and each entry names
its document and order code so a reviewer can check it without reading this
code.

**There is no single "module width", and this module refuses to pretend there
is.** DIN 43880 defines a module as a *band* rather than one value: a device's
installation width lies between 17.5 mm and 18.0 mm, or a multiple of half of
either. That is not a technicality. Read from their own datasheets:

* ABB S200 (MCB): 1 modular spacing = **17.5 mm**
* Schneider Acti9 iC60H (RCBO): **18.0 mm**

Both are conforming. A table that stored "1 module" and multiplied by a single
constant would be wrong for one vendor or the other, and wrong in the direction
that matters — an enclosure sized with 17.5 mm rows that is filled with 18 mm
devices does not close. So widths are stored **in millimetres, per series**,
and module counts are recorded only where the manufacturer states one.

**Two device families do not follow module pitch at all.** Terminal blocks
(Wago TOPJOB S: 5.2 mm) and contactors (frame sizes, not modules) are sized
independently of the 17.5/18 mm band. Storing them as module counts would
produce a plausible number with no basis, which is the failure this sourcing
discipline exists to prevent.

**What is deliberately absent.** Contactors are not in the table: every
manufacturer-hosted contactor datasheet reachable from here returned 403, and
the widths that circulate on distributor listings are not a citable source. A
lookup for one raises rather than returning a guess. See the tracker.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.core.errors import ValidationError


class ComponentCategory(StrEnum):
    """A device family with its own width convention.

    Named for what decides the width, not for what the device does. An MCB and
    an RCBO both sit on module pitch; a terminal block and a contactor do not,
    and that distinction is the one a caller has to get right.
    """

    #: Miniature circuit breaker. Module-pitch device.
    MCB = "mcb"
    #: Residual-current breaker with overcurrent protection. Module-pitch.
    RCBO = "rcbo"
    #: Rail-mounted feed-through terminal block. NOT module pitch.
    TERMINAL_BLOCK = "terminal-block"


@dataclass(frozen=True)
class ModuleWidth:
    """One device series' width on the rail.

    Attributes:
        category: Which device family this is.
        series: Manufacturer and series, as printed on the datasheet.
        width_mm: Width of ONE pole or one unit, in millimetres, exactly as
            the datasheet states it.
        modular_spacings: Module count the manufacturer declares for a single
            pole, or ``None`` for a family that does not use module pitch.
            Recorded rather than derived: ABB prints this field on its
            datasheets, and computing it from the width would be inventing a
            figure the source does not give.
        source: The document the figure was read from, with its order code.
    """

    category: ComponentCategory
    series: str
    width_mm: Decimal
    modular_spacings: int | None
    source: str


#: Every width this module will vouch for.
#:
#: Each was downloaded as a PDF and read; the mm figure is quoted from the
#: document's own dimensions block, not from a search result or a distributor
#: listing. Deliberately small: a short table of numbers that are right is
#: worth more here than a long one that is mostly plausible.
WIDTHS: tuple[ModuleWidth, ...] = (
    ModuleWidth(
        category=ComponentCategory.MCB,
        series="ABB S200",
        width_mm=Decimal("17.5"),
        modular_spacings=1,
        source=(
            "ABB S201-C63 datasheet, order code 2CDS251001R0634, Dimensions: "
            '"Width in Number of Modular Spacings: 1", "Product Net Width: 17.5 mm". '
            "Confirmed identical on S201-C16 (2CDS251001R0164), so width does not "
            "vary with rated current within the series."
        ),
    ),
    ModuleWidth(
        category=ComponentCategory.RCBO,
        series="Schneider Acti9 iC60H RCBO",
        width_mm=Decimal("18"),
        # Schneider's dimension drawing gives millimetres and does not state a
        # module count, so none is recorded. 18 mm is one module at the top of
        # DIN 43880's band, but writing "1" here would be our inference, not
        # the manufacturer's statement.
        modular_spacings=None,
        source=(
            "Schneider Electric, Acti9 iC60H RCBO 10, 30 and 100 mA datasheet, "
            '"Dimensions (mm)" drawing: 18 mm wide x 110 mm high x 45 mm deep (1P+N).'
        ),
    ),
    ModuleWidth(
        category=ComponentCategory.TERMINAL_BLOCK,
        series="Wago TOPJOB S 2002-1201",
        width_mm=Decimal("5.2"),
        # Not a module-pitch device. 5.2 mm is not a multiple or division of
        # 17.5 or 18, and treating it as "0.3 modules" would be arithmetic
        # with no source behind it.
        modular_spacings=None,
        source=(
            "WAGO 2002-1201 product data, wago.com: "
            '"TOPJOB S feedthrough terminal block; rail mount; 2-conductor; '
            '5.2 mm wide; gray".'
        ),
    ),
)


def _lookup(category: ComponentCategory, series: str | None) -> ModuleWidth:
    """Find one series' entry.

    Args:
        category: The device family.
        series: Exact series name, or ``None`` when the category has only one
            entry.

    Returns:
        The matching entry.

    Raises:
        ValidationError: If nothing matches, or if a category holds more than
            one series and the caller did not say which.
    """
    candidates = [entry for entry in WIDTHS if entry.category is category]
    if not candidates:
        raise ValidationError(
            f"no sourced module width for {category.value}; "
            "add one only with a manufacturer datasheet behind it"
        )

    if series is not None:
        for entry in candidates:
            if entry.series == series:
                return entry
        known = ", ".join(sorted(entry.series for entry in candidates))
        raise ValidationError(
            f"no sourced module width for series {series!r}; known {category.value} "
            f"series: {known}"
        )

    if len(candidates) > 1:
        # Refused rather than defaulting to the first. ABB's 17.5 mm and
        # Schneider's 18 mm are both correct for their own parts, so picking
        # one for a caller who did not choose would silently attribute one
        # vendor's dimension to another's device.
        known = ", ".join(sorted(entry.series for entry in candidates))
        raise ValidationError(
            f"{category.value} has more than one sourced series and they differ in "
            f"width; name one of: {known}"
        )

    return candidates[0]


def module_width_for(
    component_type: ComponentCategory,
    *,
    series: str | None = None,
    poles: int = 1,
) -> Decimal:
    """Return the rail width one device occupies, in millimetres.

    Args:
        component_type: The device family.
        series: Manufacturer series, e.g. ``"ABB S200"``. Required when the
            category holds more than one sourced series.
        poles: Number of poles. Width scales linearly with pole count for
            module-pitch devices, which is verified rather than assumed: ABB
            publishes 1 spacing / 17.5 mm for the 1-pole S201 and 3 spacings /
            52.5 mm for the 3-pole S203, exactly 3x.

    Returns:
        The width in millimetres.

    Raises:
        ValidationError: If no sourced width exists for the component type, if
            the series is ambiguous or unknown, if ``poles`` is not positive,
            or if a multi-pole width is requested for a family whose datasheets
            do not license that scaling.

    Source:
        Per entry in ``WIDTHS``; each names its datasheet and order code.
        ABB S201-C63 (2CDS251001R0634) and S201-C16 (2CDS251001R0164),
        "Product Net Width: 17.5 mm"; ABB S203-C32 (2CDS253001R0324),
        "3 modular spacings, 52.5 mm", which is what licenses the pole
        multiplication below. Schneider Electric Acti9 iC60H RCBO datasheet,
        "Dimensions (mm)": 18 mm. WAGO 2002-1201 product data: 5.2 mm.
        The 17.5-18.0 mm module band is DIN 43880 (Installationseinbaugeraete;
        Huellmasse und zugehoerige Einbaumasse), 1988-12.

    Rated current is deliberately not a parameter. The task specification asks
    for the table to be keyed by it "where it varies", and for MCBs it does
    not: ABB's S201-C16 and S201-C63 are both 17.5 mm despite a fourfold
    current difference. Accepting a parameter that changes nothing would
    suggest a precision the sources do not support.
    """
    if poles < 1:
        raise ValidationError(f"poles must be at least 1, got {poles}")

    entry = _lookup(component_type, series)

    if poles == 1:
        return entry.width_mm

    if entry.modular_spacings is None:
        # Only licensed where the manufacturer states a module count. A
        # terminal block has no pole count in this sense, and multiplying
        # Schneider's 18 mm by three would assert a 3-pole part we have not
        # read a datasheet for.
        raise ValidationError(
            f"{entry.series} does not publish a modular spacing, so a "
            f"{poles}-pole width cannot be derived from the sourced data"
        )

    return entry.width_mm * poles


def modular_spacings_for(
    component_type: ComponentCategory,
    *,
    series: str | None = None,
    poles: int = 1,
) -> int:
    """Return how many DIN modules a device occupies.

    Args:
        component_type: The device family.
        series: Manufacturer series; required when the category is ambiguous.
        poles: Number of poles.

    Returns:
        The module count.

    Raises:
        ValidationError: If the manufacturer does not publish a module count
            for this series, or if ``poles`` is not positive.

    Source:
        ABB publishes a "Width in Number of Modular Spacings" field on its
        S200 datasheets: 1 for the single-pole S201 (2CDS251001R0634), 3 for
        the three-pole S203 (2CDS253001R0324). No other series in ``WIDTHS``
        declares a module count, which is why this raises for them rather than
        converting a millimetre figure.

    Separate from ``module_width_for`` because not every device has one. A
    caller laying out a rail in module units needs to know when the answer is
    "this device is not measured that way" rather than receiving a converted
    number that looks equivalent and is not.
    """
    if poles < 1:
        raise ValidationError(f"poles must be at least 1, got {poles}")

    entry = _lookup(component_type, series)
    if entry.modular_spacings is None:
        raise ValidationError(
            f"{entry.series} is not specified in modular spacings; "
            f"its width is {entry.width_mm} mm"
        )
    return entry.modular_spacings * poles


def sourced_series(component_type: ComponentCategory) -> tuple[str, ...]:
    """List the series with a sourced width for one category.

    Args:
        component_type: The device family.

    Returns:
        The series names, sorted. Empty when nothing is sourced.

    Source:
        Reads ``WIDTHS``; every entry there names the datasheet its width was
        read from. This function adds no figure of its own.
    """
    return tuple(sorted(entry.series for entry in WIDTHS if entry.category is component_type))
