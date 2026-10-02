"""A project of boards, where one board feeds another.

A sub-board names the board that feeds it (``fed_from``). Boards are designed
leaves first, so each feeder is sized from the sub-board as designed rather
than from a figure typed in by hand:

* The feeder's design current is the sub-board's most loaded line conductor,
  the same current its incomer is rated for, after the company's demand
  factors where it sets any.
* It is entered as a load of ``power_kw`` at cos phi 1 chosen so that
  P / (k Ur cos phi) gives that current back, so the feeder is protected and
  cabled by the same rule as every other circuit (``distribution``).
* A sub-board's incomer is a switch-disconnector, protected by its feeder
  breaker and rated no lower than it. The feeder is rated at least the
  company's discrimination ratio times the largest breaker that switch
  feeds, so the two discriminate on overload; its cable is sized for that.
* A sub-board with no fault level of its own gets one from the board that
  feeds it: calculated through its three-phase feeder where the feeder's
  length is given (``fault_level``), otherwise taken unreduced, which is a
  safe figure the engineer can replace.
* A sub-board with no ``Ze`` of its own gets its supply board's plus the
  loop of the feeder between them, where the feeder's length is given
  (``disconnection``); otherwise its circuits' earth fault loops are left
  unchecked, as a figure taken unreduced would be too low.
* Voltage drop adds up from the origin. Once every feeder's current is known,
  the boards are designed again from the origin down: each feeder is held to
  the limit of the strictest load anywhere below it, and each sub-board's
  circuits to their limit less what its feeders dropped (``voltage_drop``).

Names, the feeding graph (unknown board, a board feeding itself, a loop) and
supply compatibility are checked before anything is designed, each refusal
naming the board.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from app.core.errors import ValidationError
from app.design import disconnection, distribution, fault_level, voltage_drop
from app.design.notes import note
from app.models.schemas.calculations import ConductorMaterial
from app.models.schemas.design import (
    Board,
    CompanyProfile,
    DesignNote,
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


def feeder_load(
    board: Board, length_m: Decimal | None = None, profile: CompanyProfile | None = None
) -> LoadInput:
    """The load a designed sub-board puts on the board that feeds it.

    Args:
        board: The sub-board, designed.
        length_m: The feeder cable's route length, where given.
        profile: The company, whose demand factors apply; none for none.

    Returns:
        A sub-board load whose design current is the sub-board's most loaded
        line conductor.
    """
    if profile is not None and profile.demand_factors:
        current = max(distribution.demand_currents(board.circuits, profile).values())
    else:
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
        length_m=length_m,
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
    # Leaves first: each feeder's current from the sub-board it supplies.
    # Cable sizes do not change a current, so this pass needs no drop budget.
    feeders: dict[str, list[LoadInput]] = {request.name: [] for request in requests}
    strictest: dict[str, Decimal] = {}
    after: dict[str, Decimal] = {}
    for request in order:
        board = distribution.design_distribution_board(
            _with_feeders(request, feeders), profile, sub_board_after_a=after
        )
        largest = _largest_after_incomer(board)
        if largest is not None:
            after[request.name] = largest
        strictest[request.name] = min(
            [
                *(voltage_drop.limit_for(profile, load.load) for load in request.loads),
                *(strictest[feeder.feeds] for feeder in feeders[request.name] if feeder.feeds),
            ]
        )
        if request.fed_from is not None:
            feeders[request.fed_from].append(feeder_load(board, request.feeder_length_m, profile))

    # From the origin down: each board knows what its feeders dropped.
    upstream: dict[str, Decimal] = {}
    supply_breakers: dict[str, Decimal] = {}
    designed: dict[str, Board] = {}
    for request in reversed(order):
        request, level_note = _inherit_fault_level(request, designed)
        request, loop_note = _inherit_earth_loop(request, designed)
        children = [feeder.feeds for feeder in feeders[request.name] if feeder.feeds]
        budget = voltage_drop.Budget(
            upstream_percent=upstream.get(request.name, Decimal(0)),
            feeder_limits={child: strictest[child] for child in children},
        )
        board = distribution.design_distribution_board(
            _with_feeders(request, feeders),
            profile,
            budget,
            after,
            supply_breakers.get(request.name),
        )
        for circuit in board.circuits:
            if circuit.feeds is not None:
                breaker = board.device(circuit.device_ids[0]).rated_current_a
                if breaker is not None:
                    supply_breakers[circuit.feeds] = breaker
                upstream[circuit.feeds] = budget.upstream_percent + (
                    circuit.voltage_drop_percent or Decimal(0)
                )
        if request.fed_from is not None:
            board.notes.append(note("fed_from", board=request.fed_from))
        if level_note is not None:
            board.notes.append(level_note)
        if loop_note is not None:
            board.notes.append(loop_note)
        designed[request.name] = board
    return [designed[request.name] for request in requests]


def _largest_after_incomer(board: Board) -> Decimal | None:
    """The largest protective device the board's incomer feeds directly."""
    ratings = [
        device.rated_current_a
        for device in board.devices
        if device.upstream_id in board.incomer_ids and device.rated_current_a is not None
    ]
    return max(ratings, default=None)


def _inherit_fault_level(
    request: DistributionBoardRequest, designed: dict[str, Board]
) -> tuple[DistributionBoardRequest, DesignNote | None]:
    """The sub-board with a fault level from its supply, where it gives none.

    Returns:
        The request, with the fault level set where one was derived, and the
        note saying how.
    """
    if request.fed_from is None or request.supply.fault_level_ka is not None:
        return request, None
    parent = designed[request.fed_from]
    upstream = parent.supply.fault_level_ka
    if upstream is None:
        return request, None
    feeder = next((c for c in parent.circuits if c.feeds == request.name), None)
    cable = parent.cable(feeder.cable_id) if feeder and feeder.cable_id else None
    if cable is not None and cable.length_m and request.supply.phases == 3:
        material = (
            ConductorMaterial.ALUMINIUM if cable.material == "Al" else ConductorMaterial.COPPER
        )
        level = fault_level.at_feeder_end(
            upstream_ka=upstream,
            voltage_v=request.supply.voltage_v,
            length_m=cable.length_m,
            section_mm2=cable.cross_section_mm2,
            material=material,
            parallel=cable.parallel,
        )
        made = note(
            "fault_level_calculated",
            fault=_plain(level),
            board=request.fed_from,
            upstream=_plain(upstream),
            length=_plain(cable.length_m),
            section=_plain(cable.cross_section_mm2),
            material=cable.material,
        )
    else:
        level = upstream
        made = note("fault_level_inherited", fault=_plain(upstream), board=request.fed_from)
    supply = request.supply.model_copy(update={"fault_level_ka": level})
    return request.model_copy(update={"supply": supply}), made


def _inherit_earth_loop(
    request: DistributionBoardRequest, designed: dict[str, Board]
) -> tuple[DistributionBoardRequest, DesignNote | None]:
    """The sub-board with a ``Ze`` from its supply, where it gives none.

    A sub-board's ``Ze`` is its supply board's plus the loop of the feeder
    between them, so it is only known where the feeder's length is: taken
    unchanged it would be too low, which errs the unsafe way.

    Returns:
        The request, with ``Ze`` set where one was derived, and the note
        saying how.
    """
    if request.fed_from is None or request.supply.earth_loop_ohm is not None:
        return request, None
    parent = designed[request.fed_from]
    upstream = parent.supply.earth_loop_ohm
    feeder = next((c for c in parent.circuits if c.feeds == request.name), None)
    cable = parent.cable(feeder.cable_id) if feeder and feeder.cable_id else None
    if upstream is None or cable is None or not cable.length_m:
        return request, None
    material = ConductorMaterial.ALUMINIUM if cable.material == "Al" else ConductorMaterial.COPPER
    loop = (
        disconnection.loop_ohm(
            cable.length_m,
            cable.cross_section_mm2,
            material,
            90 if cable.insulation == "XLPE" else 70,
        )
        / cable.parallel
    )
    external = (upstream + loop).quantize(Decimal("0.001"), rounding=ROUND_CEILING)
    made = note(
        "earth_loop_calculated",
        ze=_plain(external),
        board=request.fed_from,
        upstream=_plain(upstream),
        length=_plain(cable.length_m),
        section=_plain(cable.cross_section_mm2),
        material=cable.material,
    )
    supply = request.supply.model_copy(update={"earth_loop_ohm": external})
    return request.model_copy(update={"supply": supply}), made


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _with_feeders(
    request: DistributionBoardRequest, feeders: dict[str, list[LoadInput]]
) -> DistributionBoardRequest:
    return request.model_copy(update={"loads": [*request.loads, *feeders[request.name]]})
