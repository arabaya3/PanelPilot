"""A project of boards, where one board feeds another.

A sub-board names the board that feeds it (``fed_from``). Boards are designed
leaves first, so each feeder is sized from the sub-board as designed rather
than from a figure typed in by hand:

* The feeder's design current is the sub-board's most loaded line conductor,
  the same current its incomer is rated for, with no diversity applied.
* It is entered as a load of ``power_kw`` at cos phi 1 chosen so that
  P / (k Ur cos phi) gives that current back, so the feeder is protected and
  cabled by the same rule as every other circuit (``distribution``).
* A feeder and the incomer it supplies come out the same rating, so the board
  says discrimination between them is not checked.

Names, the feeding graph (unknown board, a board feeding itself, a loop) and
supply compatibility are checked before anything is designed, each refusal
naming the board.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from app.core.errors import ValidationError
from app.design import distribution
from app.design.notes import note
from app.models.schemas.design import (
    Board,
    CompanyProfile,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
)

_SQRT3 = Decimal(3).sqrt()
_HUNDREDTH = Decimal("0.01")


def design_order(requests: list[DistributionBoardRequest]) -> list[DistributionBoardRequest]:
    """Order boards so every board comes after all the boards it feeds.

    Args:
        requests: The boards, in the order given.

    Returns:
        The same boards, leaves first; otherwise in the order given.

    Raises:
        ValidationError: If two boards share a name, a board names an unknown
            board or itself as its supply, or boards feed one another in a loop.
    """
    by_name: dict[str, DistributionBoardRequest] = {}
    for request in requests:
        if request.name in by_name:
            raise ValidationError(
                f"two boards are named {request.name}",
                code="board_name_twice",
                params={"board": request.name},
            )
        by_name[request.name] = request
    for request in requests:
        if request.fed_from is None:
            continue
        if request.fed_from == request.name:
            raise ValidationError(
                f"{request.name} is named as its own supply",
                code="board_feeds_itself",
                params={"board": request.name},
            )
        if request.fed_from not in by_name:
            raise ValidationError(
                f"{request.name} is fed from {request.fed_from}, which is not in the project",
                code="board_supply_unknown",
                params={"board": request.name, "supply": request.fed_from},
            )

    order: list[DistributionBoardRequest] = []
    placed: set[str] = set()
    visiting: set[str] = set()
    children: dict[str, list[str]] = {name: [] for name in by_name}
    for request in requests:
        if request.fed_from is not None:
            children[request.fed_from].append(request.name)

    def place(name: str) -> None:
        if name in placed:
            return
        if name in visiting:
            raise ValidationError(
                f"boards feed one another in a loop through {name}",
                code="boards_loop",
                params={"board": name},
            )
        visiting.add(name)
        for child in children[name]:
            place(child)
        visiting.discard(name)
        placed.add(name)
        order.append(by_name[name])

    for request in requests:
        place(request.name)
    return order


def feeder_load(board: Board) -> LoadInput:
    """The load a designed sub-board puts on the board that feeds it.

    Args:
        board: The sub-board, designed.

    Returns:
        A sub-board load whose design current is the sub-board's most loaded
        line conductor.
    """
    current = max(distribution.phase_currents(board.circuits).values())
    three_phase = board.supply.phases == 3
    volts = board.supply.voltage_v * (_SQRT3 if three_phase else 1)
    power_kw = (volts * current / 1000).quantize(_HUNDREDTH, rounding=ROUND_CEILING)
    return LoadInput(
        description=f"Feeder to {board.name}",
        load=LoadKind.SUB_BOARD,
        power_kw=max(power_kw, _HUNDREDTH),
        phases=3 if three_phase else 1,
        power_factor=Decimal(1),
        feeds=board.name,
    )


def _check_supply(child: DistributionBoardRequest, parent: DistributionBoardRequest) -> None:
    if child.supply.phases == 3 and parent.supply.phases == 1:
        raise ValidationError(
            f"{child.name} is three-phase but {parent.name}, which feeds it, is single-phase",
            code="board_phase_mismatch",
            params={"board": child.name, "supply": parent.name},
        )
    if child.supply.phases == parent.supply.phases:
        expected = parent.supply.voltage_v
    else:
        expected = (parent.supply.voltage_v / _SQRT3).quantize(Decimal(1))
    if abs(child.supply.voltage_v - expected) > 1:
        raise ValidationError(
            f"{child.name} is supplied at {child.supply.voltage_v} V, but {parent.name} "
            f"gives {expected} V to a {child.supply.phases}-phase board",
            code="board_voltage_mismatch",
            params={
                "board": child.name,
                "voltage": child.supply.voltage_v,
                "supply": parent.name,
                "expected": expected,
            },
        )


def design_boards(requests: list[DistributionBoardRequest], profile: CompanyProfile) -> list[Board]:
    """Design every board in a project, each feeder sized from its sub-board.

    Args:
        requests: The boards' schedules.
        profile: The company whose rules apply.

    Returns:
        The boards, undesignated, in the order given.

    Raises:
        ValidationError: If the feeding graph or a supply is inconsistent, or
            a load cannot be protected or cabled.
    """
    order = design_order(requests)
    by_name = {request.name: request for request in requests}
    for request in requests:
        if request.fed_from is not None:
            _check_supply(request, by_name[request.fed_from])
    feeders: dict[str, list[LoadInput]] = {request.name: [] for request in requests}
    designed: dict[str, Board] = {}
    for request in order:
        loads = [*request.loads, *feeders[request.name]]
        board = distribution.design_distribution_board(
            request.model_copy(update={"loads": loads}), profile
        )
        if request.fed_from is not None:
            feeders[request.fed_from].append(feeder_load(board))
            board.notes.append(note("fed_from", board=request.fed_from))
        if feeders[request.name]:
            board.notes.append(note("feeder_discrimination"))
        designed[request.name] = board
    return [designed[request.name] for request in requests]
