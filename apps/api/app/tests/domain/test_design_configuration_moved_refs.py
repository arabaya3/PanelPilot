"""Which binding references moved with the load schedule (pure; SYNTHETIC stand-ins)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.domain import design_configuration as dc


def _design(*circuits: tuple[str, str, str | None]) -> Any:
    """MB-1 with circuits c1.. in order: (description, load kind, feeds)."""
    return SimpleNamespace(
        boards=[
            SimpleNamespace(
                id="MB-1",
                circuits=[
                    SimpleNamespace(
                        id=f"c{i}",
                        description=d,
                        load=k,
                        feeds=f,
                        cable_id=f"c{i}-cable",
                        device_ids=[f"c{i}-breaker"],
                    )
                    for i, (d, k, f) in enumerate(circuits, start=1)
                ],
                cables=[],
            )
        ]
    )


LIGHT, HEAT = ("Lighting", "lighting", None), ("Heater", "socket", None)


def _config(circuit: str | None = "c1", cable: str | None = None, device: str | None = None) -> Any:
    slots = {"q": {"device": device}} if device else {}
    return {
        "bindings": [
            {"id": "b1", "board": "MB-1", "circuit": circuit, "cable": cable, "slots": slots}
        ]
    }


def test_an_unchanged_reference_to_another_load_now_is_moved() -> None:
    moved = dc.moved_references(_design(LIGHT), _design(HEAT, LIGHT), _config(), (), _config())
    assert moved == [
        {
            "binding": "b1",
            "field": "circuit",
            "key": "",
            "id": "c1",
            "before": "Lighting",
            "after": "Heater",
        }
    ]


def test_cables_and_design_devices_move_with_their_circuit() -> None:
    config = _config(circuit=None, cable="c1-cable", device="c1-breaker")
    moved = dc.moved_references(_design(LIGHT), _design(HEAT, LIGHT), config, (), config)
    assert [(m["field"], m["key"], m["id"]) for m in moved] == [
        ("cable", "", "c1-cable"),
        ("slot", "q", "c1-breaker"),
    ]


def test_appended_unchanged_changed_by_the_user_or_new_are_not_moves() -> None:
    assert dc.moved_references(_design(LIGHT), _design(LIGHT, HEAT), _config(), (), _config()) == []
    # The user chose c2 against the current choices: not silent.
    assert (
        dc.moved_references(_design(LIGHT), _design(HEAT, LIGHT), _config("c2"), (), _config())
        == []
    )
    # Nothing saved before: nothing has moved.
    assert dc.moved_references(_design(LIGHT), _design(HEAT), _config(), (), None) == []
    # The binding is new since the last save.
    assert dc.moved_references(_design(LIGHT), _design(HEAT), _config(), (), {"bindings": []}) == []
    # Gone: the composer refuses it (unknown reference), not this check.
    assert dc.moved_references(_design(LIGHT), _design(), _config(), (), _config()) == []


def test_a_confirmation_keeps_exactly_its_reference() -> None:
    kept = [{"binding": "b1", "field": "circuit", "key": None, "id": "c1"}]
    assert dc.moved_references(_design(LIGHT), _design(HEAT), _config(), kept, _config()) == []
    other = [{"binding": "b1", "field": "cable", "key": None, "id": "c1"}]
    assert dc.moved_references(_design(LIGHT), _design(HEAT), _config(), other, _config()) != []


def test_check_references_refuses_naming_every_moved_reference() -> None:
    config = _config(cable="c1-cable")
    with pytest.raises(ValidationError) as refused:
        dc.check_references(_design(LIGHT), _design(HEAT), config, (), config)
    params = refused.value.params
    assert params["refusal"] == "reference_moved"
    assert (params["binding"], params["field"], params["ref"]) == ("b1", "circuit", "c1")
    assert params["moved"] == "b1|circuit||c1;b1|cable||c1-cable"


def _boards(*boards: tuple[str, list[tuple[str, str, str | None]]]) -> Any:
    """Boards in order, each with its circuits c1.. (description, load kind, feeds)."""
    return SimpleNamespace(
        boards=[
            SimpleNamespace(
                id=name,
                circuits=[
                    SimpleNamespace(
                        id=f"c{i}",
                        description=d,
                        load=k,
                        feeds=f,
                        cable_id=f"c{i}-cable",
                        device_ids=[f"c{i}-breaker"],
                    )
                    for i, (d, k, f) in enumerate(circuits, start=1)
                ],
                cables=[],
            )
            for name, circuits in boards
        ]
    )


def _on(board: str, circuit: str = "c1") -> Any:
    return {"bindings": [{"id": "b1", "board": board, "circuit": circuit, "slots": {}}]}


def test_a_board_renamed_with_a_moved_load_is_still_moved() -> None:
    before = _boards(("MB-1", [LIGHT]))
    after = _boards(("MB-9", [HEAT, LIGHT]))
    # The binding follows the rename (its board changed with it): its c1 is now Heater.
    moved = dc.moved_references(before, after, _on("MB-9"), (), _on("MB-1"))
    assert [(m["id"], m["before"], m["after"]) for m in moved] == [("c1", "Lighting", "Heater")]
    # The rename alone moves nothing.
    assert (
        dc.moved_references(before, _boards(("MB-9", [LIGHT])), _on("MB-9"), (), _on("MB-1")) == []
    )


def test_a_feeder_is_the_same_while_it_feeds_the_same_board_followed_through_a_rename() -> None:
    feeder = ("Feeder to SB-1", "feeder", "SB-1")
    renamed = ("Feeder to SB-2", "feeder", "SB-2")
    before = _boards(("MB-1", [feeder]), ("SB-1", []))
    after = _boards(("MB-1", [renamed]), ("SB-2", []))
    assert dc.moved_references(before, after, _on("MB-1"), (), _on("MB-1")) == []
    # A load inserted before the feeder: c1 is no longer the feeder.
    shifted = _boards(("MB-1", [HEAT, renamed]), ("SB-2", []))
    moved = dc.moved_references(before, shifted, _on("MB-1"), (), _on("MB-1"))
    assert [(m["before"], m["after"]) for m in moved] == [("feeder to SB-1", "Heater")]


def test_reordered_fed_boards_move_their_feeders() -> None:
    to2, to3 = ("Feeder to SB-2", "feeder", "SB-2"), ("Feeder to SB-3", "feeder", "SB-3")
    before = _boards(("MB-1", [to2, to3]), ("SB-2", []), ("SB-3", []))
    after = _boards(("MB-1", [to3, to2]), ("SB-3", []), ("SB-2", []))
    moved = dc.moved_references(before, after, _on("MB-1"), (), _on("MB-1"))
    assert [(m["before"], m["after"]) for m in moved] == [("feeder to SB-2", "feeder to SB-3")]


def test_a_deleted_board_is_not_taken_for_a_rename() -> None:
    # A deleted: B moves to A's position, but B was saved before, so it is not A renamed.
    before = _boards(("A", [LIGHT]), ("B", [HEAT]))
    after = _boards(("B", [HEAT]))
    # The user re-pointed the binding from A to B with the same id: their choice, not a move.
    assert dc.moved_references(before, after, _on("B"), (), _on("A")) == []
