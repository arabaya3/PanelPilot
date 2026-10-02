"""Tests for `app/design/terminals.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.design import distribution, profile, terminals
from app.models.schemas.calculations import ConductorMaterial
from app.models.schemas.design import (
    Board,
    Circuit,
    DistributionBoardRequest,
    InstallationConditions,
    LoadInput,
    LoadKind,
    MotorStarter,
    Phase,
)


def _circuit(phase: Phase, starter: MotorStarter | None = None) -> Circuit:
    return Circuit(
        id="c1",
        description="x",
        load=LoadKind.MOTOR if starter else LoadKind.SOCKET,
        power_kw=Decimal(1),
        design_current_a=Decimal(5),
        phase=phase,
        starter=starter,
    )


@pytest.mark.parametrize(
    ("phase", "starter", "cores", "expected"),
    [
        (Phase.L2, None, 3, ["L2", "N", "PE"]),
        (Phase.THREE_PHASE, None, 5, ["L1", "L2", "L3", "N", "PE"]),
        (Phase.THREE_PHASE, None, 4, ["L1", "L2", "L3", "PE"]),
        (Phase.THREE_PHASE, MotorStarter.DIRECT_ON_LINE, 4, ["U", "V", "W", "PE"]),
        (Phase.THREE_PHASE, MotorStarter.DRIVE, 4, ["U", "V", "W", "PE"]),
        (
            Phase.THREE_PHASE,
            MotorStarter.STAR_DELTA,
            7,
            ["U1", "V1", "W1", "U2", "V2", "W2", "PE"],
        ),
    ],
)
def test_functions(
    phase: Phase, starter: MotorStarter | None, cores: int, expected: list[str]
) -> None:
    assert terminals.functions(_circuit(phase, starter), cores) == expected


def _board(**conditions: object) -> Board:
    request = DistributionBoardRequest(
        name="DB",
        loads=[
            LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal("1.5")),
            LoadInput(
                description="AC", load=LoadKind.AIR_CONDITIONING, power_kw=Decimal(4), phases=3
            ),
        ],
        conditions=InstallationConditions.model_validate(conditions),
    )
    return distribution.design_distribution_board(request, profile.default_profile())


def test_strip_numbers_every_core_in_circuit_order() -> None:
    strip = terminals.strip(_board())
    assert [t.number for t in strip] == list(range(1, 9))
    assert [t.function for t in strip] == ["L1", "N", "PE", "L1", "L2", "L3", "N", "PE"]
    # Sockets on 2.5 mm², 16 A: the 2.5 mm² terminal, its PE terminal for PE.
    assert strip[0].article == "8WH1000-0AF00"
    assert strip[2].article == "8WH1000-0CF07"
    assert {t.circuit for t in strip} == {"Sockets", "AC"}


def test_aluminium_terminals_are_left_unselected() -> None:
    strip = terminals.strip(_board(conductor_material=ConductorMaterial.ALUMINIUM))
    assert strip
    assert all(t.article is None for t in strip)
