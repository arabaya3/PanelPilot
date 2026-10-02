"""Variable frequency drive selection calculations.

Pure functions. Each formula cites the manufacturer guide it came from.

A drive range is one series as its own manual tabulates it: the output
current of each type for each supply band, and the ambient and altitude
deratings. The default is the ABB ACS880-01 wall-mounted range, IP21: types
...-3 on 380-415 V and ...-5 on 415-500 V (IEC ratings at Un = 400 and
500 V), and ...-7 on 525-600 V (UL ratings at Un = 575 V) and 660-690 V (IEC
ratings at Un = 690 V). The other manufacturers' ranges are in
``drive_ranges``, each transcribed from its manual with page citations. A
voltage, site or current a range's manual does not tabulate is refused for
that range rather than read from another one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.calculations import AppliedFactor, DutyClass, VfdSelectionResult
from app.models.schemas.search import Citation

HARDWARE_MANUAL_ID = "abb-3AUA0000078093"
HARDWARE_MANUAL_TITLE = "ACS880-01 drives hardware manual"
GUIDE_7_ID = "abb-3AFE64362569"
GUIDE_7_TITLE = "Technical guide No. 7: Dimensioning of a drive system"

#: PDF pages of the hardware manual's derating sections.
_DERATING_PAGES = (243, 244)

#: Ambient limits for full current and for derated current, °C.
_FULL_CURRENT_UP_TO_C = Decimal(40)
_MAX_AMBIENT_C = Decimal(55)
_MIN_AMBIENT_C = Decimal(-15)

#: Altitude: full current to 1000 m, then 1 percentage point per 100 m, to 4000 m.
_FULL_CURRENT_UP_TO_M = Decimal(1000)
_MAX_ALTITUDE_M = Decimal(4000)

_SQRT_3 = Decimal(3).sqrt()


@dataclass(frozen=True)
class _Rating:
    """One row of a ratings table."""

    type_code: str
    frame: str | None
    #: Continuous output current for normal (light-overload) duty; ABB's I2.
    nominal_a: str
    #: Continuous current allowing 150 % for 60 s (ABB's IHd: 50 % overload
    #: for 1 min every 5 min); ``None`` where the manual gives no such
    #: rating for this type.
    heavy_duty_a: str | None
    #: PDF page of the row, where the table runs over several pages.
    page: int | None = None


#: ACS880-01, Un = 400 V, IEC ratings (hardware manual pp. 234-235). The
#: 293A, 430A and 490A types carry footnoted heavy-duty currents allowing
#: 30 %, 25 % and 40 % overload rather than 50 %; they are not offered for
#: heavy duty.
_RATINGS_400V: tuple[_Rating, ...] = (
    _Rating("ACS880-01-02A4-3", "R1", "2.4", "1.8"),
    _Rating("ACS880-01-03A3-3", "R1", "3.3", "2.4"),
    _Rating("ACS880-01-04A0-3", "R1", "4.0", "3.3"),
    _Rating("ACS880-01-05A6-3", "R1", "5.6", "4.0"),
    _Rating("ACS880-01-07A2-3", "R1", "8.0", "5.6"),
    _Rating("ACS880-01-09A4-3", "R1", "10.0", "8.0"),
    _Rating("ACS880-01-12A6-3", "R1", "12.9", "10.0"),
    _Rating("ACS880-01-017A-3", "R2", "17", "12.6"),
    _Rating("ACS880-01-025A-3", "R2", "25", "17"),
    _Rating("ACS880-01-032A-3", "R3", "32", "25"),
    _Rating("ACS880-01-038A-3", "R3", "38", "32"),
    _Rating("ACS880-01-045A-3", "R4", "45", "38"),
    _Rating("ACS880-01-061A-3", "R4", "61", "45"),
    _Rating("ACS880-01-072A-3", "R5", "72", "61"),
    _Rating("ACS880-01-087A-3", "R5", "87", "72"),
    _Rating("ACS880-01-105A-3", "R6", "105", "87"),
    _Rating("ACS880-01-145A-3", "R6", "145", "105"),
    _Rating("ACS880-01-169A-3", "R7", "169", "145"),
    _Rating("ACS880-01-206A-3", "R7", "206", "169"),
    _Rating("ACS880-01-246A-3", "R8", "246", "206"),
    _Rating("ACS880-01-293A-3", "R8", "293", None),
    _Rating("ACS880-01-363A-3", "R9", "363", "293"),
    _Rating("ACS880-01-430A-3", "R9", "430", None),
    _Rating("ACS880-01-490A-3", "R9", "490", None),
    _Rating("ACS880-01-595A-3", "R9e", "595", "505"),
    _Rating("ACS880-01-670A-3", "R9e", "670", "595"),
)

#: ACS880-01-xxxx-5, Un = 500 V, IEC ratings (hardware manual pp. 236-237).
#: The manual tabulates these types at Un = 400 V too, with the same I2 and
#: IHd. 260A, 414A and 477A carry footnoted heavy-duty currents (30 %, 25 %
#: and 40 % overload) and are not offered for heavy duty.
_RATINGS_500V: tuple[_Rating, ...] = (
    _Rating("ACS880-01-02A1-5", "R1", "2.1", "1.7"),
    _Rating("ACS880-01-03A0-5", "R1", "3.0", "2.1"),
    _Rating("ACS880-01-03A4-5", "R1", "3.4", "3.0"),
    _Rating("ACS880-01-04A8-5", "R1", "4.8", "3.4"),
    _Rating("ACS880-01-05A2-5", "R1", "5.2", "4.8"),
    _Rating("ACS880-01-07A6-5", "R1", "7.6", "5.2"),
    _Rating("ACS880-01-11A0-5", "R1", "11.0", "7.6"),
    _Rating("ACS880-01-014A-5", "R2", "14", "11"),
    _Rating("ACS880-01-021A-5", "R2", "21", "14"),
    _Rating("ACS880-01-027A-5", "R3", "27", "21"),
    _Rating("ACS880-01-034A-5", "R3", "34", "27"),
    _Rating("ACS880-01-040A-5", "R4", "40", "34"),
    _Rating("ACS880-01-052A-5", "R4", "52", "40"),
    _Rating("ACS880-01-065A-5", "R5", "65", "52"),
    _Rating("ACS880-01-077A-5", "R5", "77", "65"),
    _Rating("ACS880-01-096A-5", "R6", "96", "77"),
    _Rating("ACS880-01-124A-5", "R6", "124", "96"),
    _Rating("ACS880-01-156A-5", "R7", "156", "124"),
    _Rating("ACS880-01-180A-5", "R7", "180", "156"),
    _Rating("ACS880-01-240A-5", "R8", "240", "180"),
    _Rating("ACS880-01-260A-5", "R8", "260", None),
    _Rating("ACS880-01-361A-5", "R9", "361", "302"),
    _Rating("ACS880-01-414A-5", "R9", "414", None),
    _Rating("ACS880-01-477A-5", "R9", "477", None),
    _Rating("ACS880-01-585A-5", "R9e", "585", "505"),
    _Rating("ACS880-01-635A-5", "R9e", "635", "585"),
)

#: ACS880-01-xxxx-7, Un = 690 V, IEC ratings (hardware manual pp. 237-238).
_RATINGS_690V: tuple[_Rating, ...] = (
    _Rating("ACS880-01-07A4-7", "R3", "7.4", "5.6"),
    _Rating("ACS880-01-09A9-7", "R3", "9.9", "7.4"),
    _Rating("ACS880-01-14A3-7", "R3", "14.3", "9.9"),
    _Rating("ACS880-01-019A-7", "R3", "19", "14.3"),
    _Rating("ACS880-01-023A-7", "R3", "23", "19"),
    _Rating("ACS880-01-027A-7", "R3", "27", "23"),
    _Rating("ACS880-01-035A-7", "R5", "35", "26"),
    _Rating("ACS880-01-042A-7", "R5", "42", "35"),
    _Rating("ACS880-01-049A-7", "R5", "49", "42"),
    _Rating("ACS880-01-061A-7", "R6", "61", "49"),
    _Rating("ACS880-01-084A-7", "R6", "84", "61"),
    _Rating("ACS880-01-098A-7", "R7", "98", "84"),
    _Rating("ACS880-01-119A-7", "R7", "119", "98"),
    _Rating("ACS880-01-142A-7", "R8", "142", "119"),
    _Rating("ACS880-01-174A-7", "R8", "174", "142"),
    _Rating("ACS880-01-210A-7", "R9", "210", "174"),
    _Rating("ACS880-01-271A-7", "R9", "271", "210"),
)


#: ACS880-01-xxxx-7, UL (NEC) ratings at Un = 575 V (hardware manual
#: pp. 240-241): the only ratings the manual prints for a -7 drive below 690 V.
#: They give no I2, so the nominal column holds ILd -- a continuous current
#: that still allows 10 % overload, so it is never more than the drive
#: delivers without overload. 271A's IHd is footnoted (30 % overload) and is
#: not offered for heavy duty.
_RATINGS_575V: tuple[_Rating, ...] = (
    _Rating("ACS880-01-07A4-7", "R3", "7.0", "5.6"),
    _Rating("ACS880-01-09A9-7", "R3", "9.4", "7.4"),
    _Rating("ACS880-01-14A3-7", "R3", "13.6", "9.9"),
    _Rating("ACS880-01-019A-7", "R3", "18", "14.3"),
    _Rating("ACS880-01-023A-7", "R3", "22", "19"),
    _Rating("ACS880-01-027A-7", "R3", "27", "23"),
    _Rating("ACS880-01-035A-7", "R5", "41", "32"),
    _Rating("ACS880-01-042A-7", "R5", "52", "41"),
    _Rating("ACS880-01-049A-7", "R5", "52", "41"),
    _Rating("ACS880-01-061A-7", "R6", "62", "52"),
    _Rating("ACS880-01-084A-7", "R6", "77", "62"),
    _Rating("ACS880-01-098A-7", "R7", "99", "77"),
    _Rating("ACS880-01-119A-7", "R7", "125", "99"),
    _Rating("ACS880-01-142A-7", "R8", "144", "125"),
    _Rating("ACS880-01-174A-7", "R8", "180", "144"),
    _Rating("ACS880-01-210A-7", "R9", "242", "192"),
    _Rating("ACS880-01-271A-7", "R9", "271", None),
)


@dataclass(frozen=True)
class _Derating:
    """A linear output-current derating, as a manual states it.

    Full current up to ``full_up_to``, then ``percent_per_step`` percent per
    ``step`` to ``limit``. A manual that prints a curve or per-type table
    instead has ``percent_per_step`` ``None``: the range is then offered only
    where it carries full current, rather than read off a curve.
    """

    full_up_to: Decimal
    percent_per_step: Decimal | None
    step: Decimal
    limit: Decimal
    floor: Decimal
    page: int
    section: str


@dataclass(frozen=True)
class _Catalogue:
    """One voltage range: its supply band, ratings and where they are printed."""

    #: The type-code suffix, as the manual's type designation gives it.
    suffix: str
    low_v: Decimal
    high_v: Decimal
    ratings: tuple[_Rating, ...]
    page: int
    section: str


#: The supply bands, per the manual's type designation (p. 39): -3 380...415 V,
#: -5 380...500 V, -7 525...690 V. -3 keeps 380-415 V, as before; -5 covers
#: the rest of its range, where its I2 is the same at the two tabulated
#: voltages. -7 is rated by IEC at Un = 690 V, offered in the 660-690 V band
#: the manual groups with 690 V (brake chopper table, p. 348), and by UL at
#: Un = 575 V, offered on 525-600 V, the band the same table groups with it.
#: 600-660 V has neither and is refused.
_CATALOGUES: tuple[_Catalogue, ...] = (
    _Catalogue(
        "-3", Decimal(380), Decimal(415), _RATINGS_400V, 234, "Electrical ratings, IEC, Un = 400 V"
    ),
    _Catalogue(
        "-5", Decimal(415), Decimal(500), _RATINGS_500V, 236, "Electrical ratings, IEC, Un = 500 V"
    ),
    _Catalogue(
        "-7",
        Decimal(525),
        Decimal(600),
        _RATINGS_575V,
        240,
        "Electrical ratings, UL (NEC), Un = 575 V, ILd as the normal-duty rating",
    ),
    _Catalogue(
        "-7", Decimal(660), Decimal(690), _RATINGS_690V, 237, "Electrical ratings, IEC, Un = 690 V"
    ),
)


@dataclass(frozen=True)
class DriveRange:
    """One drive series, as its manual tabulates it.

    Attributes:
        key: Stable identifier, e.g. ``abb-acs880-01``.
        manufacturer: As the manufacturer names itself.
        series: The series name.
        document_id: The manual's reference.
        document_title: The manual's title.
        catalogues: Ratings per supply band.
        temperature: Surrounding-air derating, °C.
        altitude: Altitude derating, m.
        notes: What the transcription leaves out, for a reader of the code.
    """

    key: str
    manufacturer: str
    series: str
    document_id: str
    document_title: str
    catalogues: tuple[_Catalogue, ...]
    temperature: _Derating
    altitude: _Derating
    notes: tuple[str, ...] = field(default=())


#: The default range. Its deratings: 1 % per °C above +40 °C to +55 °C for
#: IP21 frames R1-R9e (p. 243 as printed), and 1 percentage point per 100 m
#: above 1000 m to 4000 m (p. 244).
ACS880_01 = DriveRange(
    key="abb-acs880-01",
    manufacturer="ABB",
    series="ACS880-01",
    document_id=HARDWARE_MANUAL_ID,
    document_title=HARDWARE_MANUAL_TITLE,
    catalogues=_CATALOGUES,
    temperature=_Derating(
        _FULL_CURRENT_UP_TO_C,
        Decimal(1),
        Decimal(1),
        _MAX_AMBIENT_C,
        _MIN_AMBIENT_C,
        _DERATING_PAGES[0],
        "Surrounding air temperature",
    ),
    altitude=_Derating(
        _FULL_CURRENT_UP_TO_M,
        Decimal(1),
        Decimal(100),
        _MAX_ALTITUDE_M,
        Decimal(0),
        _DERATING_PAGES[1],
        "Altitude derating",
    ),
)

DEFAULT_RANGE = ACS880_01.key


def _ranges() -> dict[str, DriveRange]:
    """Every range, the default first."""
    from app.ai.tools.drive_ranges import RANGES

    return {ACS880_01.key: ACS880_01, **{r.key: r for r in RANGES}}


def _range(key: str | None) -> DriveRange:
    """Look up a range, refusing an unknown key."""
    ranges = _ranges()
    found = ranges.get(key or DEFAULT_RANGE)
    if found is None:
        raise ValidationError(f"no drive range {key!r}; known ranges: {', '.join(sorted(ranges))}")
    return found


@dataclass(frozen=True)
class RangeSummary:
    """What a page needs to offer a range.

    Attributes:
        key: As ``select_frame`` takes it.
        manufacturer: The manufacturer.
        series: The series.
        bands: Supply bands, as ``(low_v, high_v)``.
        heavy_duty: Whether any type has a heavy-duty rating.
        source: The manual.
    """

    key: str
    manufacturer: str
    series: str
    bands: tuple[tuple[Decimal, Decimal], ...]
    heavy_duty: bool
    source: Citation


def available_ranges() -> list[RangeSummary]:
    """List the drive ranges selection can choose from.

    Source:
        Each range's own manual: the ABB ACS880-01 hardware manual
        (3AUA0000078093) and the manuals named in ``drive_ranges``.

    Returns:
        One summary per range, the default first, then by manufacturer.
    """
    ranges = list(_ranges().values())
    ordered = [ranges[0], *sorted(ranges[1:], key=lambda r: (r.manufacturer, r.series))]
    return [
        RangeSummary(
            key=r.key,
            manufacturer=r.manufacturer,
            series=r.series,
            bands=tuple((c.low_v, c.high_v) for c in r.catalogues),
            heavy_duty=any(x.heavy_duty_a for c in r.catalogues for x in c.ratings),
            source=_cite(r, r.catalogues[0].page, r.catalogues[0].section),
        )
        for r in ordered
    ]


@dataclass(frozen=True)
class _Fuse:
    """One row of the ultrarapid (aR) stud-mount fuse table."""

    amps: str
    bussmann: str
    din_size: str
    #: The installation's minimum prospective short-circuit current, A, for
    #: the fuse to operate fast enough (footnote 1).
    min_short_circuit_a: str
    page: int


#: ACS880-01 input fuses, ultrarapid (aR) DIN 43653 stud-mount, one per phase
#: (hardware manual pp. 258-262). The manual allows aR fuses for every frame,
#: recommends them for R7-R9 and allows only them for R9e, so one table
#: covers the catalogue. Values as printed -- 180A-5 is listed at 315 A with
#: a 170M3018, which elsewhere in the table is 350 A.
_AR_FUSES: dict[str, _Fuse] = {
    "ACS880-01-02A4-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-03A3-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-04A0-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-05A6-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-07A2-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-09A4-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-12A6-3": _Fuse("25", "170M1311", "000", "75", 259),
    "ACS880-01-017A-3": _Fuse("40", "170M1313", "000", "140", 259),
    "ACS880-01-025A-3": _Fuse("40", "170M1313", "000", "140", 259),
    "ACS880-01-032A-3": _Fuse("63", "170M1315", "000", "250", 259),
    "ACS880-01-038A-3": _Fuse("63", "170M1315", "000", "250", 259),
    "ACS880-01-045A-3": _Fuse("80", "170M1316", "000", "310", 259),
    "ACS880-01-061A-3": _Fuse("100", "170M1317", "000", "450", 259),
    "ACS880-01-072A-3": _Fuse("125", "170M1318", "000", "590", 259),
    "ACS880-01-087A-3": _Fuse("160", "170M1319", "000", "800", 259),
    "ACS880-01-105A-3": _Fuse("200", "170M3015", "1", "810", 259),
    "ACS880-01-145A-3": _Fuse("250", "170M3016", "1", "1100", 260),
    "ACS880-01-169A-3": _Fuse("315", "170M3017", "1", "1400", 260),
    "ACS880-01-206A-3": _Fuse("350", "170M3018", "1", "1750", 260),
    "ACS880-01-246A-3": _Fuse("450", "170M5009", "2", "2100", 260),
    "ACS880-01-293A-3": _Fuse("500", "170M5010", "2", "2400", 260),
    "ACS880-01-363A-3": _Fuse("630", "170M5012", "2", "3400", 260),
    "ACS880-01-430A-3": _Fuse("700", "170M5013", "2", "4100", 260),
    "ACS880-01-490A-3": _Fuse("700", "170M5013", "2", "4100", 260),
    "ACS880-01-595A-3": _Fuse("1000", "170M6014", "3", "6500", 260),
    "ACS880-01-670A-3": _Fuse("1000", "170M6014", "3", "6500", 260),
    "ACS880-01-02A1-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-03A0-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-03A4-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-04A8-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-05A2-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-07A6-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-11A0-5": _Fuse("25", "170M1308", "000", "32", 260),
    "ACS880-01-014A-5": _Fuse("40", "170M1313", "000", "140", 260),
    "ACS880-01-021A-5": _Fuse("40", "170M1313", "000", "140", 260),
    "ACS880-01-027A-5": _Fuse("63", "170M1315", "000", "250", 260),
    "ACS880-01-034A-5": _Fuse("63", "170M1315", "000", "250", 260),
    "ACS880-01-040A-5": _Fuse("80", "170M1316", "000", "310", 260),
    "ACS880-01-052A-5": _Fuse("100", "170M1317", "000", "450", 260),
    "ACS880-01-065A-5": _Fuse("125", "170M1318", "000", "590", 260),
    "ACS880-01-077A-5": _Fuse("160", "170M1319", "000", "800", 260),
    "ACS880-01-096A-5": _Fuse("200", "170M3015", "1", "810", 260),
    "ACS880-01-124A-5": _Fuse("250", "170M3016", "1", "1100", 260),
    "ACS880-01-156A-5": _Fuse("315", "170M3017", "1", "1400", 261),
    "ACS880-01-180A-5": _Fuse("315", "170M3018", "1", "1750", 261),
    "ACS880-01-240A-5": _Fuse("400", "170M5008", "2", "1800", 261),
    "ACS880-01-260A-5": _Fuse("450", "170M5009", "2", "2100", 261),
    "ACS880-01-302A-5": _Fuse("550", "170M5011", "2", "3000", 261),
    "ACS880-01-361A-5": _Fuse("630", "170M5012", "2", "3400", 261),
    "ACS880-01-414A-5": _Fuse("700", "170M5013", "2", "4100", 261),
    "ACS880-01-477A-5": _Fuse("700", "170M5013", "2", "4100", 261),
    "ACS880-01-585A-5": _Fuse("1000", "170M6014", "3", "6500", 261),
    "ACS880-01-635A-5": _Fuse("1000", "170M6014", "3", "6500", 261),
    "ACS880-01-07A4-7": _Fuse("16", "170M1309", "000", "45", 261),
    "ACS880-01-09A9-7": _Fuse("20", "170M1310", "000", "59", 261),
    "ACS880-01-14A3-7": _Fuse("32", "170M1312", "000", "105", 261),
    "ACS880-01-019A-7": _Fuse("40", "170M1313", "000", "140", 261),
    "ACS880-01-023A-7": _Fuse("50", "170M1314", "000", "180", 261),
    "ACS880-01-027A-7": _Fuse("50", "170M1314", "000", "180", 261),
    "ACS880-01-035A-7": _Fuse("63", "170M1315", "000", "250", 261),
    "ACS880-01-042A-7": _Fuse("80", "170M1316", "000", "310", 261),
    "ACS880-01-049A-7": _Fuse("80", "170M1316", "000", "310", 261),
    "ACS880-01-061A-7": _Fuse("125", "170M1318", "000", "590", 261),
    "ACS880-01-084A-7": _Fuse("160", "170M1319", "000", "800", 261),
    "ACS880-01-098A-7": _Fuse("200", "170M3015", "1", "810", 261),
    "ACS880-01-119A-7": _Fuse("200", "170M3015", "1", "810", 261),
    "ACS880-01-142A-7": _Fuse("250", "170M3016", "1", "1100", 261),
    "ACS880-01-174A-7": _Fuse("315", "170M3017", "1", "1400", 261),
    "ACS880-01-210A-7": _Fuse("400", "170M5008", "2", "1800", 261),
    "ACS880-01-271A-7": _Fuse("450", "170M5009", "2", "2100", 262),
}


@dataclass(frozen=True)
class InputFuse:
    """The fuses a drive's supply needs.

    Attributes:
        amps: Fuse rating, A.
        bussmann: Bussmann type.
        din_size: DIN 43653 size.
        min_short_circuit_a: The installation's minimum prospective
            short-circuit current for the fuse to operate fast enough, A.
        source: Where the row is printed.
    """

    amps: str
    bussmann: str
    din_size: str
    min_short_circuit_a: str
    source: Citation


def input_fuse(*, type_code: str) -> InputFuse:
    """Return the input fuse the manual lists for a drive type.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Fuses (IEC)",
        ultrarapid (aR) fuses DIN 43653 stud-mount, pp. 258-262.

    Args:
        type_code: The drive, as `select_frame` names it (without the frame).

    Returns:
        The fuse, one per phase.

    Raises:
        ValidationError: If the manual lists no fuse for the type.
    """
    row = _AR_FUSES.get(type_code)
    if row is None:
        raise ValidationError(f"no input fuse is listed for {type_code}")
    return InputFuse(
        amps=row.amps,
        bussmann=row.bussmann,
        din_size=row.din_size,
        min_short_circuit_a=row.min_short_circuit_a,
        source=_manual(row.page, "Fuses (IEC), aR fuses DIN 43653 stud-mount"),
    )


def _manual(page: int, section: str) -> Citation:
    """Cite a page of the ACS880-01 hardware manual."""
    return _cite(ACS880_01, page, section)


def _cite(drive_range: DriveRange, page: int, section: str) -> Citation:
    """Cite a page of a range's manual."""
    return Citation(
        document_id=drive_range.document_id,
        document_title=drive_range.document_title,
        manufacturer=drive_range.manufacturer,
        page=page,
        section=section,
    )


def _require_fraction(name: str, value: Decimal) -> None:
    """Refuse anything outside (0, 1]."""
    if not value.is_finite() or value <= 0 or value > 1:
        raise ValidationError(f"{name} must be in (0, 1], got {value}")


def _catalogue(supply_voltage_v: Decimal, drive_range: DriveRange = ACS880_01) -> _Catalogue:
    """Return the band rated for a supply, or refuse one no band is."""
    if supply_voltage_v.is_finite():
        for catalogue in drive_range.catalogues:
            if catalogue.low_v <= supply_voltage_v <= catalogue.high_v:
                return catalogue
    bands = ", ".join(
        f"{c.low_v}-{c.high_v} V" + (f" ({c.suffix})" if c.suffix else "")
        for c in drive_range.catalogues
    )
    raise ValidationError(
        f"{supply_voltage_v} V is outside the {drive_range.series} supply ranges tabulated "
        f"here: {bands}; other voltages are not tabulated here",
        code="drive_voltage",
        params={"voltage": supply_voltage_v, "series": drive_range.series, "bands": bands},
    )


def _derate(value: Decimal, rule: _Derating, *, what: str, unit: str) -> Decimal:
    """Apply one linear derating to a value already checked against its limits."""
    if value <= rule.full_up_to:
        return Decimal(1)
    if rule.percent_per_step is None:
        raise ValidationError(
            f"{what} {value} {unit} is above {rule.full_up_to} {unit}, where the manual's "
            "derating is a curve rather than a rate; it is not read off here"
        )
    return 1 - (value - rule.full_up_to) / rule.step * rule.percent_per_step / Decimal(100)


def required_drive_current_a(
    *,
    motor_power_kw: Decimal,
    supply_voltage_v: Decimal,
    motor_efficiency: Decimal,
    motor_power_factor: Decimal,
    duty_class: DutyClass,
) -> Decimal:
    """Compute the motor's rated current, which the drive must supply.

    I = P / (√3 U η cos φ), from P_in = √3 U I cos φ (3.15) and η = P_out /
    P_in (3.16). The duty class does not change the current: it selects which
    of the manual's ratings the current is compared with (`select_frame`),
    and the heavy-duty rating already carries the 150 % overload allowance.

    Source:
        ABB Technical guide No. 7 (3AFE64362569), "Motor power", formulas
        3.15 and 3.16, and Example 3.4.

    Args:
        motor_power_kw: Motor shaft rating, in kilowatts.
        supply_voltage_v: Line-to-line supply voltage, in volts.
        motor_efficiency: Motor efficiency, 0 < η ≤ 1.
        motor_power_factor: Motor power factor at rated load, 0 < pf ≤ 1.
        duty_class: Normal or heavy duty; accepted for the call's symmetry.

    Returns:
        The motor current, in amperes.

    Raises:
        ValidationError: If a quantity is not positive, or efficiency or power
            factor is outside (0, 1].
    """
    del duty_class
    if not motor_power_kw.is_finite() or motor_power_kw <= 0:
        raise ValidationError(f"motor_power_kw must be positive, got {motor_power_kw}")
    if not supply_voltage_v.is_finite() or supply_voltage_v <= 0:
        raise ValidationError(f"supply_voltage_v must be positive, got {supply_voltage_v}")
    _require_fraction("motor_efficiency", motor_efficiency)
    _require_fraction("motor_power_factor", motor_power_factor)
    return (motor_power_kw * 1000) / (
        _SQRT_3 * supply_voltage_v * motor_efficiency * motor_power_factor
    )


def altitude_derate(*, altitude_m: Decimal, drive_range: str | None = None) -> Decimal:
    """Return the output-current derating factor for installation altitude.

    For the default range: unity to 1000 m, then one percentage point per
    100 m. The manual allows the derating to be reduced below 40 °C ambient;
    that relief is not applied, so the factor is never kinder than the
    manual's. Other ranges apply their own manual's rule.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Altitude derating",
        p. 244 as printed; maximum installation altitude 4000 m. Other
        ranges: the page their ``altitude`` rule cites.

    Args:
        altitude_m: Installation altitude above sea level, in metres.
        drive_range: The range; the default when ``None``.

    Returns:
        The derating factor, in the range (0, 1].

    Raises:
        ValidationError: If the altitude is off the range's rule.
    """
    rule = _range(drive_range).altitude
    if not altitude_m.is_finite() or altitude_m < rule.floor or altitude_m > rule.limit:
        raise ValidationError(
            f"altitude {altitude_m} m is outside {rule.floor}-{rule.limit} m, the maximum "
            "permitted installation altitude"
        )
    return _derate(altitude_m, rule, what="altitude", unit="m")


def temperature_derate(*, ambient_temp_c: Decimal, drive_range: str | None = None) -> Decimal:
    """Return the output-current derating factor for surrounding air temperature.

    For the default range: unity to +40 °C, then 1 % per added degree to
    +55 °C. Other ranges apply their own manual's rule.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Surrounding air
        temperature derating", p. 243 as printed, for IP21 frames R1-R9e.
        Other ranges: the page their ``temperature`` rule cites.

    Args:
        ambient_temp_c: Surrounding air temperature, in °C.
        drive_range: The range; the default when ``None``.

    Returns:
        The derating factor.

    Raises:
        ValidationError: If the temperature is off the range's rule.
    """
    rule = _range(drive_range).temperature
    if not ambient_temp_c.is_finite() or ambient_temp_c < rule.floor or ambient_temp_c > rule.limit:
        raise ValidationError(
            f"ambient {ambient_temp_c} °C is outside the drive's range "
            f"({rule.floor}...+{rule.limit} °C)",
            code="drive_ambient",
            params={"ambient": ambient_temp_c, "low": rule.floor, "high": rule.limit},
        )
    return _derate(ambient_temp_c, rule, what="ambient", unit="°C")


def select_frame(
    *,
    required_current_a: Decimal,
    supply_voltage_v: Decimal,
    duty_class: DutyClass,
    altitude_m: Decimal,
    ambient_temp_c: Decimal,
    drive_range: str | None = None,
) -> VfdSelectionResult:
    """Select the smallest catalogue drive whose derated current meets the demand.

    Each type's rating for the duty -- I2 for normal, IHd for heavy -- is
    multiplied by the temperature and altitude factors, and the first type at
    or above the required current is chosen.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Electrical ratings",
        IEC ratings at Un = 400 V, 500 V and 690 V (pp. 234-238 as printed)
        and UL ratings at Un = 575 V (pp. 240-241),
        with the deratings on pp. 243-244. Other ranges: their manual's
        ratings tables and derating pages, as ``drive_ranges`` cites them.

    Args:
        required_current_a: Continuous current the motor demands, in amperes.
        supply_voltage_v: Line-to-line supply voltage, in volts.
        duty_class: Normal or heavy duty.
        altitude_m: Installation altitude, in metres.
        ambient_temp_c: Ambient temperature at the drive, in °C.
        drive_range: The range to choose from; the default when ``None``.

    Returns:
        The selected type with its derated current and every factor applied.

    Raises:
        ValidationError: If no range is rated for the supply, the site is off
            the derating curves, or no catalogue type covers the demand.
    """
    if not required_current_a.is_finite() or required_current_a <= 0:
        raise ValidationError(f"required_current_a must be positive, got {required_current_a}")
    chosen = _range(drive_range)
    catalogue = _catalogue(supply_voltage_v, chosen)
    k_temp = temperature_derate(ambient_temp_c=ambient_temp_c, drive_range=chosen.key)
    k_alt = altitude_derate(altitude_m=altitude_m, drive_range=chosen.key)
    factor = k_temp * k_alt

    # The smallest rating that carries the demand, not the first in the
    # table: a manual's heavy-duty column need not rise with its normal one.
    best: tuple[Decimal, _Rating] | None = None
    for rating in catalogue.ratings:
        rated = rating.nominal_a if duty_class is DutyClass.NORMAL else rating.heavy_duty_a
        if rated is None:
            continue
        derated = Decimal(rated) * factor
        if derated >= required_current_a and (best is None or derated < best[0]):
            best = (derated, rating)
    if best is not None:
        derated, rating = best
        return VfdSelectionResult(
            frame_reference=(
                f"{rating.type_code} ({rating.frame})" if rating.frame else rating.type_code
            ),
            rated_output_current_a=derated,
            applied_factors=[
                AppliedFactor(
                    name=f"temperature {ambient_temp_c} °C",
                    value=k_temp,
                    source=_cite(chosen, chosen.temperature.page, chosen.temperature.section),
                ),
                AppliedFactor(
                    name=f"altitude {altitude_m} m",
                    value=k_alt,
                    source=_cite(chosen, chosen.altitude.page, chosen.altitude.section),
                ),
            ],
            drive_range=chosen.key,
            manufacturer=chosen.manufacturer,
            series=chosen.series,
        )
    needed = required_current_a.quantize(Decimal("0.1"))
    if chosen is ACS880_01:
        raise ValidationError(
            f"no ACS880-01-xxxx{catalogue.suffix} type supplies {needed} A "
            f"for {duty_class.value} duty at this site; consider a cabinet-built ACS880",
            code="no_drive_type",
            params={"current": needed},
        )
    raise ValidationError(
        f"no {chosen.manufacturer} {chosen.series} type supplies {needed} A for "
        f"{duty_class.value} duty at {supply_voltage_v} V on this site",
        code="no_drive_type",
        params={"current": needed},
    )


def ratings_citation(supply_voltage_v: Decimal, drive_range: str | None = None) -> Citation:
    """Cite the ratings table for a supply.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Electrical ratings",
        or the ratings table of the range's own manual.

    Args:
        supply_voltage_v: The supply `select_frame` was given.
        drive_range: The range; the default when ``None``.

    Returns:
        The citation.

    Raises:
        ValidationError: If the range is not rated for the supply.
    """
    chosen = _range(drive_range)
    catalogue = _catalogue(supply_voltage_v, chosen)
    return _cite(chosen, catalogue.page, catalogue.section)


def motor_current_citation() -> Citation:
    """Cite the motor-current formula.

    Source:
        ABB Technical guide No. 7 (3AFE64362569), formulas 3.15-3.16.

    Returns:
        The citation.
    """
    return Citation(
        document_id=GUIDE_7_ID,
        document_title=GUIDE_7_TITLE,
        manufacturer="ABB",
        page=13,
        section="Motor power, formulas 3.15-3.16",
    )
