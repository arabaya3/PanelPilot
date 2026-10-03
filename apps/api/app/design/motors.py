"""Motor circuits: how a three-phase motor is started, from cited tables.

A load with a ``starter`` is a squirrel-cage motor rated by its shaft power.
Its rated current is, in order of preference:

* P / (sqrt(3) U eta cos phi) (ABB Technical guide No. 7, formulas 3.15 and
  3.16) at the load's own power factor, where it gives one;
* for a coordinated starter, the Ir the handbook's table prints for a motor
  of exactly that power;
* otherwise the same formula at cos phi 0.85 and an efficiency of 0.9.

The board says which, for the engineer to check against the nameplate.

* **Direct on line / star-delta:** a Type 2 coordinated set -- moulded-case
  breaker, contactor(s) and thermal overload relay -- from the coordination
  tables of ABB's *Electrical installation handbook* Vol. 2, §3.3
  (``motor_starter``). In star-delta the relay sits in the phase windings,
  so it is set to Ir / sqrt(3), and the motor is fed by six conductors each
  carrying Ir / sqrt(3).
* **Drive:** the smallest ACS880-01 that supplies Ir for normal duty at the
  board's ambient (``vfd_selection``), behind the aR fuses its manual lists.

Each device names its article in ``part_key`` as ``"<maker>/<type>"``, so the
parts list, the drawings and the quotation can show it before a company
catalogue is loaded; :func:`parts_for` turns those keys into parts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.ai.tools import motor_starter, vfd_selection
from app.core.errors import ValidationError
from app.design import mccb
from app.design.notes import note
from app.models.schemas.calculations import DutyClass, StartType
from app.models.schemas.design import (
    Board,
    DesignNote,
    Device,
    DeviceKind,
    LoadInput,
    MotorStarter,
    Part,
    Supply,
)

#: Assumed where a load gives none: typical of IE2/IE3 motors of a few kW.
DEFAULT_MOTOR_EFFICIENCY = Decimal("0.9")
DEFAULT_MOTOR_POWER_FACTOR = Decimal("0.85")

_SQRT3 = Decimal(3).sqrt()
_CENT = Decimal("0.01")

_HANDBOOK = "ABB Electrical installation handbook Vol. 2 (1SDC010001D0204), §3.3"
_DRIVE_MANUAL = "ABB ACS880-01 hardware manual (3AUA0000078093)"


@dataclass
class MotorCircuit:
    """What a motor's circuit is built from.

    Attributes:
        devices: In order from the busbar; the first is the protective device.
        current_a: The motor's rated current Ir, the circuit's Ib.
        cable_current_a: What each conductor to the motor carries.
        cable_cores: Cores of the motor cable, protective conductor included.
        notes: What the board should say about it.
        magnetic_trip_a: The starter breaker's magnetic threshold I3, as its
            coordination table prints it; ``None`` behind a drive's fuses.
    """

    devices: list[Device]
    current_a: Decimal
    cable_current_a: Decimal
    cable_cores: int
    notes: list[DesignNote] = field(default_factory=list)
    magnetic_trip_a: Decimal | None = None


def _start_type(starter: MotorStarter) -> StartType | None:
    return {
        MotorStarter.DIRECT_ON_LINE: StartType.DOL,
        MotorStarter.STAR_DELTA: StartType.STAR_DELTA,
    }.get(starter)


def motor_current(load: LoadInput, supply: Supply) -> tuple[Decimal, DesignNote]:
    """A motor's rated current from its shaft power, and where it came from.

    Args:
        load: The motor; ``power_kw`` is its rated (shaft) power.
        supply: The board's supply.

    Returns:
        Ir in amperes, to the hundredth, and the note saying how it was
        reached.
    """
    start = _start_type(load.starter) if load.starter else None
    if load.power_factor is None and start is not None:
        typical = motor_starter.typical_motor_current(
            motor_power_kw=load.power_kw, start=start, supply_voltage_v=supply.voltage_v
        )
        if typical is not None:
            return typical, note(
                "motor_current_table",
                load=load.description,
                current=typical,
                power=load.power_kw,
                source=_HANDBOOK,
            )
    power_factor = load.power_factor or DEFAULT_MOTOR_POWER_FACTOR
    current = vfd_selection.required_drive_current_a(
        motor_power_kw=load.power_kw,
        supply_voltage_v=supply.voltage_v,
        motor_efficiency=DEFAULT_MOTOR_EFFICIENCY,
        motor_power_factor=power_factor,
        duty_class=DutyClass.NORMAL,
    )
    current = current.quantize(_CENT)
    return current, note(
        "motor_current_formula",
        load=load.description,
        current=current,
        power=load.power_kw,
        power_factor=power_factor,
        efficiency=DEFAULT_MOTOR_EFFICIENCY,
    )


def _breaker_rating(article: str) -> tuple[str | None, Decimal | None]:
    """Read "MA 20" or "PR221-I In320" off a breaker article: release and rating."""
    words = article.replace("In", " In ").split()
    for index, word in enumerate(words):
        for release in ("MF", "MA"):
            if word.startswith(release):
                value = word[len(release) :] or (words[index + 1] if index + 1 < len(words) else "")
                return release, Decimal(value) if value else None
        if word == "In" and index + 1 < len(words):
            return (f"{words[index - 1]} In" if index else "In"), Decimal(words[index + 1])
    return None, None


def _coordinated(
    index: int, load: LoadInput, supply: Supply, current: Decimal, star_delta: bool
) -> MotorCircuit:
    start = StartType.STAR_DELTA if star_delta else StartType.DOL
    selection = motor_starter.select_starter(
        motor_power_kw=load.power_kw,
        motor_current_a=current,
        start=start,
        supply_voltage_v=supply.voltage_v,
        fault_level_ka=supply.fault_level_ka,
    )
    row = selection.row
    # The drawing fonts carry no delta.
    section = (selection.source.section or "").replace("\u0394", "D")
    prefix = f"c{index + 1}"
    release, rating = _breaker_rating(row.breaker)
    breaker = Device(
        id=f"{prefix}-breaker",
        kind=DeviceKind.CIRCUIT_BREAKER,
        part_key=f"ABB/{row.breaker}",
        poles=3,
        rated_current_a=rating,
        curve=release,
        description=f"{load.description} motor protection",
    )
    devices = [breaker]
    roles = ("line", "delta", "star") if star_delta else ("line",)
    upstream = breaker.id
    for role, contactor in zip(roles, row.contactors, strict=True):
        device = Device(
            id=f"{prefix}-{role}",
            kind=DeviceKind.CONTACTOR,
            part_key=f"ABB/{contactor}",
            poles=3,
            description=f"{load.description} {role} contactor",
            upstream_id=upstream,
        )
        devices.append(device)
        if role == "line":
            upstream = device.id
    setting = (current / _SQRT3).quantize(_CENT) if star_delta else current
    notes: list[DesignNote] = []
    if row.overload:
        devices.append(
            Device(
                id=f"{prefix}-overload",
                kind=DeviceKind.OVERLOAD_RELAY,
                part_key=f"ABB/{row.overload}",
                poles=3,
                rated_current_a=setting,
                description=f"{load.description} overload, set to {setting} A",
                upstream_id=devices[1].id,
            )
        )
    else:
        notes.append(note("no_overload_row", load=load.description))
    notes.append(
        note(
            "starter_table",
            load=load.description,
            starter=load.starter.value if load.starter else start.value,
            source=f"{_HANDBOOK} {section}",
        )
    )
    if star_delta:
        notes.append(note("star_delta_interlock", load=load.description))
    return MotorCircuit(
        devices=devices,
        current_a=current,
        cable_current_a=setting,
        cable_cores=7 if star_delta else 4,
        notes=notes,
        magnetic_trip_a=Decimal(row.magnetic_trip_a),
    )


def _drive(
    index: int, load: LoadInput, supply: Supply, current: Decimal, ambient_c: Decimal
) -> MotorCircuit:
    result = vfd_selection.select_frame(
        required_current_a=current,
        supply_voltage_v=supply.voltage_v,
        duty_class=DutyClass.NORMAL,
        altitude_m=Decimal(0),
        ambient_temp_c=ambient_c,
    )
    type_code = result.frame_reference.split(" ", 1)[0]
    fuse = vfd_selection.input_fuse(type_code=type_code)
    prefix = f"c{index + 1}"
    fuses = Device(
        id=f"{prefix}-fuses",
        kind=DeviceKind.FUSE,
        part_key=f"Bussmann/{fuse.bussmann}",
        poles=3,
        rated_current_a=Decimal(fuse.amps),
        curve="aR",
        description=f"{load.description} drive input fuses",
    )
    drive = Device(
        id=f"{prefix}-drive",
        kind=DeviceKind.DRIVE,
        part_key=f"ABB/{type_code}",
        rated_current_a=result.rated_output_current_a.quantize(_CENT),
        description=f"{load.description} drive",
        upstream_id=fuses.id,
    )
    return MotorCircuit(
        devices=[fuses, drive],
        current_a=current,
        cable_current_a=current,
        cable_cores=4,
        notes=[
            note(
                "drive_selected",
                load=load.description,
                drive=type_code,
                ambient=ambient_c,
                fuse=fuse.amps,
                source=_DRIVE_MANUAL,
            ),
            note("drive_fuse_fault_level", load=load.description, current=fuse.min_short_circuit_a),
        ],
    )


def motor_circuit(
    index: int, load: LoadInput, supply: Supply, *, ambient_c: Decimal
) -> MotorCircuit:
    """The devices, current and cable of a motor's circuit.

    Args:
        index: The load's position in its schedule, which keys device ids.
        load: The motor, with its ``starter``.
        supply: The board's supply.
        ambient_c: The ambient the board is in, for a drive's derating.

    Returns:
        The motor's circuit.

    Raises:
        ValidationError: If the motor is not three-phase or no table holds it,
            naming the load.
    """
    if load.starter is None:
        raise ValidationError("no starter given", code="starter_missing").about(load.description)
    if load.phases != 3 or supply.phases != 3:
        raise ValidationError(
            "a motor starter needs a three-phase motor", code="starter_needs_three_phase"
        ).about(load.description)
    current, basis = motor_current(load, supply)
    try:
        if load.starter is MotorStarter.DRIVE:
            circuit = _drive(index, load, supply, current, ambient_c)
        else:
            circuit = _coordinated(
                index, load, supply, current, load.starter is MotorStarter.STAR_DELTA
            )
    except ValidationError as exc:
        raise exc.about(load.description) from exc
    circuit.notes.insert(0, basis)
    return circuit


_PART_NAMES: dict[DeviceKind, str] = {
    DeviceKind.CIRCUIT_BREAKER: "Moulded-case circuit-breaker, motor protection",
    DeviceKind.CONTACTOR: "Contactor, AC-3",
    DeviceKind.OVERLOAD_RELAY: "Thermal overload relay",
    DeviceKind.FUSE: "aR fuse, DIN 43653 stud-mount (one per phase)",
    DeviceKind.DRIVE: "Variable-speed drive",
}


def parts_for(boards: list[Board]) -> list[Part]:
    """The articles the boards name, once each, in the order first named.

    Args:
        boards: The designed boards.

    Returns:
        One part per distinct ``"<maker>/<type>"`` key.
    """
    parts: dict[str, Part] = {}
    for board in boards:
        for device in board.devices:
            key = device.part_key
            if not key or "/" not in key or key in parts:
                continue
            manufacturer, type_number = key.split("/", 1)
            from_drive_manual = device.kind in (DeviceKind.DRIVE, DeviceKind.FUSE)
            source = mccb.source_of(type_number) or (
                _DRIVE_MANUAL if from_drive_manual else _HANDBOOK
            )
            parts[key] = Part(
                key=key,
                manufacturer=manufacturer,
                type_number=type_number,
                order_number=None,
                description=(
                    "Moulded-case circuit-breaker, thermomagnetic"
                    if mccb.source_of(type_number)
                    else _PART_NAMES.get(device.kind, device.kind.value)
                ),
                source=source,
            )
    return list(parts.values())
