"""Variable frequency drive selection calculations.

Pure functions. Each formula cites the manufacturer guide it came from.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas.calculations import DutyClass, VfdSelectionResult


def required_drive_current_a(
    *,
    motor_power_kw: Decimal,
    supply_voltage_v: Decimal,
    motor_efficiency: Decimal,
    motor_power_factor: Decimal,
    duty_class: DutyClass,
) -> Decimal:
    """Compute the continuous output current a drive must supply for a motor.

    Normal-duty selections size on rated current; heavy-duty selections add the
    150%-for-60s overload allowance the guide specifies.

    Source:
        ABB ACS880 Hardware Manual (3AUA0000078093), §3 "Selecting the drive",
        ratings tables 3.1–3.3; cross-checked against Siemens SINAMICS G120
        Operating Instructions, §4.2.

    Args:
        motor_power_kw: Motor shaft rating, in kilowatts.
        supply_voltage_v: Line-to-line supply voltage, in volts.
        motor_efficiency: Motor efficiency, 0 < η ≤ 1.
        motor_power_factor: Motor power factor at rated load, 0 < pf ≤ 1.
        duty_class: Normal or heavy duty.

    Returns:
        The required continuous drive output current, in amperes.

    Raises:
        ValidationError: If efficiency or power factor is outside (0, 1].
    """
    raise NotImplementedError


def altitude_derate(*, altitude_m: Decimal) -> Decimal:
    """Return the output-current derating factor for installation altitude.

    Unity to 1000 m, then a linear reduction with height.

    Source:
        ABB ACS880 Hardware Manual (3AUA0000078093), §5 "Ambient conditions",
        altitude derating curve.

    Args:
        altitude_m: Installation altitude above sea level, in metres.

    Returns:
        The derating factor, in the range (0, 1].

    Raises:
        ValidationError: If the altitude exceeds the curve's stated maximum.
    """
    raise NotImplementedError


def select_frame(
    *,
    required_current_a: Decimal,
    supply_voltage_v: Decimal,
    duty_class: DutyClass,
    altitude_m: Decimal,
    ambient_temp_c: Decimal,
) -> VfdSelectionResult:
    """Select the smallest catalogue drive frame meeting the derated demand.

    Source:
        ABB ACS880 Hardware Manual (3AUA0000078093), §3 ratings tables, with
        temperature derating from §5.

    Args:
        required_current_a: Continuous current the motor demands, in amperes.
        supply_voltage_v: Line-to-line supply voltage, in volts.
        duty_class: Normal or heavy duty.
        altitude_m: Installation altitude, in metres.
        ambient_temp_c: Ambient temperature at the enclosure, in °C.

    Returns:
        The selected frame with applied derates and cited sources.

    Raises:
        ValidationError: If no catalogue frame covers the derated demand.
    """
    raise NotImplementedError
