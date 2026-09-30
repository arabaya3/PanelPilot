"""Motor starter selection: circuit-breaker, contactor and overload relay.

Pure functions. Each table cites the manufacturer guide it came from.

The coordination tables of ABB's *Electrical installation handbook* Vol. 2
(1SDC010001D0204), §3.3 "Protection and switching of motors": Type 2
coordination at 400 V and 50 kA prospective short-circuit current, for a
three-phase squirrel-cage motor. Only those tables are held; another voltage,
a higher fault level, or a motor past the last row is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import ValidationError
from app.models.schemas.calculations import StartType
from app.models.schemas.search import Citation

HANDBOOK_ID = "abb-1SDC010001D0204"
HANDBOOK_TITLE = "Electrical installation handbook, Vol. 2: Electrical devices (4th ed., 2006)"

#: The tables' supply and the fault level their coordination holds to.
TABLE_VOLTAGE_V = Decimal(400)
TABLE_FAULT_LEVEL_KA = Decimal(50)

#: A supply this close to 400 V is the tables' nominal voltage (IEC 60038).
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
        overload_range_a: Its current setting range, min and max.
    """

    power_kw: str
    current_a: str
    breaker: str
    magnetic_trip_a: str
    contactors: tuple[str, ...]
    overload: str | None
    overload_range_a: tuple[str, str] | None


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
) -> StarterRow:
    return StarterRow(pe, ir, breaker, i3, contactors, tor, tor_range)


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

_TABLES: dict[StartType, tuple[tuple[StarterRow, ...], int, str]] = {
    StartType.DOL: (_DOL, 124, "Table 3: 400 V 50 kA DOL Normal Type 2"),
    StartType.STAR_DELTA: (_STAR_DELTA, 126, "Table 5: 400 V 50 kA Y/Δ Normal Type 2"),
    StartType.DOL_HEAVY: (_DOL_HEAVY, 126, "Table 6: 400 V 50 kA DOL Normal and Heavy duty Type 2"),
}


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
        §3.3 "Protection and switching of motors", Tables 3, 5 and 6 (400 V,
        50 kA, Type 2); worked examples p. 134 as printed.

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
        ValidationError: If the supply is not 400 V, the fault level exceeds
            50 kA, a quantity is not positive, or the motor is past the table.
    """
    for name, value in (("motor_power_kw", motor_power_kw), ("motor_current_a", motor_current_a)):
        if not value.is_finite() or value <= 0:
            raise ValidationError(f"{name} must be positive, got {value}")
    if (
        not supply_voltage_v.is_finite()
        or abs(supply_voltage_v - TABLE_VOLTAGE_V) > TABLE_VOLTAGE_V * _VOLTAGE_TOLERANCE
    ):
        raise ValidationError(
            f"{supply_voltage_v} V: the coordination tables held are for 400 V only"
        )
    if fault_level_ka is not None and (
        not fault_level_ka.is_finite() or fault_level_ka > TABLE_FAULT_LEVEL_KA
    ):
        raise ValidationError(
            f"a {fault_level_ka} kA fault level exceeds the 50 kA the coordination holds to"
        )

    rows, page, section = _TABLES[start]
    for row in rows:
        if Decimal(row.power_kw) >= motor_power_kw and Decimal(row.current_a) >= motor_current_a:
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
