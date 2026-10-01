"""Motor starter selection: circuit-breaker, contactor and overload relay.

Pure functions. Each table cites the manufacturer guide it came from.

The coordination tables of ABB's *Electrical installation handbook* Vol. 2
(1SDC010001D0204), §3.3 "Protection and switching of motors": Type 2
coordination at 400, 440, 500 and 690 V and 50 kA prospective short-circuit
current, for a three-phase squirrel-cage motor. Rows the handbook marks as
Type 1 only are left out. Another voltage, a higher fault level, or a motor
past the last row is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.calculations import StartType
from app.models.schemas.search import Citation

HANDBOOK_ID = "abb-1SDC010001D0204"
HANDBOOK_TITLE = "Electrical installation handbook, Vol. 2: Electrical devices (4th ed., 2006)"

#: The fault level the coordination holds to.
TABLE_FAULT_LEVEL_KA = Decimal(50)

#: A supply this close to a table's voltage is that table's (IEC 60038).
_VOLTAGE_TOLERANCE = Decimal("0.05")


@dataclass(frozen=True)
class StarterRow:
    """One row of a coordination table.

    Attributes:
        power_kw: Motor rated power Pe.
        current_a: Motor rated current Ir the row is sized for.
        breaker: The moulded-case circuit-breaker.
        magnetic_trip_a: Its magnetic trip threshold I3.
        contactors: Line contactor; for star-delta, line, delta and star.
        overload: The thermal overload release, where the table gives one.
        overload_range_a: Its current setting range, min and max, as the
            motor sees it (through the current transformer, where there is one).
        current_transformer: The KORC transformer feeding the overload
            release and its primary turns, where the table gives one.
    """

    power_kw: str
    current_a: str
    breaker: str
    magnetic_trip_a: str
    contactors: tuple[str, ...]
    overload: str | None
    overload_range_a: tuple[str, str] | None
    current_transformer: str | None = None


@dataclass(frozen=True)
class StarterSelection:
    """What a motor needs, and where it came from."""

    row: StarterRow
    start: StartType
    source: Citation


def _row(
    pe: str,
    ir: str,
    breaker: str,
    i3: str,
    contactors: tuple[str, ...],
    tor: str | None = None,
    tor_range: tuple[str, str] | None = None,
    *,
    ct: str | None = None,
) -> StarterRow:
    return StarterRow(pe, ir, breaker, i3, contactors, tor, tor_range, ct)


#: Table 3: 400 V 50 kA DOL Normal Type 2 (Tmax - Contactor - TOR), p. 121 as
#: printed. MA: magnetic only adjustable release; MF: fixed magnetic only.
_DOL: tuple[StarterRow, ...] = (
    _row("0.37", "1.1", "T2S160 MF 1.6", "21", ("A9",), "TA25DU1.4", ("1", "1.4")),
    _row("0.55", "1.5", "T2S160 MF 1.6", "21", ("A9",), "TA25DU1.8", ("1.3", "1.8")),
    _row("0.75", "1.9", "T2S160 MF 2", "26", ("A9",), "TA25DU2.4", ("1.7", "2.4")),
    _row("1.1", "2.8", "T2S160 MF 3.2", "42", ("A9",), "TA25DU4", ("2.8", "4")),
    _row("1.5", "3.5", "T2S160 MF 4", "52", ("A16",), "TA25DU5", ("3.5", "5")),
    _row("2.2", "5", "T2S160 MF 5", "65", ("A26",), "TA25DU6.5", ("4.5", "6.5")),
    _row("3", "6.6", "T2S160 MF 8.5", "110", ("A26",), "TA25DU8.5", ("6", "8.5")),
    _row("4", "8.6", "T2S160 MF 11", "145", ("A30",), "TA25DU11", ("7.5", "11")),
    _row("5.5", "11.5", "T2S160 MF 12.5", "163", ("A30",), "TA25DU14", ("10", "14")),
    _row("7.5", "15.2", "T2S160 MA 20", "210", ("A30",), "TA25DU19", ("13", "19")),
    _row("11", "22", "T2S160 MA 32", "288", ("A30",), "TA42DU25", ("18", "25")),
    _row("15", "28.5", "T2S160 MA 52", "392", ("A50",), "TA75DU42", ("29", "42")),
    _row("18.5", "36", "T2S160 MA 52", "469", ("A50",), "TA75DU52", ("36", "52")),
    _row("22", "42", "T2S160 MA 52", "547", ("A50",), "TA75DU52", ("36", "52")),
    _row("30", "56", "T2S160 MA 80", "840", ("A63",), "TA75DU80", ("60", "80")),
    _row("37", "68", "T2S160 MA 80", "960", ("A75",), "TA75DU80", ("60", "80")),
    _row("45", "83", "T2S160 MA 100", "1200", ("A95",), "TA110DU110", ("80", "110")),
    _row("55", "98", "T3S250 MA 160", "1440", ("A110",), "TA110DU110", ("80", "110")),
    _row("75", "135", "T3S250 MA 200", "1800", ("A145",), "TA200DU175", ("130", "175")),
    _row("90", "158", "T3S250 MA 200", "2400", ("A185",), "TA200DU200", ("150", "200")),
    _row("110", "193", "T4S320 PR221-I In320", "2720", ("A210",), "E320DU320", ("100", "320")),
    _row("132", "232", "T5S400 PR221-I In400", "3200", ("A260",), "E320DU320", ("100", "320")),
    _row("160", "282", "T5S400 PR221-I In400", "4000", ("A300",), "E320DU320", ("100", "320")),
    _row("200", "349", "T5S630 PR221-I In630", "5040", ("AF400",), "E500DU500", ("150", "500")),
    _row("250", "430", "T6S630 PR221-I In630", "6300", ("AF460",), "E500DU500", ("150", "500")),
    _row("290", "520", "T6S800 PR221-I In800", "7200", ("AF580",), "E800DU800", ("250", "800")),
    _row("315", "545", "T6S800 PR221-I In800", "8000", ("AF580",), "E800DU800", ("250", "800")),
    _row("355", "610", "T6S800 PR221-I In800", "8000", ("AF750",), "E800DU800", ("250", "800")),
)  # fmt: skip

#: Table 5: 400 V 50 kA Y/Δ Normal Type 2 (Tmax - Contactor - TOR), p. 123
#: as printed. Contactors are line, delta, star.
_STAR_DELTA: tuple[StarterRow, ...] = (
    _row("18.5", "36", "T2S160 MA52", "469", ("A50", "A50", "A26"), "TA75DU25", ("18", "25")),
    _row("22", "42", "T2S160 MA52", "547", ("A50", "A50", "A26"), "TA75DU32", ("22", "32")),
    _row("30", "56", "T2S160 MA80", "720", ("A63", "A63", "A30"), "TA75DU42", ("29", "42")),
    _row("37", "68", "T2S160 MA80", "840", ("A75", "A75", "A30"), "TA75DU52", ("36", "52")),
    _row("45", "83", "T2S160 MA100", "1050", ("A75", "A75", "A30"), "TA75DU63", ("45", "63")),
    _row("55", "98", "T2S160 MA100", "1200", ("A75", "A75", "A40"), "TA75DU63", ("45", "63")),
    _row("75", "135", "T3S250 MA160", "1700", ("A95", "A95", "A75"), "TA110DU90", ("66", "90")),
    _row("90", "158", "T3S250 MA200", "2000", ("A110", "A110", "A95"), "TA110DU110", ("80", "110")),
    _row("110", "193", "T3S250 MA200", "2400", ("A145", "A145", "A95"), "TA200DU135", ("100", "135")),
    _row("132", "232", "T4S320 PR221-I In320", "2880", ("A145", "A145", "A110"), "E200DU200", ("60", "200")),
    _row("160", "282", "T5S400 PR221-I In400", "3600", ("A185", "A185", "A145"), "E200DU200", ("60", "200")),
    _row("200", "349", "T5S630 PR221-I In630", "4410", ("A210", "A210", "A185"), "E320DU320", ("100", "320")),
    _row("250", "430", "T5S630 PR221-I In630", "5670", ("A260", "A260", "A210"), "E320DU320", ("100", "320")),
    _row("290", "520", "T6S630 PR221-I In630", "6300", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
    _row("315", "545", "T6S800 PR221-I In800", "7200", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
    _row("355", "610", "T6S800 PR221-I In800", "8000", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
)  # fmt: skip

#: Table 6: 400 V 50 kA DOL Normal and Heavy duty Type 2 (Tmax with MP
#: release - Contactor), p. 123 as printed. The MP release protects against
#: overload itself, so there is no separate TOR; for heavy-duty start its
#: tripping class is set to 30. (160 kW: AF400 for heavy duty; the table
#: gives AF300 for normal start, which this table is not used for here.)
_DOL_HEAVY: tuple[StarterRow, ...] = (
    _row("30", "56", "T4S250 PR222MP In100", "600", ("A95",)),
    _row("37", "68", "T4S250 PR222MP In100", "700", ("A95",)),
    _row("45", "83", "T4S250 PR222MP In100", "800", ("A95",)),
    _row("55", "98", "T4S250 PR222MP In160", "960", ("A145",)),
    _row("75", "135", "T4S250 PR222MP In160", "1280", ("A145",)),
    _row("90", "158", "T4S250 PR222MP In200", "1600", ("A185",)),
    _row("110", "193", "T5S400 PR222MP In320", "1920", ("A210",)),
    _row("132", "232", "T5S400 PR222MP In320", "2240", ("A260",)),
    _row("160", "282", "T5S400 PR222MP In320", "2560", ("AF400",)),
    _row("200", "349", "T5S400 PR222MP In400", "3200", ("AF400",)),
    _row("250", "430", "T6S800 PR222MP In630", "5040", ("AF460",)),
    _row("290", "520", "T6S800 PR222MP In630", "5670", ("AF580",)),
    _row("315", "545", "T6S800 PR222MP In630", "5670", ("AF580",)),
    _row("355", "610", "T6S800 PR222MP In630", "5670", ("AF750",)),
)  # fmt: skip

# Table 7: 440 V 50 kA DOL Normal Type 2 (Tmax – Contactor – TOR)
# PDF p. 127 (printed p. 124). Same layout as Table 3; breakers are the H
# (T2H/T4H/T5H/T6H) versions. MA: magnetic only adjustable release;
# MF: fixed magnetic only release.
# Footnote *: "Connection kit not available. To use the connection kit,
# replace with relay E800DU800." (applies to the 290 kW row).
_DOL_440: tuple[StarterRow, ...] = (
    _row("0.37", "1", "T2H160 MF 1", "13", ("A9",), "TA25DU1.4", ("1", "1.4")),
    _row("0.55", "1.4", "T2H160 MF 1.6", "21", ("A9",), "TA25DU1.8", ("1.3", "1.8")),
    _row("0.75", "1.7", "T2H160 MF 2", "26", ("A9",), "TA25DU2.4", ("1.7", "2.4")),
    _row("1.1", "2.2", "T2H160 MF 2.5", "33", ("A9",), "TA25DU3.1", ("2.2", "3.1")),
    _row("1.5", "3", "T2H160 MF 3.2", "42", ("A16",), "TA25DU4", ("2.8", "4")),
    _row("2.2", "4.4", "T2H160 MF 5", "65", ("A26",), "TA25DU5", ("3.5", "5")),
    _row("3", "5.7", "T2H160 MF 6.5", "84", ("A26",), "TA25DU6.5", ("4.5", "6.5")),
    _row("4", "7.8", "T2H160 MF 8.5", "110", ("A30",), "TA25DU11", ("7.5", "11")),
    _row("5.5", "10.5", "T2H160 MF 11", "145", ("A30",), "TA25DU14", ("10", "14")),
    _row("7.5", "13.5", "T2H160 MA 20", "180", ("A30",), "TA25DU19", ("13", "19")),
    _row("11", "19", "T2H160 MA 32", "240", ("A30",), "TA42DU25", ("18", "25")),
    _row("15", "26", "T2H160 MA 32", "336", ("A50",), "TA75DU32", ("22", "32")),
    _row("18.5", "32", "T2H160 MA 52", "469", ("A50",), "TA75DU42", ("29", "42")),
    _row("22", "38", "T2H160 MA 52", "547", ("A50",), "TA75DU52", ("36", "52")),
    _row("30", "52", "T2H160 MA 80", "720", ("A63",), "TA75DU63", ("45", "63")),
    _row("37", "63", "T2H160 MA 80", "840", ("A75",), "TA75DU80", ("60", "80")),
    _row("45", "75", "T2H160 MA 100", "1050", ("A95",), "TA110DU90", ("65", "90")),
    _row("55", "90", "T4H250 PR221-I In160", "1200", ("A110",), "TA110DU110", ("80", "110")),
    _row("75", "120", "T4H250 PR221-I In250", "1750", ("A145",), "E200DU200", ("60", "200")),
    _row("90", "147", "T4H250 PR221-I In250", "2000", ("A185",), "E200DU200", ("60", "200")),
    _row("110", "177", "T4H250 PR221-I In250", "2500", ("A210",), "E320DU320", ("100", "320")),
    _row("132", "212", "T5H400 PR221-I In320", "3200", ("A260",), "E320DU320", ("100", "320")),
    _row("160", "260", "T5H400 PR221-I In400", "3600", ("A300",), "E320DU320", ("100", "320")),
    _row("200", "320", "T5H630 PR221-I In630", "4410", ("AF400",), "E500DU500", ("150", "500")),  # printed "AF 400"
    _row("250", "410", "T6H630 PR221-I In630", "5355", ("AF460",), "E500DU500", ("150", "500")),  # printed "AF 460"
    _row("290", "448", "T6H630 PR221-I In630", "6300", ("AF580",), "E500DU500", ("150", "500")),  # printed "AF 580"; TOR printed "E500DU500*" (no connection kit; use E800DU800 for it)
    _row("315", "500", "T6H800 PR221-I In800", "7200", ("AF580",), "E800DU800", ("250", "800")),  # printed "AF 580"
    _row("355", "549", "T6H800 PR221-I In800", "8000", ("AF580",), "E800DU800", ("250", "800")),  # printed "AF 580"
)  # fmt: skip

# Table 9: 440 V 50 kA Y/Δ Normal Type 2 (Tmax – Contactor – TOR)
# PDF p. 129 (printed p. 126). Same layout as Table 5; contactors are line,
# delta, star. Several contactors printed with a space ("A 50"), normalised.
# MA: Magnetic only adjustable release.
_STAR_DELTA_440: tuple[StarterRow, ...] = (
    _row("18.5", "32", "T2H160 MA52", "392", ("A50", "A50", "A16"), "TA75DU25", ("18", "25")),
    _row("22", "38", "T2H160 MA52", "469", ("A50", "A50", "A26"), "TA75DU25", ("18", "25")),
    _row("30", "52", "T2H160 MA80", "720", ("A63", "A63", "A26"), "TA75DU42", ("29", "42")),
    _row("37", "63", "T2H160 MA80", "840", ("A75", "A75", "A30"), "TA75DU42", ("29", "42")),
    _row("45", "75", "T2H160 MA80", "960", ("A75", "A75", "A30"), "TA75DU52", ("36", "52")),
    _row("55", "90", "T2H160 MA100", "1150", ("A75", "A75", "A40"), "TA75DU63", ("45", "63")),
    _row("75", "120", "T4H250 PR221-I In250", "1625", ("A95", "A95", "A75"), "TA80DU80", ("60", "80")),
    _row("90", "147", "T4H250 PR221-I In250", "1875", ("A95", "A95", "A75"), "TA110DU110", ("80", "110")),
    _row("110", "177", "T4H250 PR221-I In250", "2250", ("A145", "A145", "A95"), "E200DU200", ("60", "200")),
    _row("132", "212", "T4H320 PR221-I In320", "2720", ("A145", "A145", "A110"), "E200DU200", ("60", "200")),
    _row("160", "260", "T5H400 PR221-I In400", "3200", ("A185", "A185", "A145"), "E200DU200", ("60", "200")),
    _row("200", "320", "T5H630 PR221-I In630", "4095", ("A210", "A210", "A185"), "E320DU320", ("100", "320")),
    _row("250", "410", "T5H630 PR221-I In630", "5040", ("A260", "A260", "A210"), "E320DU320", ("100", "320")),
    _row("290", "448", "T6H630 PR221-I In630", "5670", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
    _row("315", "500", "T6H630 PR221-I In630", "6300", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
    _row("355", "549", "T6H800 PR221-I In800", "7200", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
)  # fmt: skip

# Table 10: 440 V 50 kA DOL Normal and Heavy duty Type 2 (Tmax with MP release-Contactor)
# PDF p. 129 (printed p. 126). Same layout as Table 6: no TOR. Extra printed
# columns given in the comment: I1 range [A] and contactor Group [A].
# Footnote (*): for heavy-duty start set the electronic release tripping class
# to class 30. Footnote (**): in case of normal start use AF300 (160 kW row).
_DOL_HEAVY_440: tuple[StarterRow, ...] = (
    _row("30", "52", "T4H250 PR222MP In100", "600", ("A95",)),  # I1 40-100; Group 93
    _row("37", "63", "T4H250 PR222MP In100", "700", ("A95",)),  # I1 40-100; Group 93
    _row("45", "75", "T4H250 PR222MP In100", "800", ("A95",)),  # I1 40-100; Group 93
    _row("55", "90", "T4H250 PR222MP In160", "960", ("A145",)),  # I1 64-160; Group 145
    _row("75", "120", "T4H250 PR222MP In160", "1120", ("A145",)),  # I1 64-160; Group 145
    _row("90", "147", "T4H250 PR222MP In200", "1400", ("A185",)),  # I1 80-200; Group 185
    _row("110", "177", "T5H400 PR222MP In320", "1920", ("A210",)),  # I1 128-320; Group 210
    _row("132", "212", "T5H400 PR222MP In320", "2240", ("A260",)),  # I1 128-320; Group 240 (printed 240, not 260)
    _row("160", "260", "T5H400 PR222MP In320", "2560", ("AF400",)),  # I1 128-320; Group 320; printed "AF400**": AF300 for normal start
    _row("200", "320", "T5H400 PR222MP In400", "3200", ("AF400",)),  # I1 160-400; Group 400
    _row("250", "370", "T6H800 PR222MP In630", "4410", ("AF460",)),  # I1 252-630; Group 460; NB Ir 370 here vs 410 in Tables 7/9
    _row("290", "436", "T6H800 PR222MP In630", "5040", ("AF460",)),  # I1 252-630; Group 460; NB Ir 436 here vs 448 in Tables 7/9
    _row("315", "500", "T6H800 PR222MP In630", "5040", ("AF580",)),  # I1 252-630; Group 580
    _row("355", "549", "T6H800 PR222MP In630", "5670", ("AF580",)),  # I1 252-630; Group 580
)  # fmt: skip

# Table 11: 500 V 50 kA DOL Normal Type 2 (Tmax – Contactor – TOR)
# PDF p. 130 (printed p. 127). Same layout as Table 3; breakers mix T2L, T4H,
# T5H and T6L. Footnote *: "Connection kit not available. To use the connection
# kit, replace with relay E800DU800." (315 kW row).
_DOL_500: tuple[StarterRow, ...] = (
    _row("0.37", "0.88", "T2L160 MF 1", "13", ("A9",), "TA25DU1.0", ("0.63", "1")),
    _row("0.55", "1.2", "T2L160 MF 1.6", "21", ("A9",), "TA25DU1.4", ("1", "1.4")),
    _row("0.75", "1.5", "T2L160 MF 1.6", "21", ("A9",), "TA25DU1.8", ("1.3", "1.8")),
    _row("1.1", "2.2", "T2L160 MF 2.5", "33", ("A9",), "TA25DU3.1", ("2.2", "3.1")),
    _row("1.5", "2.8", "T2L160 MF 3.2", "42", ("A16",), "TA25DU4", ("2.8", "4")),
    _row("2.2", "4", "T2L160 MF 4", "52", ("A26",), "TA25DU5", ("3.5", "5")),
    _row("3", "5.2", "T2L160 MF 6.5", "84", ("A26",), "TA25DU6.5", ("4.5", "6.5")),
    _row("4", "6.9", "T2L160 MF 8.5", "110", ("A30",), "TA25DU8.5", ("6", "8.5")),
    _row("5.5", "9.1", "T2L160 MF 11", "145", ("A30",), "TA25DU11", ("7.5", "11")),
    _row("7.5", "12.2", "T2L160 MF 12.5", "163", ("A30",), "TA25DU14", ("10", "14")),
    _row("11", "17.5", "T2L160 MA 20", "240", ("A30",), "TA25DU19", ("13", "19")),
    _row("15", "23", "T2L160 MA 32", "336", ("A50",), "TA75DU25", ("18", "25")),
    _row("18.5", "29", "T2L160 MA 52", "392", ("A50",), "TA75DU32", ("22", "32")),
    _row("22", "34", "T2L160 MA 52", "469", ("A50",), "TA75DU42", ("29", "42")),
    _row("30", "45", "T2L160 MA 52", "624", ("A63",), "TA75DU52", ("36", "52")),
    _row("37", "56", "T2L160 MA 80", "840", ("A75",), "TA75DU63", ("45", "63")),
    _row("45", "67", "T2L160 MA 80", "960", ("A95",), "TA80DU80", ("60", "80")),
    _row("55", "82", "T2L160 MA 100", "1200", ("A110",), "TA110DU90", ("65", "90")),
    _row("75", "110", "T4H250 PR221-I In160", "1440", ("A145",), "E200DU200", ("60", "200")),
    _row("90", "132", "T4H250 PR221-I In250", "1875", ("A145",), "E200DU200", ("60", "200")),
    _row("110", "158", "T4H250 PR221-I In250", "2250", ("A185",), "E200DU200", ("60", "200")),
    _row("132", "192", "T4H320 PR221-I In320", "2720", ("A210",), "E320DU320", ("100", "320")),
    _row("160", "230", "T5H400 PR221-I In400", "3600", ("A260",), "E320DU320", ("100", "320")),
    _row("200", "279", "T5H400 PR221-I In400", "4000", ("A300",), "E320DU320", ("100", "320")),
    _row("250", "335", "T5H630 PR221-I In630", "4725", ("AF400",), "E500DU500", ("150", "500")),  # printed "AF 400", "E 500DU500"
    _row("290", "394", "T6L630 PR221-I In630", "5040", ("AF460",), "E500DU500", ("150", "500")),  # printed "AF 460", "E 500DU500"
    _row("315", "440", "T6L630 PR221-I In630", "6300", ("AF580",), "E500DU500", ("150", "500")),  # printed "AF 580", "E 500DU500*" (no connection kit; use E800DU800 for it)
    _row("355", "483", "T6L630 PR221-I In630", "6300", ("AF580",), "E800DU800", ("250", "800")),  # printed "AF 580", "E 800DU800"
)  # fmt: skip

# Table 13: 500 V 50 kA Y/Δ Normal Type 2 (Tmax – Contactor – TOR)
# PDF p. 132 (printed p. 129). Same layout as Table 5 (line, delta, star).
# Table starts at 22 kW (no 18.5 kW row). MA: magnetic only adjustable release.
_STAR_DELTA_500: tuple[StarterRow, ...] = (
    _row("22", "34", "T2L160 MA52", "430", ("A50", "A50", "A16"), "TA75DU25", ("18", "25")),
    _row("30", "45", "T2L160 MA52", "547", ("A63", "A63", "A26"), "TA75DU32", ("22", "32")),
    _row("37", "56", "T2L160 MA80", "720", ("A75", "A75", "A30"), "TA75DU42", ("29", "42")),
    _row("45", "67", "T2L160 MA80", "840", ("A75", "A75", "A30"), "TA75DU52", ("36", "52")),
    _row("55", "82", "T2L160 MA100", "1050", ("A75", "A75", "A30"), "TA75DU52", ("36", "52")),
    _row("75", "110", "T4H250 PR221-I In250", "1375", ("A95", "A95", "A50"), "TA80DU80", ("60", "80")),
    _row("90", "132", "T4H250 PR221-I In250", "1750", ("A95", "A95", "A75"), "TA110DU90", ("65", "90")),
    _row("110", "158", "T4H250 PR221-I In250", "2000", ("A110", "A110", "A95"), "TA110DU110", ("80", "110")),
    _row("132", "192", "T4H320 PR221-I In320", "2560", ("A145", "A145", "A95"), "E200DU200", ("60", "200")),
    _row("160", "230", "T4H320 PR221-I In320", "2880", ("A145", "A145", "A110"), "E200DU200", ("60", "200")),
    _row("200", "279", "T5H400 PR221-I In400", "3400", ("A210", "A210", "A145"), "E320DU320", ("100", "320")),
    _row("250", "335", "T5H630 PR221-I In630", "4410", ("A210", "A210", "A185"), "E320DU320", ("100", "320")),
    _row("290", "394", "T5H630 PR221-I In630", "5040", ("A260", "A260", "A210"), "E320DU320", ("100", "320")),
    _row("315", "440", "T6L630 PR221-I In630", "5760", ("AF400", "AF400", "A210"), "E500DU500", ("150", "500")),
    _row("355", "483", "T6L630 PR221-I In630", "6300", ("AF400", "AF400", "A260"), "E500DU500", ("150", "500")),
)  # fmt: skip

# Table 14: 500 V 50 kA DOL Normal and Heavy duty Type 2 (Tmax with MP release-Contactor)
# PDF p. 132 (printed p. 129). Same layout as Table 6: no TOR; I1 range and
# Group in the comment. Footnote (*): for heavy duty start set the electronic
# release tripping class to class 30. Footnote (**): in case of normal start
# use AF300 (200 kW row).
_DOL_HEAVY_500: tuple[StarterRow, ...] = (
    _row("30", "45", "T4H250 PR222MP In100", "600", ("A95",)),  # I1 40-100; Group 80
    _row("37", "56", "T4H250 PR222MP In100", "600", ("A95",)),  # I1 40-100; Group 80
    _row("45", "67", "T4H250 PR222MP In100", "700", ("A145",)),  # I1 40-100; Group 100
    _row("55", "82", "T4H250 PR222MP In100", "800", ("A145",)),  # I1 40-100; Group 100
    _row("75", "110", "T4H250 PR222MP In160", "1120", ("A145",)),  # I1 64-160; Group 145
    _row("90", "132", "T4H250 PR222MP In160", "1280", ("A145",)),  # I1 64-160; Group 145
    _row("110", "158", "T4H250 PR222MP In200", "1600", ("A185",)),  # I1 80-200; Group 170
    _row("132", "192", "T5H400 PR222MP In320", "1920", ("A210",)),  # I1 128-320; Group 210
    _row("160", "230", "T5H400 PR222MP In320", "2240", ("A260",)),  # I1 128-320; Group 260
    _row("200", "279", "T5H400 PR222MP In400", "2800", ("AF400",)),  # I1 160-400; Group 400; printed "AF400**": AF300 for normal start
    _row("250", "335", "T5H400 PR222MP In400", "3200", ("AF400",)),  # I1 160-400; Group 400
    _row("290", "395", "T6H800 PR222MP In630", "5040", ("AF460",)),  # I1 252-630; Group 460; NB Ir 395 here vs 394 in Tables 11/13; breaker T6H here vs T6L in 11/13
    _row("315", "415", "T6H800 PR222MP In630", "5040", ("AF460",)),  # I1 252-630; Group 460; NB Ir 415 here vs 440 in Tables 11/13
    _row("355", "451", "T6H800 PR222MP In630", "5670", ("AF580",)),  # I1 252-630; Group 580; NB Ir 451 here vs 483 in Tables 11/13
)  # fmt: skip

# Table 15: 690 V 50kA DOL Normal Type 2 (Tmax-Contactor-CT-TOR)
# PDF p. 133 (printed p. 130). LAYOUT DIFFERS from Table 3:
#   * the current column is headed "Ie" (not "Ir");
#   * extra "CT" columns: KORC type and "N° of primary turns". From 5.5 kW
#     (second alternative) to 55 kW the TA25DU relay is fed through a
#     4L185R/4 KORC current transformer; the printed TOR "current setting"
#     min/max is then the motor-side range (e.g. TA25DU2.4 -> 6-8.5 A), and is
#     transcribed as tor_range unchanged. CT data are in the row comment.
#     Rows <= 4 kW and >= 75 kW have the CT columns blank.
#   * breaker "In" printed with a space ("PR221-I In 100") and MF without
#     ("MF1"); normalised to "PR221-I In100" / "MF 1" to match motor_starter.py.
# Footnotes: (*) Type 1 coordination [rows 2.2, 3, 4 kW and 5.5 kW MF6.5 alt];
#   (**) Cable cross section equal to 4 mm2 [marks the primary-turns figure on
#   5.5-15 kW]; (***) No mounting kit to contactor is available; to use
#   mounting kit provide E800DU800 [355 kW]. Also: "For further information
#   about the KORK, please see the brochure KORK 1GB00-04 catalogue."
_DOL_690: tuple[StarterRow, ...] = (
    _row("0.37", "0.6", "T2L160 MF 1", "13", ("A9",), "TA25DU0.63", ("0.4", "0.63")),
    _row("0.55", "0.9", "T2L160 MF 1", "13", ("A9",), "TA25DU1", ("0.63", "1")),
    _row("0.75", "1.1", "T2L160 MF 1.6", "21", ("A9",), "TA25DU1.4", ("1", "1.4")),
    _row("1.1", "1.6", "T2L160 MF 1.6", "21", ("A9",), "TA25DU1.8", ("1.3", "1.8")),
    _row("1.5", "2", "T2L160 MF 2.5", "33", ("A9",), "TA25DU2.4", ("1.7", "2.4")),
    # AMBIGUOUS: 5.5 kW / 6.5 A is a merged Pe/Ie cell spanning two printed
    # lines, i.e. two alternative solutions for the same motor:
    _row("5.5", "6.5", "T4L250 PR221-I In100", "150", ("A95",), "TA25DU2.4", ("6", "8.5"), ct="4L185R/4, 13 primary turns"),  # alt 2: CT 4L185R/4, 13** primary turns (4 mm2 cable)
    _row("7.5", "8.8", "T4L250 PR221-I In100", "150", ("A95",), "TA25DU2.4", ("7.9", "11.1"), ct="4L185R/4, 10 primary turns"),  # CT 4L185R/4, 10** primary turns (4 mm2 cable)
    _row("11", "13", "T4L250 PR221-I In100", "200", ("A95",), "TA25DU2.4", ("11.2", "15.9"), ct="4L185R/4, 7 primary turns"),  # CT 4L185R/4, 7** primary turns (4 mm2 cable)
    _row("15", "18", "T4L250 PR221-I In100", "250", ("A95",), "TA25DU3.1", ("15.2", "20.5"), ct="4L185R/4, 7 primary turns"),  # CT 4L185R/4, 7** primary turns (4 mm2 cable)
    _row("18.5", "21", "T4L250 PR221-I In100", "300", ("A95",), "TA25DU3.1", ("17.7", "23.9"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4, 6 primary turns
    _row("22", "25", "T4L250 PR221-I In100", "350", ("A95",), "TA25DU4", ("21.6", "30.8"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4, 6 primary turns
    _row("30", "33", "T4L250 PR221-I In100", "450", ("A145",), "TA25DU5", ("27", "38.5"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4, 6 primary turns
    _row("37", "41", "T4L250 PR221-I In100", "550", ("A145",), "TA25DU4", ("32.4", "46.3"), ct="4L185R/4, 4 primary turns"),  # CT 4L185R/4, 4 primary turns
    _row("45", "49", "T4L250 PR221-I In100", "700", ("A145",), "TA25DU5", ("40.5", "57.8"), ct="4L185R/4, 4 primary turns"),  # CT 4L185R/4, 4 primary turns
    _row("55", "60", "T4L250 PR221-I In100", "800", ("A145",), "TA25DU5", ("54", "77.1"), ct="4L185R/4, 3 primary turns"),  # CT 4L185R/4, 3 primary turns
    _row("75", "80", "T4L250 PR221-I In160", "1120", ("A145",), "E200DU200", ("65", "200")),
    _row("90", "95", "T4L250 PR221-I In160", "1280", ("A145",), "E200DU200", ("65", "200")),
    _row("110", "115", "T4L250 PR221-I In250", "1625", ("A145",), "E200DU200", ("65", "200")),
    _row("132", "139", "T4L250 PR221-I In250", "2000", ("A185",), "E200DU200", ("65", "200")),
    _row("160", "167", "T4L250 PR221-I In250", "2250", ("A185",), "E200DU200", ("65", "200")),
    _row("200", "202", "T5L400 PR221-I In320", "2720", ("A210",), "E320DU320", ("105", "320")),
    _row("250", "242", "T5L400 PR221-I In400", "3400", ("A300",), "E320DU320", ("105", "320")),
    _row("290", "301", "T5L630 PR221-I In630", "4410", ("AF400",), "E500DU500", ("150", "500")),
    _row("315", "313", "T5L630 PR221-I In630", "4410", ("AF400",), "E500DU500", ("150", "500")),
    _row("355", "370", "T5L630 PR221-I In630", "5355", ("AF580",), "E500DU500", ("150", "500")),  # TOR printed "E500DU500***": no mounting kit to contactor; provide E800DU800 for it
)  # fmt: skip

# Table 17: 690 V 50 kA Y/Δ Normal Type 2 (Tmax – Contactor – CT – TOR)
# PDF p. 135 (printed p. 132). LAYOUT DIFFERS from Table 5:
#   * extra "CT" columns (KORC type, N° of primary turns) used on 5.5-30 kW;
#     blank from 37 kW up. tor_range is the printed "Current setting" as is.
#   * the TOR heading is "Overload Release" (not "Thermal Overload Release").
#   * breaker printed without a space, "T4L250PR221-I In100"; normalised to
#     "T4L250 PR221-I In100".
#   * table starts at 5.5 kW and runs to 450 kW (rows for 400 and 450 kW,
#     which no other table has).
# Footnotes: (*) Cable cross section equal to 4 mm2 [marks Ir on 5.5-15 kW];
#   (**) Connect the overload/relay upstream the line-delta node [marks KORC on
#   5.5-30 kW and the TOR on 5.5-55 kW]. Also the KORK brochure note.
_STAR_DELTA_690: tuple[StarterRow, ...] = (
    _row("5.5", "6.5", "T4L250 PR221-I In100", "150", ("A95", "A95", "A26"), "TA25DU2.4", ("6", "8.5"), ct="4L185R/4, 13 primary turns"),  # Ir printed "6.5*" (4 mm2 cable); CT 4L185R/4**, 13 turns; TOR "TA25DU2.4**" (upstream of line-delta node)
    _row("7.5", "8.8", "T4L250 PR221-I In100", "150", ("A95", "A95", "A26"), "TA25DU2.4", ("7.9", "11.1"), ct="4L185R/4, 10 primary turns"),  # Ir printed "8.8*"; CT 4L185R/4**, 10 turns; TOR "TA25DU2.4**"
    _row("11", "13", "T4L250 PR221-I In100", "200", ("A95", "A95", "A26"), "TA25DU2.4", ("11.2", "15.9"), ct="4L185R/4, 7 primary turns"),  # Ir printed "13*"; CT 4L185R/4**, 7 turns; TOR "TA25DU2.4**"
    _row("15", "18", "T4L250 PR221-I In100", "250", ("A95", "A95", "A26"), "TA25DU3.1", ("15.2", "20.5"), ct="4L185R/4, 7 primary turns"),  # Ir printed "18*"; CT 4L185R/4**, 7 turns; TOR "TA25DU3.1**"
    _row("18.5", "21", "T4L250 PR221-I In100", "300", ("A95", "A95", "A30"), "TA25DU3.1", ("17.7", "23.9"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4**, 6 turns; TOR "TA25DU3.1**"
    _row("22", "25", "T4L250 PR221-I In100", "350", ("A95", "A95", "A30"), "TA25DU4", ("21.6", "30.8"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4**, 6 turns; TOR "TA25DU4**"
    _row("30", "33", "T4L250 PR221-I In100", "450", ("A145", "A145", "A30"), "TA25DU5", ("27", "38.5"), ct="4L185R/4, 6 primary turns"),  # CT 4L185R/4**, 6 turns; TOR "TA25DU5**"
    _row("37", "41", "T4L250 PR221-I In100", "550", ("A145", "A145", "A30"), "TA75DU52", ("36", "52")),  # no CT; TOR "TA75DU52**"
    _row("45", "49", "T4L250 PR221-I In100", "650", ("A145", "A145", "A30"), "TA75DU52", ("36", "52")),  # no CT; TOR "TA75DU52**"
    _row("55", "60", "T4L250 PR221-I In100", "800", ("A145", "A145", "A40"), "TA75DU52", ("36", "52")),  # no CT; TOR "TA75DU52**"
    _row("75", "80", "T4L250 PR221-I In160", "1120", ("A145", "A145", "A50"), "TA75DU52", ("36", "52")),
    _row("90", "95", "T4L250 PR221-I In160", "1280", ("A145", "A145", "A75"), "TA75DU63", ("45", "63")),
    _row("110", "115", "T4L250 PR221-I In160", "1600", ("A145", "A145", "A75"), "TA75DU80", ("60", "80")),
    _row("132", "139", "T4L250 PR221-I In250", "1875", ("A145", "A145", "A95"), "TA200DU110", ("80", "110")),
    _row("160", "167", "T4L250 PR221-I In250", "2125", ("A145", "A145", "A110"), "TA200DU110", ("80", "110")),
    _row("200", "202", "T4L320 PR221-I In320", "2720", ("A185", "A185", "A110"), "TA200DU135", ("100", "135")),
    _row("250", "242", "T5L400 PR221-I In400", "3200", ("AF400", "AF400", "A145"), "E500DU500", ("150", "500")),
    _row("290", "301", "T5L400 PR221-I In400", "4000", ("AF400", "AF400", "A145"), "E500DU500", ("150", "500")),
    _row("315", "313", "T5L630 PR221-I In630", "4410", ("AF400", "AF400", "A185"), "E500DU500", ("150", "500")),
    _row("355", "370", "T5L630 PR221-I In630", "5040", ("AF400", "AF400", "A210"), "E500DU500", ("150", "500")),
    _row("400", "420", "T5L630 PR221-I In630", "5670", ("AF460", "AF460", "A210"), "E500DU500", ("150", "500")),
    _row("450", "470", "T5L630 PR221-I In630", "6300", ("AF460", "AF460", "A260"), "E500DU500", ("150", "500")),
)  # fmt: skip

# Table 18: 690 V 50 kA DOL Normal and Heavy duty Type 2 (Tmax with MP release-Contactor)
# PDF p. 136 (printed p. 133). Same layout as Table 6: no TOR; I1 range and
# Group in the comment. Only 45-315 kW. Footnote (*): for heavy duty start set
# the electronic release tripping class to class 30. No AF300 (**) footnote.
_DOL_HEAVY_690: tuple[StarterRow, ...] = (
    _row("45", "49", "T4L250 PR222MP In100", "600", ("A145",)),  # I1 40-100; Group 100
    _row("55", "60", "T4L250 PR222MP In100", "600", ("A145",)),  # I1 40-100; Group 100
    _row("75", "80", "T4L250 PR222MP In100", "800", ("A145",)),  # I1 40-100; Group 100
    _row("90", "95", "T4L250 PR222MP In160", "960", ("A145",)),  # I1 64-160; Group 120
    _row("110", "115", "T4L250 PR222MP In160", "1120", ("A145",)),  # I1 64-160; Group 120
    _row("132", "139", "T4L250 PR222MP In160", "1440", ("A185",)),  # I1 64-160; Group 160
    _row("160", "167", "T4L250 PR222MP In200", "1600", ("A185",)),  # I1 80-200; Group 170
    _row("200", "202", "T5L400 PR222MP In320", "1920", ("A210",)),  # I1 128-320; Group 210
    _row("250", "242", "T5L400 PR222MP In320", "2240", ("A300",)),  # I1 128-320; Group 280
    _row("290", "301", "T5L400 PR222MP In400", "2800", ("AF400",)),  # I1 160-400; Group 350
    _row("315", "313", "T5L400 PR222MP In400", "3200", ("AF400",)),  # I1 160-400; Group 350
)  # fmt: skip


_Table = tuple[tuple[StarterRow, ...], int, str]

#: The tables by supply voltage and start type, with their PDF page and title.
_TABLES: dict[Decimal, dict[StartType, _Table]] = {
    Decimal(400): {
        StartType.DOL: (_DOL, 124, "Table 3: 400 V 50 kA DOL Normal Type 2"),
        StartType.STAR_DELTA: (_STAR_DELTA, 126, "Table 5: 400 V 50 kA Y/Δ Normal Type 2"),
        StartType.DOL_HEAVY: (
            _DOL_HEAVY,
            126,
            "Table 6: 400 V 50 kA DOL Normal and Heavy duty Type 2",
        ),
    },
    Decimal(440): {
        StartType.DOL: (_DOL_440, 127, "Table 7: 440 V 50 kA DOL Normal Type 2"),
        StartType.STAR_DELTA: (_STAR_DELTA_440, 129, "Table 9: 440 V 50 kA Y/Δ Normal Type 2"),
        StartType.DOL_HEAVY: (
            _DOL_HEAVY_440,
            129,
            "Table 10: 440 V 50 kA DOL Normal and Heavy duty Type 2",
        ),
    },
    Decimal(500): {
        StartType.DOL: (_DOL_500, 130, "Table 11: 500 V 50 kA DOL Normal Type 2"),
        StartType.STAR_DELTA: (_STAR_DELTA_500, 132, "Table 13: 500 V 50 kA Y/Δ Normal Type 2"),
        StartType.DOL_HEAVY: (
            _DOL_HEAVY_500,
            132,
            "Table 14: 500 V 50 kA DOL Normal and Heavy duty Type 2",
        ),
    },
    Decimal(690): {
        StartType.DOL: (_DOL_690, 133, "Table 15: 690 V 50 kA DOL Normal Type 2"),
        StartType.STAR_DELTA: (_STAR_DELTA_690, 135, "Table 17: 690 V 50 kA Y/Δ Normal Type 2"),
        StartType.DOL_HEAVY: (
            _DOL_HEAVY_690,
            136,
            "Table 18: 690 V 50 kA DOL Normal and Heavy duty Type 2",
        ),
    },
}


def _table_voltage(supply_voltage_v: Decimal) -> Decimal:
    """The table voltage a supply is nominally at, or a refusal."""
    if supply_voltage_v.is_finite():
        near = [v for v in _TABLES if abs(supply_voltage_v - v) <= v * _VOLTAGE_TOLERANCE]
        if near:
            return min(near, key=lambda v: abs(supply_voltage_v - v))
    held = ", ".join(f"{v} V" for v in _TABLES)
    raise ValidationError(
        f"{supply_voltage_v} V: the coordination tables held are for {held} (± 5 %)"
    )


def select_starter(
    *,
    motor_power_kw: Decimal,
    motor_current_a: Decimal,
    start: StartType,
    supply_voltage_v: Decimal,
    fault_level_ka: Decimal | None = None,
) -> StarterSelection:
    """Select a Type 2 coordinated starter for a squirrel-cage motor.

    The first row whose rated power and rated current both reach the motor's
    is chosen: a motor between two rows, or drawing more than its row's
    typical current, takes the larger row, never the smaller.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        §3.3 "Protection and switching of motors", Tables 3, 5 and 6 (400 V),
        7, 9 and 10 (440 V), 11, 13 and 14 (500 V), 15, 17 and 18 (690 V),
        all 50 kA, Type 2; worked examples p. 134 as printed.

    Args:
        motor_power_kw: Motor rated power.
        motor_current_a: Motor nameplate current.
        start: Direct on line, star-delta, or heavy-duty direct on line.
        supply_voltage_v: Line-to-line supply voltage.
        fault_level_ka: Prospective short-circuit current at the panel, if
            known; the coordination holds up to 50 kA.

    Returns:
        The table row, and the citation for it.

    Raises:
        ValidationError: If no table is for the supply, the fault level exceeds
            50 kA, a quantity is not positive, the motor is past the table, or
            the row's overload release cannot be set down to the motor's
            current.
    """
    for name, value in (("motor_power_kw", motor_power_kw), ("motor_current_a", motor_current_a)):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"{name} must be positive, got {value}")
    table_voltage = _table_voltage(supply_voltage_v)
    if fault_level_ka is not None and (
        not fault_level_ka.is_finite() or fault_level_ka > TABLE_FAULT_LEVEL_KA
    ):
        raise ValidationError(
            f"a {fault_level_ka} kA fault level exceeds the 50 kA the coordination holds to"
        )

    rows, page, section = _TABLES[table_voltage][start]
    for row in rows:
        if Decimal(row.power_kw) >= motor_power_kw and Decimal(row.current_a) >= motor_current_a:
            if (
                row.overload_range_a is not None
                and Decimal(row.power_kw) > motor_power_kw
                and motor_current_a < Decimal(row.overload_range_a[0])
            ):
                # Taking the next row up is right for a motor between two
                # rows, never for one its relay cannot be set down to: the
                # relay would not protect it. (A motor's own row is kept as
                # printed even where its Ir sits below the relay's range.)
                raise ValidationError(
                    f"a {motor_current_a} A motor is below the {row.overload} setting range "
                    f"({row.overload_range_a[0]}-{row.overload_range_a[1]} A) of the first "
                    f"Type 2 row that carries it in {section}"
                )
            return StarterSelection(
                row=row,
                start=start,
                source=Citation(
                    document_id=HANDBOOK_ID,
                    document_title=HANDBOOK_TITLE,
                    manufacturer="ABB",
                    page=page,
                    section=section,
                ),
            )
    smallest = rows[0].power_kw
    largest = rows[-1].power_kw
    raise ValidationError(
        f"a {motor_power_kw} kW, {motor_current_a} A motor is outside {section} "
        f"({smallest}-{largest} kW)"
    )
