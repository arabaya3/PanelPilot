"""Tests for `app/design/motors.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.design import motors
from app.models.schemas.design import (
    Board,
    DeviceKind,
    LoadInput,
    LoadKind,
    MotorStarter,
    Supply,
)

SUPPLY = Supply(voltage_v=Decimal(400), phases=3)


def _motor(kw: str, starter: MotorStarter, **extra: object) -> LoadInput:
    return LoadInput.model_validate(
        {
            "description": "Pump",
            "load": LoadKind.MOTOR,
            "power_kw": Decimal(kw),
            "phases": 3,
            "starter": starter,
            **extra,
        }
    )


def test_motor_current_prefers_the_nameplate_then_the_table_then_the_formula() -> None:
    # Power factor given: the formula at that power factor.
    current, basis = motors.motor_current(
        _motor("7.5", MotorStarter.DIRECT_ON_LINE, power_factor=Decimal("0.8")), SUPPLY
    )
    assert current == Decimal("15.04")
    assert "efficiency 0.9" in basis
    assert "cos phi" not in basis
    # No power factor, a row for exactly this power: the table's Ir.
    current, basis = motors.motor_current(_motor("7.5", MotorStarter.DIRECT_ON_LINE), SUPPLY)
    assert current == Decimal("15.2")
    assert "typical Ir" in basis
    # A drive, or a power with no row: the formula at the assumed values.
    current, basis = motors.motor_current(_motor("8", MotorStarter.DRIVE), SUPPLY)
    assert current == Decimal("15.09")
    assert "cos phi 0.85" in basis


def test_a_direct_on_line_starter_is_a_coordinated_set() -> None:
    circuit = motors.motor_circuit(
        0, _motor("7.5", MotorStarter.DIRECT_ON_LINE), SUPPLY, ambient_c=Decimal(30)
    )
    kinds = [d.kind for d in circuit.devices]
    assert kinds == [DeviceKind.CIRCUIT_BREAKER, DeviceKind.CONTACTOR, DeviceKind.OVERLOAD_RELAY]
    breaker, contactor, relay = circuit.devices
    assert breaker.part_key == "ABB/T2S160 MA 20"
    assert (breaker.curve, breaker.rated_current_a) == ("MA", Decimal(20))
    assert contactor.part_key == "ABB/A30"
    assert contactor.upstream_id == breaker.id
    assert relay.part_key == "ABB/TA25DU19"
    assert relay.rated_current_a == Decimal("15.2")
    assert (circuit.cable_cores, circuit.cable_current_a) == (4, Decimal("15.2"))
    assert any("Table 3" in note for note in circuit.notes)


def test_a_star_delta_relay_is_set_to_the_phase_current() -> None:
    circuit = motors.motor_circuit(
        2, _motor("30", MotorStarter.STAR_DELTA), SUPPLY, ambient_c=Decimal(30)
    )
    roles = [d.id.rsplit("-", 1)[-1] for d in circuit.devices]
    assert roles == ["breaker", "line", "delta", "star", "overload"]
    relay = circuit.devices[-1]
    assert relay.rated_current_a == Decimal("32.33")  # 56 A / sqrt(3)
    assert circuit.cable_cores == 7
    assert circuit.cable_current_a == Decimal("32.33")
    assert any("mechanical interlock" in note for note in circuit.notes)
    assert not any("Δ" in note for note in circuit.notes)


def test_a_drive_is_chosen_with_its_input_fuses() -> None:
    circuit = motors.motor_circuit(
        1, _motor("15", MotorStarter.DRIVE), SUPPLY, ambient_c=Decimal(30)
    )
    fuses, drive = circuit.devices
    assert fuses.kind is DeviceKind.FUSE
    assert fuses.curve == "aR"
    assert drive.kind is DeviceKind.DRIVE
    assert drive.part_key == "ABB/ACS880-01-032A-3"
    assert drive.rated_current_a is not None
    assert drive.rated_current_a >= circuit.current_a
    assert any("screened" in note for note in circuit.notes)


@pytest.mark.parametrize(
    ("load", "supply", "message"),
    [
        (_motor("7.5", MotorStarter.DIRECT_ON_LINE, phases=1), SUPPLY, "three-phase motor"),
        (
            _motor("7.5", MotorStarter.DIRECT_ON_LINE),
            Supply(phases=1, voltage_v=Decimal(230)),
            "three-phase",
        ),
        (_motor("1000", MotorStarter.DIRECT_ON_LINE), SUPPLY, "Pump: "),
    ],
)
def test_a_motor_no_table_holds_is_refused_by_name(
    load: LoadInput, supply: Supply, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        motors.motor_circuit(0, load, supply, ambient_c=Decimal(30))


def test_parts_for() -> None:
    first, second = (
        motors.motor_circuit(
            index, _motor("7.5", MotorStarter.DIRECT_ON_LINE), SUPPLY, ambient_c=Decimal(30)
        )
        for index in (0, 1)
    )
    # Two identical motors name each article once.
    board = Board(id="MCC", name="MCC", devices=[*first.devices, *second.devices])
    parts = motors.parts_for([board])
    assert [p.key for p in parts] == ["ABB/T2S160 MA 20", "ABB/A30", "ABB/TA25DU19"]
    assert parts[0].manufacturer == "ABB"
    assert parts[0].type_number == "T2S160 MA 20"
    assert "1SDC010001D0204" in (parts[0].source or "")
