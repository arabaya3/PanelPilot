"""Cable sizing calculations.

Pure functions: no I/O, no database, no settings. Every formula names the
manufacturer guide or standard clause it came from in its docstring, so a
reviewer can check the arithmetic against the paper source without reading the
call site.

Conductor sizing and derating, and aluminium voltage drop, come from ABB's *Electrical installation
handbook*, Vol. 2 "Electrical devices" (1SDC010001D0204, 4th ed. 2006), which
republishes the IEC 60364-5-52 tables under ABB's own publication. Schneider's
EIG, the guide first named for these, refuses our crawler; ABB publishes the
same tables openly, with two worked sizing examples to test against.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from app.core.errors import ValidationError
from app.models.schemas.calculations import (
    AppliedFactor,
    CableSizingResult,
    ConductorMaterial,
    InstallationMethod,
)
from app.models.schemas.search import Citation

#: The handbook this module's sizing tables are transcribed from.
ABB_HANDBOOK_ID = "abb-1SDC010001D0204"
ABB_HANDBOOK_TITLE = "Electrical installation handbook, Vol. 2: Electrical devices (4th ed., 2006)"

#: PDF page of each table, for citations.
_TABLE_4_PAGE = 34
_TABLE_5_PAGE = 37
_TABLE_8_PAGES = {
    InstallationMethod.A1: 41,
    InstallationMethod.A2: 41,
    InstallationMethod.B1: 41,
    InstallationMethod.B2: 42,
    InstallationMethod.C: 42,
    InstallationMethod.E: 43,
    InstallationMethod.F: 43,
}


def abb_citation(page: int, section: str) -> Citation:
    """Cite a page of the ABB handbook.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204).

    Args:
        page: 1-indexed PDF page.
        section: The table, as the handbook titles it.

    Returns:
        The citation.
    """
    return Citation(
        document_id=ABB_HANDBOOK_ID,
        document_title=ABB_HANDBOOK_TITLE,
        manufacturer="ABB",
        page=page,
        section=section,
    )


#: Table 4: correction factor k1 for ambient air temperature other than 30 °C,
#: PVC (70 °C) and XLPE/EPR (90 °C) columns. The handbook marks the PVC column
#: as ending at 60 °C. 30 °C is the reference and is 1 by definition.
_AMBIENT_K1: dict[int, dict[int, str]] = {
    70: {
        10: "1.22", 15: "1.17", 20: "1.12", 25: "1.06", 30: "1", 35: "0.94",
        40: "0.87", 45: "0.79", 50: "0.71", 55: "0.61", 60: "0.50",
    },
    90: {
        10: "1.15", 15: "1.12", 20: "1.08", 25: "1.04", 30: "1", 35: "0.96",
        40: "0.91", 45: "0.87", 50: "0.82", 55: "0.76", 60: "0.71", 65: "0.65",
        70: "0.58", 75: "0.50", 80: "0.41",
    },
}  # fmt: skip

#: Table 5, item 1 ("bunched in air, on a surface, embedded or enclosed"):
#: reduction factor k2 by number of circuits or multi-core cables. The bunched
#: row is the lowest in the table at every count, so it is the one that holds
#: whatever the actual arrangement; the handbook's own worked example uses it.
_GROUPING_K2: dict[int, str] = {
    1: "1.00", 2: "0.80", 3: "0.70", 4: "0.65", 5: "0.60", 6: "0.57",
    7: "0.54", 8: "0.52", 9: "0.50", 12: "0.45", 16: "0.41", 20: "0.38",
}  # fmt: skip

#: Table 8: current-carrying capacity I0 of copper conductors at 30 °C, in
#: amperes. Per method and cross-section: (XLPE/EPR 2 loaded, XLPE/EPR 3
#: loaded, PVC 2 loaded, PVC 3 loaded). For method F the 3-loaded column is
#: the trefoil (three single-core cables touching) arrangement.
_AMPACITY_CU: dict[InstallationMethod, dict[str, tuple[str, str, str, str]]] = {
    InstallationMethod.A1: {
        "1.5": ("19", "17", "14.5", "13.5"), "2.5": ("26", "23", "19.5", "18"),
        "4": ("35", "31", "26", "24"), "6": ("45", "40", "34", "31"),
        "10": ("61", "54", "46", "42"), "16": ("81", "73", "61", "56"),
        "25": ("106", "95", "80", "73"), "35": ("131", "117", "99", "89"),
        "50": ("158", "141", "119", "108"), "70": ("200", "179", "151", "136"),
        "95": ("241", "216", "182", "164"), "120": ("278", "249", "210", "188"),
        "150": ("318", "285", "240", "216"), "185": ("362", "324", "273", "245"),
        "240": ("424", "380", "321", "286"), "300": ("486", "435", "367", "328"),
    },
    InstallationMethod.A2: {
        "1.5": ("18.5", "16.5", "14", "13"), "2.5": ("25", "22", "18.5", "17.5"),
        "4": ("33", "30", "25", "23"), "6": ("42", "38", "32", "29"),
        "10": ("57", "51", "43", "39"), "16": ("76", "68", "57", "52"),
        "25": ("99", "89", "75", "68"), "35": ("121", "109", "92", "83"),
        "50": ("145", "130", "110", "99"), "70": ("183", "164", "139", "125"),
        "95": ("220", "197", "167", "150"), "120": ("253", "227", "192", "172"),
        "150": ("290", "259", "219", "196"), "185": ("329", "295", "248", "223"),
        "240": ("386", "346", "291", "261"), "300": ("442", "396", "334", "298"),
    },
    InstallationMethod.B1: {
        "1.5": ("23", "20", "17.5", "15.5"), "2.5": ("31", "28", "24", "21"),
        "4": ("42", "37", "32", "28"), "6": ("54", "48", "41", "36"),
        "10": ("75", "66", "57", "50"), "16": ("100", "88", "76", "68"),
        "25": ("133", "117", "101", "89"), "35": ("164", "144", "125", "110"),
        "50": ("198", "175", "151", "134"), "70": ("253", "222", "192", "171"),
        "95": ("306", "269", "232", "207"), "120": ("354", "312", "269", "239"),
    },
    InstallationMethod.B2: {
        "1.5": ("22", "19.5", "16.5", "15"), "2.5": ("30", "26", "23", "20"),
        "4": ("40", "35", "30", "27"), "6": ("51", "44", "38", "34"),
        "10": ("69", "60", "52", "46"), "16": ("91", "80", "69", "62"),
        "25": ("119", "105", "90", "80"), "35": ("146", "128", "111", "99"),
        "50": ("175", "154", "133", "118"), "70": ("221", "194", "168", "149"),
        "95": ("265", "233", "201", "179"), "120": ("305", "268", "232", "206"),
    },
    InstallationMethod.C: {
        "1.5": ("24", "22", "19.5", "17.5"), "2.5": ("33", "30", "27", "24"),
        "4": ("45", "40", "36", "32"), "6": ("58", "52", "46", "41"),
        "10": ("80", "71", "63", "57"), "16": ("107", "96", "85", "76"),
        "25": ("138", "119", "112", "96"), "35": ("171", "147", "138", "119"),
        "50": ("209", "179", "168", "144"), "70": ("269", "229", "213", "184"),
        "95": ("328", "278", "258", "223"), "120": ("382", "322", "299", "259"),
        "150": ("441", "371", "344", "299"), "185": ("506", "424", "392", "341"),
        "240": ("599", "500", "461", "403"), "300": ("693", "576", "530", "464"),
    },
    InstallationMethod.E: {
        "1.5": ("26", "23", "22", "18.5"), "2.5": ("36", "32", "30", "25"),
        "4": ("49", "42", "40", "34"), "6": ("63", "54", "51", "43"),
        "10": ("86", "75", "70", "60"), "16": ("115", "100", "94", "80"),
        "25": ("149", "127", "119", "101"), "35": ("185", "158", "148", "126"),
        "50": ("225", "192", "180", "153"), "70": ("289", "246", "232", "196"),
        "95": ("352", "298", "282", "238"), "120": ("410", "346", "328", "276"),
        "150": ("473", "399", "379", "319"), "185": ("542", "456", "434", "364"),
        "240": ("641", "538", "514", "430"), "300": ("741", "621", "593", "497"),
    },
    InstallationMethod.F: {
        "25": ("161", "135", "131", "110"), "35": ("200", "169", "162", "137"),
        "50": ("242", "207", "196", "167"), "70": ("310", "268", "251", "216"),
        "95": ("377", "328", "304", "264"), "120": ("437", "383", "352", "308"),
        "150": ("504", "444", "406", "356"), "185": ("575", "510", "463", "409"),
        "240": ("679", "607", "546", "485"), "300": ("783", "703", "629", "561"),
        "400": ("940", "823", "754", "656"), "500": ("1083", "946", "868", "749"),
        "630": ("1254", "1088", "1005", "855"),
    },
}  # fmt: skip

#: Table 8, aluminium columns: I0 at 30 °C in the same column order as
#: `_AMPACITY_CU`. The handbook tabulates aluminium from 2.5 mm² (from 25 mm²
#: for method F). Read from the rendered pages: the PDF's text layer carries a
#: few stale values under the printed ones (A1 2.5 mm² PVC 2-loaded prints 15
#: over a hidden 14.5), and the printed value is the one reproduced here.
_AMPACITY_AL: dict[InstallationMethod, dict[str, tuple[str, str, str, str]]] = {
    InstallationMethod.A1: {
        "2.5": ("20", "19", "15", "14"), "4": ("27", "25", "20", "18.5"),
        "6": ("35", "32", "26", "24"), "10": ("48", "44", "36", "32"),
        "16": ("64", "58", "48", "43"), "25": ("84", "76", "63", "57"),
        "35": ("103", "94", "77", "70"), "50": ("125", "113", "93", "84"),
        "70": ("158", "142", "118", "107"), "95": ("191", "171", "142", "129"),
        "120": ("220", "197", "164", "149"), "150": ("253", "226", "189", "170"),
        "185": ("288", "256", "215", "194"), "240": ("338", "300", "252", "227"),
        "300": ("387", "344", "289", "261"),
    },
    InstallationMethod.A2: {
        "2.5": ("19.5", "18", "14.5", "13.5"), "4": ("26", "24", "19.5", "17.5"),
        "6": ("33", "31", "25", "23"), "10": ("45", "41", "33", "31"),
        "16": ("60", "55", "44", "41"), "25": ("78", "71", "58", "53"),
        "35": ("96", "87", "71", "65"), "50": ("115", "104", "86", "78"),
        "70": ("145", "131", "108", "98"), "95": ("175", "157", "130", "118"),
        "120": ("201", "180", "150", "135"), "150": ("230", "206", "172", "155"),
        "185": ("262", "233", "195", "176"), "240": ("307", "273", "229", "207"),
        "300": ("352", "313", "263", "237"),
    },
    InstallationMethod.B1: {
        "2.5": ("25", "22", "18.5", "16.5"), "4": ("33", "29", "25", "22"),
        "6": ("43", "38", "32", "28"), "10": ("59", "52", "44", "39"),
        "16": ("79", "71", "60", "53"), "25": ("105", "93", "79", "70"),
        "35": ("130", "116", "97", "86"), "50": ("157", "140", "118", "104"),
        "70": ("200", "179", "150", "133"), "95": ("242", "217", "181", "161"),
        "120": ("281", "251", "210", "186"),
    },
    InstallationMethod.B2: {
        "2.5": ("23", "21", "17.5", "15.5"), "4": ("31", "28", "24", "21"),
        "6": ("40", "35", "30", "27"), "10": ("54", "48", "41", "36"),
        "16": ("72", "64", "54", "48"), "25": ("94", "84", "71", "62"),
        "35": ("115", "103", "86", "77"), "50": ("138", "124", "104", "92"),
        "70": ("175", "156", "131", "116"), "95": ("210", "188", "157", "139"),
        "120": ("242", "216", "181", "160"),
    },
    InstallationMethod.C: {
        "2.5": ("26", "24", "21", "18.5"), "4": ("35", "32", "28", "25"),
        "6": ("45", "41", "36", "32"), "10": ("62", "57", "49", "44"),
        "16": ("84", "76", "66", "59"), "25": ("101", "90", "83", "73"),
        "35": ("126", "112", "103", "90"), "50": ("154", "136", "125", "110"),
        "70": ("198", "174", "160", "140"), "95": ("241", "211", "195", "170"),
        "120": ("280", "245", "226", "197"), "150": ("324", "283", "261", "227"),
        "185": ("371", "323", "298", "259"), "240": ("439", "382", "352", "305"),
        "300": ("508", "440", "406", "351"),
    },
    InstallationMethod.E: {
        "2.5": ("28", "24", "23", "19.5"), "4": ("38", "32", "31", "26"),
        "6": ("49", "42", "39", "33"), "10": ("67", "58", "54", "46"),
        "16": ("91", "77", "73", "61"), "25": ("108", "97", "89", "78"),
        "35": ("135", "120", "111", "96"), "50": ("164", "146", "135", "117"),
        "70": ("211", "187", "173", "150"), "95": ("257", "227", "210", "183"),
        "120": ("300", "263", "244", "212"), "150": ("346", "304", "282", "245"),
        "185": ("397", "347", "322", "280"), "240": ("470", "409", "380", "330"),
        "300": ("543", "471", "439", "381"),
    },
    InstallationMethod.F: {
        "25": ("121", "103", "98", "84"), "35": ("150", "129", "122", "105"),
        "50": ("184", "159", "149", "128"), "70": ("237", "206", "192", "166"),
        "95": ("289", "253", "235", "203"), "120": ("337", "296", "273", "237"),
        "150": ("389", "343", "316", "274"), "185": ("447", "395", "363", "315"),
        "240": ("530", "471", "430", "375"), "300": ("613", "547", "497", "434"),
        "400": ("740", "663", "600", "526"), "500": ("856", "770", "694", "610"),
        "630": ("996", "899", "808", "711"),
    },
}  # fmt: skip

_AMPACITY: dict[
    ConductorMaterial, dict[InstallationMethod, dict[str, tuple[str, str, str, str]]]
] = {
    ConductorMaterial.COPPER: _AMPACITY_CU,
    ConductorMaterial.ALUMINIUM: _AMPACITY_AL,
}


def _ambient_factor(ambient_temp_c: Decimal, insulation_rating_c: int) -> tuple[Decimal, int]:
    """Return k1 and the tabulated temperature it was read at.

    A temperature between two rows takes the hotter row: its factor is the
    lower of the two, so the cable it sizes is never smaller than the table
    supports. That is reading the table conservatively, not interpolating it.
    """
    column = _AMBIENT_K1.get(insulation_rating_c)
    if column is None:
        raise ValidationError(
            f"insulation rated {insulation_rating_c} °C is not tabulated; "
            "Table 4 gives PVC (70 °C) and XLPE/EPR (90 °C)"
        )
    _require_finite("ambient_temp_c", ambient_temp_c)
    rows = sorted(column)
    if ambient_temp_c < rows[0] or ambient_temp_c > rows[-1]:
        raise ValidationError(
            f"ambient {ambient_temp_c} °C is outside Table 4 for {insulation_rating_c} °C "
            f"insulation ({rows[0]}-{rows[-1]} °C); the handbook says to consult the "
            "cable manufacturer"
        )
    row = next(t for t in rows if t >= ambient_temp_c)
    return Decimal(column[row]), row


def _grouping_factor(grouped_circuits: int) -> tuple[Decimal, int]:
    """Return k2 and the tabulated circuit count it was read at.

    A count between two columns takes the larger: more circuits never reduce
    less.
    """
    counts = sorted(_GROUPING_K2)
    if grouped_circuits < 1 or grouped_circuits > counts[-1]:
        raise ValidationError(
            f"{grouped_circuits} grouped circuits is outside Table 5 (1-{counts[-1]})"
        )
    column = next(n for n in counts if n >= grouped_circuits)
    return Decimal(_GROUPING_K2[column]), column


def size_conductor(
    *,
    design_current_a: Decimal,
    installation_method: InstallationMethod,
    ambient_temp_c: Decimal,
    grouped_circuits: int,
    conductor_material: ConductorMaterial,
    insulation_rating_c: int,
    three_phase: bool = True,
) -> CableSizingResult:
    """Select the smallest conductor whose derated ampacity carries the load.

    The handbook's procedure: I'b = Ib / (k1 k2); pick the first section in
    Table 8 with I0 >= I'b; the capacity in place is Iz = I0 k1 k2.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        §2.2.1: Table 4 (k1), Table 5 item 1 (k2), Table 8 (I0), and the
        procedure summarised on p. 37. Worked examples on pp. 52-55.

    Args:
        design_current_a: Design current of the circuit, in amperes.
        installation_method: Reference installation method.
        ambient_temp_c: Ambient air temperature at the run, in degrees Celsius.
        grouped_circuits: Number of loaded circuits in the same grouping.
        conductor_material: Copper or aluminium.
        insulation_rating_c: Conductor temperature rating, 70 (PVC) or 90
            (XLPE/EPR) °C.
        three_phase: Three loaded conductors if true, two if not.

    Returns:
        The selected size with each factor applied and its source.

    Raises:
        ValidationError: If no tabulated size carries the derated current, or
            if an argument falls outside the tables.

    Methods D1, D2 and G are refused: they are not transcribed here, and a
    guessed column is the error this sourcing exists to prevent.
    """
    _require_positive("design_current_a", design_current_a)
    table = _AMPACITY[conductor_material].get(installation_method)
    if table is None:
        raise ValidationError(
            f"installation method {installation_method.value} is not supported; "
            f"supported: {', '.join(m.value for m in _AMPACITY_CU)}"
        )

    k1, temp_row = _ambient_factor(ambient_temp_c, insulation_rating_c)
    k2, count_column = _grouping_factor(grouped_circuits)
    required = design_current_a / (k1 * k2)

    index = (0 if insulation_rating_c == 90 else 2) + (1 if three_phase else 0)
    for section, row in table.items():
        ampacity = Decimal(row[index])
        if ampacity >= required:
            return CableSizingResult(
                cross_section_mm2=Decimal(section),
                derated_ampacity_a=ampacity * k1 * k2,
                applied_factors=[
                    AppliedFactor(
                        name=f"k1 ambient {temp_row} °C",
                        value=k1,
                        source=abb_citation(_TABLE_4_PAGE, "Table 4"),
                    ),
                    AppliedFactor(
                        name=f"k2 grouping {count_column} circuits",
                        value=k2,
                        source=abb_citation(_TABLE_5_PAGE, "Table 5"),
                    ),
                ],
            )
    raise ValidationError(
        f"no tabulated {conductor_material.value} section carries {required.quantize(Decimal('0.01'))} A "
        f"by method {installation_method.value}; use parallel conductors"
    )


def ampacity_citation(installation_method: InstallationMethod) -> Citation:
    """Cite the Table 8 page for an installation method.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        Table 8, pp. 38-40 as printed.

    Args:
        installation_method: A method `size_conductor` supports.

    Returns:
        The citation.
    """
    return abb_citation(_TABLE_8_PAGES[installation_method], "Table 8")


#: Phase-to-phase voltage drop, in volts per ampere per kilometre.
#:
#: Schneider Electric, *Electrical Installation Guide* 2010, Fig. G28. Keyed by
#: copper cross-section; the guide's aluminium column is offset (its first row
#: is 6 mm² Cu / 10 mm² Al) and is not reproduced here: aluminium is read from
#: ABB's own tables below instead, which need no offset.
#:
#: Each tuple is (single-phase motor cos 0.8, single-phase motor cos 0.35,
#: single-phase lighting, three-phase motor cos 0.8, three-phase motor
#: cos 0.35, three-phase lighting), matching the guide's column order.
_VOLTAGE_DROP_MV_PER_A_KM: dict[Decimal, tuple[str, str, str, str, str, str]] = {
    Decimal("1.5"): ("24", "10.6", "30", "20", "9.4", "25"),
    Decimal("2.5"): ("14.4", "6.4", "18", "12", "5.7", "15"),
    Decimal("4"): ("9.1", "4.1", "11.2", "8", "3.6", "9.5"),
    Decimal("6"): ("6.1", "2.9", "7.5", "5.3", "2.5", "6.2"),
    Decimal("10"): ("3.7", "1.7", "4.5", "3.2", "1.5", "3.6"),
    Decimal("16"): ("2.36", "1.15", "2.8", "2.05", "1", "2.4"),
    Decimal("25"): ("1.5", "0.75", "1.8", "1.3", "0.65", "1.5"),
    Decimal("35"): ("1.15", "0.6", "1.29", "1", "0.52", "1.1"),
    Decimal("50"): ("0.86", "0.47", "0.95", "0.75", "0.41", "0.77"),
    Decimal("70"): ("0.64", "0.37", "0.64", "0.56", "0.32", "0.55"),
    Decimal("95"): ("0.48", "0.30", "0.47", "0.42", "0.26", "0.4"),
    Decimal("120"): ("0.39", "0.26", "0.37", "0.34", "0.23", "0.31"),
    Decimal("150"): ("0.33", "0.24", "0.30", "0.29", "0.21", "0.27"),
    Decimal("185"): ("0.29", "0.22", "0.24", "0.25", "0.19", "0.2"),
    Decimal("240"): ("0.24", "0.2", "0.19", "0.21", "0.17", "0.16"),
    Decimal("300"): ("0.21", "0.19", "0.15", "0.18", "0.16", "0.13"),
}

#: Specific voltage drop of aluminium cables, in V/(A·km), by power factor and
#: cross-section.
#:
#: ABB, *Electrical installation handbook* Vol. 2, §2.2.2 Tables 8-12 (one per
#: cos φ: 1, 0.9, 0.85, 0.8, 0.75), computed by the handbook from its Table 2
#: resistance and reactance at 80 °C with formula (1). Each tuple is
#: (single-core single-phase, single-core three-phase, two-core single-phase,
#: three-core three-phase), the handbook's column order.
_VOLTAGE_DROP_AL: dict[Decimal, dict[Decimal, tuple[str, str, str, str]]] = {
    Decimal("1"): {
        Decimal("1.5"): ("48.77", "42.23", "49.76", "43.09"),
        Decimal("2.5"): ("29.36", "25.43", "29.92", "25.91"),
        Decimal("4"): ("18.35", "15.89", "18.72", "16.21"),
        Decimal("6"): ("12.22", "10.59", "12.46", "10.79"),
        Decimal("10"): ("7.38", "6.39", "7.48", "6.48"),
        Decimal("16"): ("4.65", "4.02", "4.71", "4.08"),
        Decimal("25"): ("2.93", "2.54", "2.99", "2.59"),
        Decimal("35"): ("2.11", "1.83", "2.15", "1.87"),
        Decimal("50"): ("1.56", "1.35", "1.59", "1.38"),
        Decimal("70"): ("1.08", "0.94", "1.10", "0.95"),
        Decimal("95"): ("0.78", "0.67", "0.79", "0.69"),
        Decimal("120"): ("0.62", "0.54", "0.63", "0.55"),
        Decimal("150"): ("0.50", "0.44", "0.52", "0.45"),
        Decimal("185"): ("0.41", "0.35", "0.41", "0.36"),
        Decimal("240"): ("0.31", "0.27", "0.32", "0.28"),
        Decimal("300"): ("0.25", "0.22", "0.26", "0.22"),
    },
    Decimal("0.9"): {
        Decimal("1.5"): ("44.04", "38.14", "44.88", "38.87"),
        Decimal("2.5"): ("26.56", "23.00", "27.02", "23.40"),
        Decimal("4"): ("16.64", "14.41", "16.93", "14.66"),
        Decimal("6"): ("11.12", "9.63", "11.29", "9.78"),
        Decimal("10"): ("6.75", "5.84", "6.81", "5.89"),
        Decimal("16"): ("4.28", "3.71", "4.31", "3.73"),
        Decimal("25"): ("2.73", "2.36", "2.76", "2.39"),
        Decimal("35"): ("1.99", "1.72", "2.01", "1.74"),
        Decimal("50"): ("1.49", "1.29", "1.50", "1.30"),
        Decimal("70"): ("1.06", "0.92", "1.06", "0.91"),
        Decimal("95"): ("0.78", "0.68", "0.78", "0.68"),
        Decimal("120"): ("0.64", "0.55", "0.63", "0.55"),
        Decimal("150"): ("0.53", "0.46", "0.53", "0.46"),
        Decimal("185"): ("0.44", "0.38", "0.44", "0.38"),
        Decimal("240"): ("0.36", "0.31", "0.35", "0.30"),
        Decimal("300"): ("0.30", "0.26", "0.30", "0.26"),
    },
    Decimal("0.85"): {
        Decimal("1.5"): ("41.63", "36.05", "42.42", "36.73"),
        Decimal("2.5"): ("25.12", "21.75", "25.55", "22.12"),
        Decimal("4"): ("15.75", "13.64", "16.02", "13.87"),
        Decimal("6"): ("10.53", "9.12", "10.69", "9.26"),
        Decimal("10"): ("6.40", "5.54", "6.45", "5.58"),
        Decimal("16"): ("4.07", "3.52", "4.09", "3.54"),
        Decimal("25"): ("2.60", "2.25", "2.63", "2.27"),
        Decimal("35"): ("1.90", "1.65", "1.91", "1.66"),
        Decimal("50"): ("1.43", "1.24", "1.43", "1.24"),
        Decimal("70"): ("1.02", "0.88", "1.01", "0.88"),
        Decimal("95"): ("0.76", "0.66", "0.76", "0.65"),
        Decimal("120"): ("0.63", "0.54", "0.61", "0.53"),
        Decimal("150"): ("0.53", "0.46", "0.52", "0.45"),
        Decimal("185"): ("0.44", "0.38", "0.43", "0.37"),
        Decimal("240"): ("0.36", "0.31", "0.35", "0.30"),
        Decimal("300"): ("0.31", "0.27", "0.30", "0.26"),
    },
    Decimal("0.8"): {
        Decimal("1.5"): ("39.22", "33.96", "39.95", "34.59"),
        Decimal("2.5"): ("23.67", "20.50", "24.07", "20.84"),
        Decimal("4"): ("14.85", "12.86", "15.09", "13.07"),
        Decimal("6"): ("9.94", "8.61", "10.08", "8.73"),
        Decimal("10"): ("6.05", "5.24", "6.09", "5.27"),
        Decimal("16"): ("3.85", "3.34", "3.87", "3.35"),
        Decimal("25"): ("2.47", "2.14", "2.49", "2.16"),
        Decimal("35"): ("1.81", "1.57", "1.82", "1.57"),
        Decimal("50"): ("1.37", "1.18", "1.37", "1.18"),
        Decimal("70"): ("0.98", "0.85", "0.97", "0.84"),
        Decimal("95"): ("0.74", "0.64", "0.73", "0.63"),
        Decimal("120"): ("0.61", "0.53", "0.59", "0.51"),
        Decimal("150"): ("0.51", "0.45", "0.50", "0.44"),
        Decimal("185"): ("0.43", "0.38", "0.42", "0.36"),
        Decimal("240"): ("0.36", "0.31", "0.34", "0.30"),
        Decimal("300"): ("0.31", "0.27", "0.30", "0.26"),
    },
    Decimal("0.75"): {
        Decimal("1.5"): ("36.80", "31.87", "37.47", "32.45"),
        Decimal("2.5"): ("22.23", "19.25", "22.58", "19.56"),
        Decimal("4"): ("13.95", "12.08", "14.17", "12.27"),
        Decimal("6"): ("9.35", "8.09", "9.47", "8.20"),
        Decimal("10"): ("5.69", "4.93", "5.72", "4.96"),
        Decimal("16"): ("3.63", "3.15", "3.64", "3.15"),
        Decimal("25"): ("2.34", "2.02", "2.35", "2.03"),
        Decimal("35"): ("1.72", "1.49", "1.72", "1.49"),
        Decimal("50"): ("1.30", "1.13", "1.30", "1.12"),
        Decimal("70"): ("0.94", "0.81", "0.92", "0.80"),
        Decimal("95"): ("0.71", "0.62", "0.70", "0.60"),
        Decimal("120"): ("0.59", "0.51", "0.57", "0.49"),
        Decimal("150"): ("0.50", "0.43", "0.49", "0.42"),
        Decimal("185"): ("0.42", "0.37", "0.41", "0.35"),
        Decimal("240"): ("0.35", "0.31", "0.34", "0.29"),
        Decimal("300"): ("0.31", "0.27", "0.29", "0.25"),
    },
}

#: PDF page of each aluminium specific-voltage-drop table, by power factor.
_VOLTAGE_DROP_AL_PAGES: dict[Decimal, tuple[int, str]] = {
    Decimal("1"): (64, "§2.2.2 Table 8"),
    Decimal("0.9"): (65, "§2.2.2 Table 9"),
    Decimal("0.85"): (65, "§2.2.2 Table 10"),
    Decimal("0.8"): (66, "§2.2.2 Table 11"),
    Decimal("0.75"): (66, "§2.2.2 Table 12"),
}

#: Methods whose Table 8 pictures show single-core cables or insulated
#: conductors; the rest show a multi-core cable. It picks the voltage-drop
#: column, since a single-core formation has the higher reactance.
_SINGLE_CORE_METHODS = frozenset(
    {InstallationMethod.A1, InstallationMethod.B1, InstallationMethod.F}
)

_SCHNEIDER_G28 = Citation(
    document_id="schneider-eig-2010",
    document_title="Electrical Installation Guide 2010",
    manufacturer="Schneider Electric",
    section="Chapter G, Fig. G28",
)

#: The power factor the guide's "normal service" motor columns are tabulated at.
_COS_PHI_NORMAL = Decimal("0.8")

#: The power factor its "start-up" motor columns are tabulated at.
_COS_PHI_STARTUP = Decimal("0.35")


def _require_finite(name: str, value: Decimal) -> None:
    """Refuse ``NaN``, ``sNaN`` and infinities.

    Args:
        name: The argument's name, for the message.
        value: The argument.

    Raises:
        ValidationError: If ``value`` is not a finite number.
    """
    if not value.is_finite():
        raise ValidationError(f"{name} must be a finite number, got {value}")


def _require_positive(name: str, value: Decimal) -> None:
    """Refuse anything but a finite number above zero.

    Args:
        name: The argument's name, for the message.
        value: The argument.

    Raises:
        ValidationError: If ``value`` is not finite, or not above zero.
    """
    _require_finite(name, value)
    if value <= 0:
        raise ValidationError(f"{name} must be positive, got {value}")


class LoadType(StrEnum):
    """Which of Fig. G28's column pairs applies.

    The guide tabulates motor and lighting circuits separately at the same
    cross-section, and they differ — a 70 mm² three-phase run is 0.56 V/A/km
    for a motor and 0.55 for lighting. Its own Example 2 uses the lighting
    column for a line supplying lighting circuits, so this is a real
    distinction the caller must make rather than a nuance to average away.
    """

    MOTOR = "motor"
    LIGHTING = "lighting"


def voltage_drop(
    *,
    current_a: Decimal,
    length_m: Decimal,
    cross_section_mm2: Decimal,
    conductor_material: ConductorMaterial,
    power_factor: Decimal,
    three_phase: bool,
    load_type: LoadType = LoadType.MOTOR,
    installation_method: InstallationMethod | None = None,
) -> Decimal:
    """Compute the line voltage drop over a cable run, in volts.

    Uses the tabulated V/A/km method rather than a resistivity calculation, so
    reactance is included for larger cross-sections.

    Source:
        Copper: Schneider Electric, *Electrical Installation Guide* 2010,
        Chapter G, Fig. G28 ("Phase-to-phase voltage drop ΔU for a circuit,
        in volts per ampere per km"), with the method and worked examples
        from §3. Aluminium: ABB, *Electrical installation handbook* Vol. 2
        (1SDC010001D0204), §2.2.2 Tables 8-12, with ΔU = ΔUx Ib L.

    Args:
        current_a: Load current, in amperes.
        length_m: One-way run length, in metres.
        cross_section_mm2: Conductor cross-sectional area, in mm².
        conductor_material: Copper or aluminium.
        power_factor: Load power factor. For a copper motor circuit this
            selects the guide's column and must be exactly 0.8 (normal
            service) or 0.35 (start-up) — see below. For aluminium it selects
            ABB's table and must be 1, 0.9, 0.85, 0.8 or 0.75.
        three_phase: ``True`` for three-phase, ``False`` for single-phase.
        load_type: Motor or lighting. Fig. G28 tabulates these separately and
            they differ; the guide's Example 2 uses the lighting column.
            Aluminium is tabulated by power factor alone, so it is not used
            there.
        installation_method: Required for aluminium: whether the method's
            cables are single-core picks the handbook's column.

    Returns:
        The phase-to-phase voltage drop in volts.

    Raises:
        ValidationError: If the current or length is not a finite positive
            number, the cross-section is not tabulated, or the power factor
            is not one the table for the conductor tabulates.

    **Interpolation is refused, not performed.** Fig. G28 is a table of
    measured values at two power factors, not a curve — the relationship
    between them includes reactance and is not linear in cos φ. Accepting
    cos φ = 0.6 and interpolating would return a confident number the guide
    does not support, for a circuit whose conductor sizing depends on it.
    A caller outside the table gets a refusal it can surface, which is the
    behaviour AI-005's spec asks for.

    **Every quantity is checked before any lookup.** ``Decimal`` admits
    ``NaN``, ``sNaN`` and ``Infinity``, and without this a negative current
    returned a negative drop, ``NaN`` and ``Infinity`` came back as results,
    and ``sNaN`` escaped as a ``TypeError`` from hashing it into the table --
    each a number or a crash where the contract promises a refusal. A zero
    current or length is refused too: it is a run carrying nothing, and the
    only answer to it is one no caller needed to ask for.
    """
    _require_positive("current_a", current_a)
    _require_positive("length_m", length_m)
    _require_finite("cross_section_mm2", cross_section_mm2)
    _require_finite("power_factor", power_factor)

    if conductor_material is ConductorMaterial.ALUMINIUM:
        return _aluminium_drop(
            current_a=current_a,
            length_m=length_m,
            cross_section_mm2=cross_section_mm2,
            power_factor=power_factor,
            three_phase=three_phase,
            installation_method=installation_method,
        )

    row = _VOLTAGE_DROP_MV_PER_A_KM.get(cross_section_mm2)
    if row is None:
        raise ValidationError(
            f"{cross_section_mm2} mm2 is not a cross-section tabulated in Fig. G28"
        )

    if load_type is LoadType.LIGHTING:
        index = 5 if three_phase else 2
    elif power_factor == _COS_PHI_NORMAL:
        index = 3 if three_phase else 0
    elif power_factor == _COS_PHI_STARTUP:
        index = 4 if three_phase else 1
    else:
        raise ValidationError(
            f"power factor {power_factor} is not tabulated; Fig. G28 gives motor "
            f"circuits at {_COS_PHI_NORMAL} (normal service) and "
            f"{_COS_PHI_STARTUP} (start-up) only, and does not support "
            "interpolation between them"
        )

    per_a_km = Decimal(row[index])
    # The guide's formula: drop = (V/A/km) x current x length in km.
    return per_a_km * current_a * (length_m / Decimal("1000"))


def _aluminium_drop(
    *,
    current_a: Decimal,
    length_m: Decimal,
    cross_section_mm2: Decimal,
    power_factor: Decimal,
    three_phase: bool,
    installation_method: InstallationMethod | None,
) -> Decimal:
    """Voltage drop of an aluminium run from ABB's specific-drop tables.

    Schneider's Fig. G28 aluminium column is offset against its copper one
    and is not transcribed; ABB tabulates aluminium outright, per power
    factor and cable formation, and that is what is read here. No power
    factor between two tables is interpolated.
    """
    if installation_method is None:
        raise ValidationError(
            "aluminium voltage drop needs the installation method: the handbook "
            "tabulates single-core and multi-core cables separately"
        )
    table = _VOLTAGE_DROP_AL.get(power_factor)
    if table is None:
        raise ValidationError(
            f"power factor {power_factor} is not tabulated for aluminium; ABB gives "
            f"{', '.join(str(c) for c in _VOLTAGE_DROP_AL)} and does not support "
            "interpolation between them"
        )
    row = table.get(cross_section_mm2)
    if row is None:
        raise ValidationError(
            f"{cross_section_mm2} mm2 is not a cross-section tabulated for aluminium "
            "voltage drop (1.5-300 mm2)"
        )
    single_core = installation_method in _SINGLE_CORE_METHODS
    index = (0 if single_core else 2) + (1 if three_phase else 0)
    return Decimal(row[index]) * current_a * (length_m / Decimal("1000"))


def voltage_drop_citation(conductor_material: ConductorMaterial, power_factor: Decimal) -> Citation:
    """Cite the table a `voltage_drop` result was read from.

    Source:
        Copper: Schneider Electric, *Electrical Installation Guide* 2010,
        Fig. G28. Aluminium: ABB, *Electrical installation handbook* Vol. 2
        (1SDC010001D0204), §2.2.2 Tables 8-12.

    Args:
        conductor_material: The conductor `voltage_drop` was given.
        power_factor: The power factor it was given.

    Returns:
        The citation.
    """
    if conductor_material is ConductorMaterial.ALUMINIUM and power_factor in (
        _VOLTAGE_DROP_AL_PAGES
    ):
        page, section = _VOLTAGE_DROP_AL_PAGES[power_factor]
        return abb_citation(page, section)
    return _SCHNEIDER_G28


def derating_factor(
    *,
    ambient_temp_c: Decimal,
    grouped_circuits: int,
    insulation_rating_c: int,
) -> Decimal:
    """Return the combined ambient and grouping derating factor, k1 x k2.

    Source:
        ABB, *Electrical installation handbook* Vol. 2 (1SDC010001D0204),
        Table 4 (ambient air) and Table 5 item 1 (bunched), reproducing
        IEC 60364-5-52 Tables B.52.14 and B.52.17.

    Args:
        ambient_temp_c: Ambient temperature, in degrees Celsius.
        grouped_circuits: Number of loaded circuits in the same grouping.
        insulation_rating_c: Conductor temperature rating, 70 or 90 °C.

    Returns:
        The product of both factors.

    Raises:
        ValidationError: If the temperature or grouping count is off-table.
    """
    k1, _ = _ambient_factor(ambient_temp_c, insulation_rating_c)
    k2, _ = _grouping_factor(grouped_circuits)
    return k1 * k2
