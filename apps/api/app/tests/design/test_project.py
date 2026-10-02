"""Tests for `app/design/project.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.design import distribution, profile, project
from app.models.schemas.design import (
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    Phase,
    Supply,
)


def _board(
    name: str, *, fed_from: str | None = None, kw: str = "3", **supply: object
) -> DistributionBoardRequest:
    return DistributionBoardRequest(
        name=name,
        fed_from=fed_from,
        supply=Supply.model_validate(supply),
        loads=[
            LoadInput(description=f"{name} sockets", load=LoadKind.SOCKET, power_kw=Decimal(kw)),
            LoadInput(description=f"{name} lights", load=LoadKind.LIGHTING, power_kw=Decimal(1)),
        ],
    )


def test_boards_are_designed_leaves_first() -> None:
    boards = [
        _board("MDB"),
        _board("DB-1", fed_from="MDB"),
        _board("DB-2", fed_from="DB-1"),
        _board("DB-3", fed_from="MDB"),
    ]
    order = [board.name for board in project.design_order(boards)]
    assert order.index("DB-2") < order.index("DB-1") < order.index("MDB")
    assert order.index("DB-3") < order.index("MDB")


@pytest.mark.parametrize(
    ("boards", "message"),
    [
        ([_board("A"), _board("A")], "two boards are named A"),
        ([_board("A", fed_from="A")], "own supply"),
        ([_board("A", fed_from="Z")], "not in the project"),
        ([_board("A", fed_from="B"), _board("B", fed_from="A")], "loop"),
    ],
)
def test_an_inconsistent_feeding_is_refused(
    boards: list[DistributionBoardRequest], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        project.design_order(boards)


def test_a_feeder_is_sized_from_the_sub_board_as_designed() -> None:
    main, sub = project.design_boards(
        [_board("MDB"), _board("DB-1", fed_from="MDB", kw="9")], profile.default_profile()
    )
    feeder = next(c for c in main.circuits if c.feeds == "DB-1")
    sub_current = max(distribution.phase_currents(sub.circuits).values())
    # The feeder's Ib is the sub-board's most loaded conductor, rounded up.
    assert sub_current <= feeder.design_current_a <= sub_current + Decimal("0.05")
    assert feeder.phase is Phase.THREE_PHASE
    breaker = main.device(feeder.device_ids[0])
    incomer = sub.device(sub.incomer_ids[0])
    assert breaker.rated_current_a >= incomer.rated_current_a  # type: ignore[operator]
    assert sub.fed_from == "MDB"
    assert "Fed from MDB." in [n.text for n in sub.notes]
    assert any("Discrimination between each sub-board feeder" in n.text for n in main.notes)


def test_a_single_phase_sub_board_on_a_three_phase_main() -> None:
    main, sub = project.design_boards(
        [_board("MDB"), _board("DB-1", fed_from="MDB", phases=1, voltage_v=230)],
        profile.default_profile(),
    )
    feeder = next(c for c in main.circuits if c.feeds == "DB-1")
    assert feeder.phase in (Phase.L1, Phase.L2, Phase.L3)
    assert sub.supply.phases == 1


@pytest.mark.parametrize(
    ("child", "message"),
    [
        ({"phases": 3, "voltage_v": 400}, "three-phase but MDB"),
        ({"phases": 1, "voltage_v": 110}, "supplied at 110 V"),
    ],
)
def test_a_sub_board_the_main_cannot_supply_is_refused(
    child: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        project.design_boards(
            [_board("MDB", phases=1, voltage_v=230), _board("DB-1", fed_from="MDB", **child)],  # type: ignore[arg-type]
            profile.default_profile(),
        )


def test_feeder_load() -> None:
    (board,) = project.design_boards([_board("DB-1", kw="9")], profile.default_profile())
    load = project.feeder_load(board)
    assert load.load is LoadKind.SUB_BOARD
    assert load.feeds == "DB-1"
    assert load.power_factor == 1
    assert load.phases == 3


def test_voltage_drop_adds_up_from_the_origin() -> None:
    sub = _board("DB-1", fed_from="MDB", kw="9").model_copy(update={"feeder_length_m": Decimal(80)})
    main, designed = project.design_boards([_board("MDB"), sub], profile.default_profile())
    feeder = next(c for c in main.circuits if c.feeds == "DB-1")
    assert feeder.voltage_drop_percent is not None
    # The feeder is held to the strictest load below it: the sub-board's lights, 3 %.
    assert 0 < feeder.voltage_drop_percent <= 3
    (upstream,) = [n for n in designed.notes if n.code == "voltage_drop_upstream"]
    assert upstream.params["percent"] == str(feeder.voltage_drop_percent.normalize())


def test_feeder_load_carries_its_length() -> None:
    (board,) = project.design_boards([_board("DB-1")], profile.default_profile())
    assert project.feeder_load(board, Decimal(25)).length_m == 25
