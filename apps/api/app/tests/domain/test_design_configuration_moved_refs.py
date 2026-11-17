"""Which binding references are not proven to name what they named (pure; SYNTHETIC stand-ins).

A reference is proven only when the load its id names has the same fingerprint as the
load it named when saved: every entered field, or the fed board for a feeder, and the
binding context the design gives it. Anything else is refused until chosen again or
confirmed with its token.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.domain import design_configuration as dc

Load = dict[str, Any]


def _load(description: str, kind: str = "socket", power: str = "1") -> Load:
    return {"description": description, "load": kind, "power_kw": power}


LIGHT = _load("Lighting", "lighting")
HEAT = _load("Heater", "socket", "1.5")


def _side(*boards: tuple[str, list[Load], list[str]]) -> tuple[Any, dict[str, Any]]:
    """A v1 design and the request it was designed from.

    Each board is ``(name, entered loads, boards it feeds)``; the feeders come after
    the entered loads, as the server appends them.
    """
    design_boards = []
    request_boards = []
    fed_from = {child: name for name, _l, children in boards for child in children}
    for name, loads, children in boards:
        rows: list[tuple[str, str, str | None]] = [
            (load["description"], load["load"], None) for load in loads
        ]
        rows += [(f"Feeder to {child}", "sub_board", child) for child in children]
        circuits = [
            SimpleNamespace(
                id=f"c{i}",
                description=description,
                load=kind,
                feeds=feeds,
                cable_id=f"c{i}-cable",
                device_ids=[f"c{i}-breaker"],
            )
            for i, (description, kind, feeds) in enumerate(rows, start=1)
        ]
        design_boards.append(SimpleNamespace(id=name, circuits=circuits))
        board: dict[str, Any] = {"name": name, "loads": loads}
        if name in fed_from:
            board["fed_from"] = fed_from[name]
        request_boards.append(board)
    request = {"info": {"name": "SYNTHETIC"}, "boards": request_boards}
    return SimpleNamespace(boards=design_boards), request


def _config(
    board: str = "MB-1",
    circuit: str | None = "c1",
    cable: str | None = None,
    device: str | None = None,
) -> Any:
    slots = {"q": {"device": device}} if device else {}
    return {
        "bindings": [
            {"id": "b1", "board": board, "circuit": circuit, "cable": cable, "slots": slots}
        ]
    }


def _moved(
    before: tuple[Any, Any],
    after: tuple[Any, Any],
    config: Any,
    saved: Any = None,
    confirmed: Any = (),
) -> list[dict[str, str]]:
    return dc.moved_references(
        before[0],
        after[0],
        config,
        confirmed,
        _config() if saved is None else saved,
        baseline_request=before[1],
        current_request=after[1],
    )


def _confirm(entry: dict[str, str]) -> dict[str, Any]:
    return {k: entry[k] for k in ("binding", "field", "key", "id", "token")}


def test_the_stand_in_request_reads_as_a_request() -> None:
    # Otherwise every test below would pass for the wrong reason (nothing proven).
    assert dc._entered_loads(_side(("MB-1", [LIGHT], []))[1])["MB-1"][0]["description"] == (
        "Lighting"
    )


# --------------------------------------------------------------------------- #
# Loads
# --------------------------------------------------------------------------- #


def test_an_unchanged_reference_to_another_load_now_is_refused() -> None:
    moved = _moved(_side(("MB-1", [LIGHT], [])), _side(("MB-1", [HEAT, LIGHT], [])), _config())
    assert [(m["id"], m["before"], m["after"]) for m in moved] == [("c1", "Lighting", "Heater")]
    assert (moved[0]["before_board"], moved[0]["after_board"]) == ("MB-1", "MB-1")
    assert moved[0]["token"]


def test_cables_and_design_devices_move_with_their_circuit() -> None:
    config = _config(circuit=None, cable="c1-cable", device="c1-breaker")
    moved = _moved(_side(("MB-1", [LIGHT], [])), _side(("MB-1", [HEAT, LIGHT], [])), config, config)
    assert [(m["field"], m["key"], m["id"]) for m in moved] == [
        ("cable", "", "c1-cable"),
        ("slot", "q", "c1-breaker"),
    ]


def test_appended_changed_by_the_user_or_new_are_not_checked() -> None:
    light = _side(("MB-1", [LIGHT], []))
    assert _moved(light, _side(("MB-1", [LIGHT, HEAT], [])), _config()) == []
    swapped = _side(("MB-1", [HEAT, LIGHT], []))
    # The user chose c2 against the current choices.
    assert _moved(light, swapped, _config(circuit="c2")) == []
    # Nothing saved before; or the binding is new since.
    assert dc.moved_references(light[0], swapped[0], _config(), (), None) == []
    assert _moved(light, swapped, _config(), {"bindings": []}) == []
    # Gone: the composer refuses it (unknown reference), not this check.
    assert _moved(light, _side(("MB-1", [], [])), _config()) == []


def test_same_description_and_kind_is_not_proof_of_the_same_load() -> None:
    small, large = _load("Sockets", "socket", "1"), _load("Sockets", "socket", "2.5")
    moved = _moved(
        _side(("MB-1", [small, large], [])), _side(("MB-1", [large, small], [])), _config()
    )
    assert [(m["before"], m["after"]) for m in moved] == [("Sockets", "Sockets")]


def test_an_identical_swap_is_indistinguishable_and_accepted() -> None:
    one = _load("Sockets", "socket", "1")
    # Every entered field and the binding context are equal: the model cannot tell them apart.
    before = _side(("MB-1", [one, dict(one)], []))
    after = _side(("MB-1", [dict(one), one], []))
    assert _moved(before, after, _config()) == []


def test_a_changed_binding_context_is_refused() -> None:
    before = _side(("MB-1", [LIGHT], []))
    after = _side(("MB-1", [LIGHT], []))
    after[0].boards[0].circuits[0].device_ids = ["c1-breaker", "c1-contactor"]
    assert len(_moved(before, after, _config())) == 1


# --------------------------------------------------------------------------- #
# Boards
# --------------------------------------------------------------------------- #


def test_a_renamed_board_is_proven_only_where_its_loads_are() -> None:
    before = _side(("MB-1", [LIGHT], []))
    # The binding is re-pointed to the renamed board: c1 is still the same entered load.
    assert _moved(before, _side(("MB-9", [LIGHT], [])), _config("MB-9")) == []
    # Renamed and a load inserted: c1 on MB-9 is Heater, not Lighting.
    moved = _moved(before, _side(("MB-9", [HEAT, LIGHT], [])), _config("MB-9"))
    assert [(m["before_board"], m["after_board"], m["after"]) for m in moved] == [
        ("MB-1", "MB-9", "Heater")
    ]


def test_swapped_board_names_are_refused() -> None:
    before = _side(("MB-1", [LIGHT], []), ("MB-2", [HEAT], []))
    after = _side(("MB-1", [HEAT], []), ("MB-2", [LIGHT], []))
    moved = _moved(before, after, _config("MB-1"), _config("MB-1"))
    assert [(m["before"], m["after"]) for m in moved] == [("Lighting", "Heater")]


def test_a_rename_chain_is_refused_where_the_load_differs() -> None:
    # A -> B while B -> C: the binding re-pointed to B keeps c1, now another load.
    before = _side(("A", [LIGHT], []), ("B", [HEAT], []))
    after = _side(("B", [_load("Pump", "motor")], []), ("C", [HEAT], []))
    moved = _moved(before, after, _config("B"), _config("A"))
    assert [(m["before_board"], m["after_board"]) for m in moved] == [("A", "B")]


def test_a_deleted_board_leaves_a_re_pointed_reference_unproven() -> None:
    before = _side(("A", [LIGHT], []), ("B", [HEAT], []))
    after = _side(("B", [HEAT], []))
    # Re-pointed from deleted A to B with the same id: c1 on B is not the load c1 on A was.
    assert len(_moved(before, after, _config("B"), _config("A"))) == 1


def test_reordered_fed_boards_move_their_feeders() -> None:
    before = _side(("MB-1", [], ["SB-2", "SB-3"]), ("SB-2", [], []), ("SB-3", [], []))
    after = _side(("MB-1", [], ["SB-3", "SB-2"]), ("SB-3", [], []), ("SB-2", [], []))
    moved = _moved(before, after, _config())
    assert [(m["before"], m["after"]) for m in moved] == [("Feeder to SB-2", "Feeder to SB-3")]


def test_a_renamed_fed_board_needs_confirmation() -> None:
    before = _side(("MB-1", [], ["SB-1"]), ("SB-1", [], []))
    after = _side(("MB-1", [], ["SB-2"]), ("SB-2", [], []))
    assert [m["after"] for m in _moved(before, after, _config())] == ["Feeder to SB-2"]


def test_an_unreadable_stored_request_proves_nothing() -> None:
    before, after = _side(("MB-1", [LIGHT], [])), _side(("MB-1", [LIGHT], []))
    assert len(_moved((before[0], {"not": "a request"}), after, _config())) == 1


def test_no_stored_design_proves_nothing() -> None:
    before, after = _side(("MB-1", [LIGHT], [])), _side(("MB-1", [LIGHT], []))
    assert len(_moved((None, before[1]), after, _config())) == 1


# --------------------------------------------------------------------------- #
# Confirmations
# --------------------------------------------------------------------------- #


def test_a_confirmation_is_bound_to_its_exact_before_and_after() -> None:
    before = _side(("MB-1", [LIGHT], []))
    after = _side(("MB-1", [HEAT, LIGHT], []))
    (entry,) = _moved(before, after, _config())
    assert _moved(before, after, _config(), confirmed=[_confirm(entry)]) == []
    # Without the right token, or for another field, it does not apply.
    wrong_token = {**_confirm(entry), "token": "x"}
    other_field = {**_confirm(entry), "field": "cable"}
    assert _moved(before, after, _config(), confirmed=[wrong_token]) != []
    assert _moved(before, after, _config(), confirmed=[other_field]) != []
    # A later edit of the load now there invalidates it.
    edited = _side(("MB-1", [_load("Heater", "socket", "2"), LIGHT], []))
    again = _moved(before, edited, _config(), confirmed=[_confirm(entry)])
    assert len(again) == 1
    assert again[0]["token"] != entry["token"]


def test_check_references_refuses_naming_every_unproven_reference() -> None:
    config = _config(cable="c1-cable")
    before, after = _side(("MB-1", [LIGHT], [])), _side(("MB-1", [HEAT], []))
    with pytest.raises(ValidationError) as refused:
        dc.check_references(
            before[0],
            after[0],
            config,
            (),
            config,
            baseline_request=before[1],
            current_request=after[1],
        )
    params = refused.value.params
    assert params["refusal"] == "reference_moved"
    assert (params["binding"], params["field"], params["ref"]) == ("b1", "circuit", "c1")
    assert (params["before_board"], params["after_board"]) == ("MB-1", "MB-1")
    entries = params["moved"].split(";")
    assert [e.split("|")[:4] for e in entries] == [
        ["b1", "circuit", "", "c1"],
        ["b1", "cable", "", "c1-cable"],
    ]
    assert entries[0].split("|")[4] == params["token"]


# --------------------------------------------------------------------------- #
# A board's own devices
# --------------------------------------------------------------------------- #


def _with_devices(side: tuple[Any, Any], groups: dict[str, list[str]]) -> tuple[Any, Any]:
    """Give each board an incomer and residual current groups feeding the named circuits."""
    for board in side[0].boards:
        board.incomer_ids = ["incomer"]
        board.devices = [SimpleNamespace(id="incomer", kind="switch", description="Incomer")]
        for group, members in groups.items():
            board.devices.append(SimpleNamespace(id=group, kind="rcd", description=None))
            for circuit in board.circuits:
                if circuit.id in members:
                    circuit.upstream_id = group
    return side


def _slot(board: str, device: str) -> Any:
    return _config(board, circuit=None, device=device)


def test_a_renumbered_residual_current_group_is_refused() -> None:
    # g1-rcd fed Lighting; after the edit g1-rcd feeds Heater.
    before = _with_devices(_side(("MB-1", [LIGHT, HEAT], [])), {"g1-rcd": ["c1"]})
    after = _with_devices(_side(("MB-1", [LIGHT, HEAT], [])), {"g1-rcd": ["c2"]})
    config = _slot("MB-1", "g1-rcd")
    moved = _moved(before, after, config, config)
    assert [(m["field"], m["id"]) for m in moved] == [("slot", "g1-rcd")]
    # The same group feeding the same loads is proven.
    same = _with_devices(_side(("MB-1", [LIGHT, HEAT], [])), {"g1-rcd": ["c1"]})
    assert _moved(before, same, config, config) == []


def test_an_incomer_follows_its_board_not_its_name() -> None:
    before = _with_devices(_side(("MB-1", [LIGHT], []), ("MB-2", [HEAT], [])), {})
    swapped = _with_devices(_side(("MB-2", [HEAT], []), ("MB-1", [LIGHT], [])), {})
    swapped[1]["boards"][0]["feeder_length_m"] = "9"  # what is entered for the board differs
    config = _slot("MB-1", "incomer")
    assert len(_moved(before, swapped, config, config)) == 1
    same = _with_devices(_side(("MB-1", [LIGHT], []), ("MB-2", [HEAT], [])), {})
    assert _moved(before, same, config, config) == []


def test_unreadable_on_both_sides_still_proves_nothing() -> None:
    before, after = _side(("MB-1", [LIGHT], [])), _side(("MB-1", [LIGHT], []))
    assert len(_moved((before[0], {"x": 1}), (after[0], {"x": 1}), _config())) == 1


def test_a_refusal_names_each_reference_in_full() -> None:
    config = _config(cable="c1-cable", device="c1-breaker")
    before, after = _side(("MB-1", [LIGHT], [])), _side(("MB-1", [HEAT], []))
    with pytest.raises(ValidationError) as refused:
        dc.check_references(
            before[0],
            after[0],
            config,
            (),
            config,
            baseline_request=before[1],
            current_request=after[1],
        )
    params = refused.value.params
    assert params["moved_count"] == "3"
    assert [params[f"r{i}_field"] for i in range(3)] == ["circuit", "cable", "slot"]
    assert all(params[f"r{i}_before"] == "Lighting" for i in range(3))
    assert all(params[f"r{i}_after_board"] == "MB-1" for i in range(3))


def _grouped(order: list[tuple[str, str]]) -> tuple[Any, Any]:
    """MB-1 with Lighting and Heater, each group ``g{n}-breaker`` -> ``g{n}-rcd`` -> circuit."""
    side = _side(("MB-1", [LIGHT, HEAT], []))
    board = side[0].boards[0]
    board.incomer_ids = ["incomer"]
    board.devices = [SimpleNamespace(id="incomer", kind="switch", upstream_id=None)]
    for group, circuit in order:
        board.devices += [
            SimpleNamespace(id=f"{group}-breaker", kind="breaker", upstream_id="incomer"),
            SimpleNamespace(id=f"{group}-rcd", kind="rcd", upstream_id=f"{group}-breaker"),
        ]
        next(c for c in board.circuits if c.id == circuit).upstream_id = f"{group}-rcd"
    return side


def test_a_group_breaker_is_told_apart_by_what_it_feeds_below_it() -> None:
    before = _grouped([("g1", "c1"), ("g2", "c2")])
    regrouped = _grouped([("g1", "c2"), ("g2", "c1")])
    for device in ("g1-breaker", "g1-rcd"):
        config = _slot("MB-1", device)
        assert len(_moved(before, regrouped, config, config)) == 1, device
        same = _grouped([("g1", "c1"), ("g2", "c2")])
        assert _moved(before, same, config, config) == [], device


def test_a_board_device_feeding_no_circuit_is_never_proven() -> None:
    before, after = _grouped([("g1", "c1")]), _grouped([("g1", "c1")])
    for side in (before, after):
        side[0].boards[0].devices.append(
            SimpleNamespace(id="spare", kind="breaker", upstream_id="incomer")
        )
    config = _slot("MB-1", "spare")
    assert len(_moved(before, after, config, config)) == 1
