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
