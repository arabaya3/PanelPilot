"""Tests for `app/design/schedule_split.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import profile, schedule_split
from app.models.schemas.design import LoadKind, SuggestedPoints


def _points(kind: LoadKind, quantity: int, watts: str, three: bool = False) -> SuggestedPoints:
    return SuggestedPoints(
        description=kind.value,
        load=kind,
        quantity=quantity,
        unit_power_kw=Decimal(watts) / 1000,
        three_phase=three,
        power_stated=False,
    )


def _split(*items: SuggestedPoints, supply: int = 3) -> list[tuple[str, Decimal, int]]:
    loads, _ = schedule_split.split_points(
        list(items), profile.default_profile(), supply_phases=supply
    )
    return [(load.description, load.power_kw, load.phases) for load in loads]


def test_points_are_spread_evenly_over_the_fewest_circuits() -> None:
    # 20 sockets at eight to a circuit: three circuits of 7, 7 and 6.
    assert _split(_points(LoadKind.SOCKET, 20, "150")) == [
        ("socket 1", Decimal("1.05"), 1),
        ("socket 2", Decimal("1.05"), 1),
        ("socket 3", Decimal("0.9"), 1),
    ]


def test_the_power_cap_splits_further_than_the_count() -> None:
    # 40 high-bays at 150 W: ten to a 1.5 kW circuit, so four circuits.
    split = _split(_points(LoadKind.LIGHTING, 40, "150"))
    assert len(split) == 4
    assert all(power <= Decimal("1.5") for _, power, _ in split)


def test_appliances_get_a_circuit_each_and_keep_one_name_when_alone() -> None:
    assert _split(_points(LoadKind.WATER_HEATER, 1, "2000")) == [("water_heater", Decimal(2), 1)]
    assert len(_split(_points(LoadKind.AIR_CONDITIONING, 3, "2500"))) == 3


def test_three_phase_only_where_it_can_be() -> None:
    motor = _points(LoadKind.MOTOR, 1, "7500", three=True)
    small_ac = _points(LoadKind.AIR_CONDITIONING, 1, "2500", three=True)
    big_ac = _points(LoadKind.AIR_CONDITIONING, 1, "7000", three=True)
    sockets = _points(LoadKind.SOCKET, 1, "150", three=True)
    assert [p for _, _, p in _split(motor, small_ac, big_ac, sockets)] == [3, 1, 3, 1]
    assert [p for _, _, p in _split(motor, supply=1)] == [1]


def test_every_circuit_says_how_its_power_was_reached() -> None:
    _, notes = schedule_split.split_points(
        [_points(LoadKind.SOCKET, 9, "150")], profile.default_profile(), supply_phases=3
    )
    assert notes == [
        "socket 1: 5 x 150 W = 0.75 kW. typical",
        "socket 2: 4 x 150 W = 0.6 kW. typical",
    ]


def test_notes_say_where_the_power_came_from_in_the_descriptions_language() -> None:
    stated = _points(LoadKind.WATER_HEATER, 1, "3000").model_copy(update={"power_stated": True})
    _, notes = schedule_split.split_points(
        [stated, _points(LoadKind.SOCKET, 1, "150")],
        profile.default_profile(),
        supply_phases=3,
        language="Arabic",
    )
    assert notes[0].endswith("kW. من الوصف")
    assert notes[1].endswith("kW. قيمة نموذجية")
