"""Variable frequency drive selection calculations.

Pure functions. Each formula cites the manufacturer guide it came from.

The catalogue is the ACS880-01 wall-mounted range, IP21: types ...-3 on
380-415 V and ...-5 on 415-500 V (IEC ratings at Un = 400 and 500 V), and
...-7 on 525-600 V (UL ratings at Un = 575 V) and 660-690 V (IEC ratings at
Un = 690 V). Other ranges, voltages and enclosures have their own tables and
derating curves and are refused rather than read from these.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    """One row of the IEC ratings table."""

    type_code: str
    frame: str
    #: I2: nominal output current, continuous with no overload.
    nominal_a: str
    #: IHd: continuous current allowing 50 % overload for 1 min every 5 min;
    #: ``None`` where the manual footnotes a smaller overload for this type.
    heavy_duty_a: str | None


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


def _manual(page: int, section: str) -> Citation:
    """Cite a page of the ACS880-01 hardware manual."""
    return Citation(
        document_id=HARDWARE_MANUAL_ID,
        document_title=HARDWARE_MANUAL_TITLE,
        manufacturer="ABB",
        page=page,
        section=section,
    )


def _require_fraction(name: str, value: Decimal) -> None:
    """Refuse anything outside (0, 1]."""
    if not value.is_finite() or value <= 0 or value > 1:
        raise ValidationError(f"{name} must be in (0, 1], got {value}")


def _catalogue(supply_voltage_v: Decimal) -> _Catalogue:
    """Return the range rated for a supply, or refuse one no range is."""
    if supply_voltage_v.is_finite():
        for catalogue in _CATALOGUES:
            if catalogue.low_v <= supply_voltage_v <= catalogue.high_v:
                return catalogue
    bands = ", ".join(f"{c.low_v}-{c.high_v} V ({c.suffix})" for c in _CATALOGUES)
    raise ValidationError(
        f"{supply_voltage_v} V is outside the ACS880-01 supply ranges tabulated here: "
        f"{bands}; other voltages are not tabulated here"
    )


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


def altitude_derate(*, altitude_m: Decimal) -> Decimal:
    """Return the output-current derating factor for installation altitude.

    Unity to 1000 m, then one percentage point per 100 m. The manual allows
    the derating to be reduced below 40 °C ambient; that relief is not
    applied, so the factor is never kinder than the manual's.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Altitude derating",
        p. 244 as printed; maximum installation altitude 4000 m.

    Args:
        altitude_m: Installation altitude above sea level, in metres.

    Returns:
        The derating factor, in the range (0, 1].

    Raises:
        ValidationError: If the altitude is negative or above 4000 m.
    """
    if not altitude_m.is_finite() or altitude_m < 0 or altitude_m > _MAX_ALTITUDE_M:
        raise ValidationError(
            f"altitude {altitude_m} m is outside 0-{_MAX_ALTITUDE_M} m, the maximum "
            "permitted installation altitude"
        )
    if altitude_m <= _FULL_CURRENT_UP_TO_M:
        return Decimal(1)
    return 1 - (altitude_m - _FULL_CURRENT_UP_TO_M) / Decimal(100) / Decimal(100)


def temperature_derate(*, ambient_temp_c: Decimal) -> Decimal:
    """Return the output-current derating factor for surrounding air temperature.

    Unity to +40 °C, then 1 % per added degree to +55 °C.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Surrounding air
        temperature derating", p. 243 as printed, for IP21 frames R1-R9e.

    Args:
        ambient_temp_c: Surrounding air temperature, in °C.

    Returns:
        The derating factor, 0.85 to 1.

    Raises:
        ValidationError: If the temperature is outside -15...+55 °C.
    """
    if (
        not ambient_temp_c.is_finite()
        or ambient_temp_c < _MIN_AMBIENT_C
        or ambient_temp_c > _MAX_AMBIENT_C
    ):
        raise ValidationError(
            f"ambient {ambient_temp_c} °C is outside the drive's range "
            f"({_MIN_AMBIENT_C}...+{_MAX_AMBIENT_C} °C)"
        )
    if ambient_temp_c <= _FULL_CURRENT_UP_TO_C:
        return Decimal(1)
    return 1 - (ambient_temp_c - _FULL_CURRENT_UP_TO_C) / Decimal(100)


def select_frame(
    *,
    required_current_a: Decimal,
    supply_voltage_v: Decimal,
    duty_class: DutyClass,
    altitude_m: Decimal,
    ambient_temp_c: Decimal,
) -> VfdSelectionResult:
    """Select the smallest catalogue drive whose derated current meets the demand.

    Each type's rating for the duty -- I2 for normal, IHd for heavy -- is
    multiplied by the temperature and altitude factors, and the first type at
    or above the required current is chosen.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Electrical ratings",
        IEC ratings at Un = 400 V, 500 V and 690 V (pp. 234-238 as printed)
        and UL ratings at Un = 575 V (pp. 240-241),
        with the deratings on pp. 243-244.

    Args:
        required_current_a: Continuous current the motor demands, in amperes.
        supply_voltage_v: Line-to-line supply voltage, in volts.
        duty_class: Normal or heavy duty.
        altitude_m: Installation altitude, in metres.
        ambient_temp_c: Ambient temperature at the drive, in °C.

    Returns:
        The selected type with its derated current and every factor applied.

    Raises:
        ValidationError: If no range is rated for the supply, the site is off
            the derating curves, or no catalogue type covers the demand.
    """
    if not required_current_a.is_finite() or required_current_a <= 0:
        raise ValidationError(f"required_current_a must be positive, got {required_current_a}")
    catalogue = _catalogue(supply_voltage_v)
    k_temp = temperature_derate(ambient_temp_c=ambient_temp_c)
    k_alt = altitude_derate(altitude_m=altitude_m)
    factor = k_temp * k_alt

    for rating in catalogue.ratings:
        rated = rating.nominal_a if duty_class is DutyClass.NORMAL else rating.heavy_duty_a
        if rated is None:
            continue
        derated = Decimal(rated) * factor
        if derated >= required_current_a:
            return VfdSelectionResult(
                frame_reference=f"{rating.type_code} ({rating.frame})",
                rated_output_current_a=derated,
                applied_factors=[
                    AppliedFactor(
                        name=f"temperature {ambient_temp_c} °C",
                        value=k_temp,
                        source=_manual(_DERATING_PAGES[0], "Surrounding air temperature"),
                    ),
                    AppliedFactor(
                        name=f"altitude {altitude_m} m",
                        value=k_alt,
                        source=_manual(_DERATING_PAGES[1], "Altitude derating"),
                    ),
                ],
            )
    raise ValidationError(
        f"no ACS880-01-xxxx{catalogue.suffix} type supplies {required_current_a.quantize(Decimal('0.1'))} A "
        f"for {duty_class.value} duty at this site; consider a cabinet-built ACS880"
    )


def ratings_citation(supply_voltage_v: Decimal) -> Citation:
    """Cite the ratings table for a supply.

    Source:
        ABB ACS880-01 hardware manual (3AUA0000078093), "Electrical ratings".

    Args:
        supply_voltage_v: The supply `select_frame` was given.

    Returns:
        The citation.

    Raises:
        ValidationError: If no range is rated for the supply.
    """
    catalogue = _catalogue(supply_voltage_v)
    return _manual(catalogue.page, catalogue.section)


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
