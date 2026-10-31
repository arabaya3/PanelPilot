"""``codec_v2``: the strict v2 reader, its canonical writer and every invariant."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.design import snapshot
from app.design.model import codec_v2, entities, migrate_v2, versions, write
from app.design.model.read import read_snapshot
from app.tests.design.model.v2_support import config_case, v1_snapshot

MODEL_DIR = Path(migrate_v2.__file__).parent
API_ROOT = MODEL_DIR.parents[2]
PURE_MODULES = ("entities.py", "findings_v2.py", "domain.py")
FRAMEWORKS = ("pydantic", "fastapi", "sqlalchemy", "starlette")


def _composed(name: str = "feeder_1p") -> dict[str, Any]:
    v1, configuration = config_case(name)
    return write.snapshot_v2_from_v1(v1, configuration)


# --------------------------------------------------------------------------- #
# Round trip and canonical form
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["feeder_1p", "motor_dol_with_control", "io_marshalling"])
def test_read_of_write_is_the_model(name: str) -> None:
    envelope = _composed(name)
    model = read_snapshot(envelope).v2
    assert model is not None
    rewritten = write.to_stored_v2(model)
    assert rewritten == envelope
    assert snapshot.canonical_json(rewritten) == snapshot.canonical_json(envelope)
    assert read_snapshot(rewritten).v2 == model


def test_dump_sorts_a_model_built_out_of_order() -> None:
    model = read_snapshot(_composed()).v2
    assert model is not None
    shuffled = entities.DesignModelV2(
        design=model.design,
        templates=model.templates[::-1],
        registry_lock=model.registry_lock[::-1],
        components=model.components[::-1],
        strips=model.strips[::-1],
        nets=tuple(
            entities.Net(n.id, n.board_id, n.signal, n.origin, n.members[::-1])
            for n in model.nets[::-1]
        ),
        connections=model.connections[::-1],
        wires=model.wires[::-1],
        assumptions=model.assumptions,
    )
    assert write.to_stored_v2(shuffled) == write.to_stored_v2(model)


# --------------------------------------------------------------------------- #
# Tampering: every one is an unreadable snapshot
# --------------------------------------------------------------------------- #


def _first_wire_net(model: dict[str, Any]) -> int:
    return next(i for i, n in enumerate(model["nets"]) if len(n["members"]) >= 2)


def _resort(model: dict[str, Any]) -> None:
    for net in model["nets"]:
        net["members"].sort(key=codec_v2.ref_key)


def _terminal_in_two_nets(model: dict[str, Any]) -> None:
    first, second = model["nets"][0], model["nets"][1]
    second["members"].append(copy.deepcopy(first["members"][0]))
    _resort(model)


def _template_changed(model: dict[str, Any]) -> None:
    model["templates"][-1]["function"] = "something_else"


def _lock_digest_changed(model: dict[str, Any]) -> None:
    model["registry_lock"]["templates"][0]["digest"] = "0" * 64


def _lock_entry_missing(model: dict[str, Any]) -> None:
    model["registry_lock"]["templates"].pop()


def _lock_entry_extra(model: dict[str, Any]) -> None:
    model["registry_lock"]["templates"].append({"ref": "global/zzz/v1", "digest": "0" * 64})


def _template_unused(model: dict[str, Any]) -> None:
    extra = copy.deepcopy(migrate_v2.LEGACY_TEMPLATES["other"])
    extra["ref"] = "global/zzz-unused/v1"
    model["templates"].append(extra)
    model["registry_lock"] = migrate_v2.lock_of(model["templates"])


def _unsorted_components(model: dict[str, Any]) -> None:
    model["components"].reverse()


def _unsorted_points(model: dict[str, Any]) -> None:
    model["strips"][0]["points"].reverse()


def _duplicate_component(model: dict[str, Any]) -> None:
    model["components"].insert(1, copy.deepcopy(model["components"][0]))


def _dangling_member(model: dict[str, Any]) -> None:
    member = next(m for n in model["nets"] for m in n["members"] if "component" in m)
    member["component"] = "c-missing"
    _resort(model)


def _member_terminal_not_on_template(model: dict[str, Any]) -> None:
    member = next(m for n in model["nets"] for m in n["members"] if "component" in m)
    member["terminal"] = "no-such-terminal"
    _resort(model)


def _dangling_net(model: dict[str, Any]) -> None:
    model["connections"][0]["net_id"] = "n-missing"


def _dangling_board(model: dict[str, Any]) -> None:
    model["components"][0]["board_id"] = "NO-BOARD"


def _dangling_device(model: dict[str, Any]) -> None:
    component = next(c for c in model["components"] if c["legacy_device_id"])
    component["legacy_device_id"] = "no-device"


def _dangling_template(model: dict[str, Any]) -> None:
    model["components"][0]["template_ref"] = "global/zzz/v1"


def _dangling_connection(model: dict[str, Any]) -> None:
    model["wires"][0]["connection_id"] = "x-missing"


def _dangling_cable(model: dict[str, Any]) -> None:
    connection = next(x for x in model["connections"] if x["core"])
    connection["core"]["cable_id"] = "no-cable"


def _connection_outside_net(model: dict[str, Any]) -> None:
    other = model["nets"][1]["members"][0]
    connection = next(x for x in model["connections"] if x["net_id"] == model["nets"][0]["id"])
    connection["to"] = copy.deepcopy(other)


def _connection_to_itself(model: dict[str, Any]) -> None:
    model["connections"][0]["to"] = copy.deepcopy(model["connections"][0]["from"])


def _duplicate_marking(model: dict[str, Any]) -> None:
    points = model["strips"][0]["points"]
    points[1]["marking"] = points[0]["marking"]


def _float_position(model: dict[str, Any]) -> None:
    model["strips"][0]["points"][0]["position"] = 1.0


def _float_cross_section(model: dict[str, Any]) -> None:
    model["wires"][0]["cross_section_mm2"] = {"value": 1.5, "source": "data"}


def _unset_with_a_value(model: dict[str, Any]) -> None:
    model["wires"][0]["colour"] = {"value": "x", "source": "unset"}


def _given_without_a_value(model: dict[str, Any]) -> None:
    model["wires"][0]["colour"] = {"value": None, "source": "user"}


def _unknown_field(model: dict[str, Any]) -> None:
    model["components"][0]["rating"] = "16"


def _unknown_model_key(model: dict[str, Any]) -> None:
    model["cables"] = []


def _missing_model_key(model: dict[str, Any]) -> None:
    del model["assumptions"]


def _design_not_canonical(model: dict[str, Any]) -> None:
    model["design"]["boards"][0]["supply"]["voltage_v"] = "+230"


def _design_unknown_field(model: dict[str, Any]) -> None:
    model["design"]["boards"][0]["terminals"] = []


def _core_past_the_cable(model: dict[str, Any]) -> None:
    connection = next(x for x in model["connections"] if x["core"])
    connection["core"]["index"] = 99


def _bool_as_int(model: dict[str, Any]) -> None:
    model["strips"][0]["points"][0]["level"] = True


def _two_wires_on_a_connection(model: dict[str, Any]) -> None:
    model["wires"][1]["connection_id"] = model["wires"][0]["connection_id"]


TAMPERS: dict[str, Callable[[dict[str, Any]], None]] = {
    f.__name__.lstrip("_"): f
    for f in (
        _terminal_in_two_nets,
        _template_changed,
        _lock_digest_changed,
        _lock_entry_missing,
        _lock_entry_extra,
        _template_unused,
        _unsorted_components,
        _unsorted_points,
        _duplicate_component,
        _dangling_member,
        _member_terminal_not_on_template,
        _dangling_net,
        _dangling_board,
        _dangling_device,
        _dangling_template,
        _dangling_connection,
        _dangling_cable,
        _connection_outside_net,
        _connection_to_itself,
        _duplicate_marking,
        _float_position,
        _float_cross_section,
        _unset_with_a_value,
        _given_without_a_value,
        _unknown_field,
        _unknown_model_key,
        _missing_model_key,
        _design_not_canonical,
        _design_unknown_field,
        _core_past_the_cable,
        _bool_as_int,
        _two_wires_on_a_connection,
    )
}


#: Why each tamper is refused: the reader must refuse it for that reason.
REASONS: dict[str, str] = {
    "bool_as_int": "is not an integer",
    "connection_outside_net": "an end is not a member of its net",
    "connection_to_itself": "joins a terminal to itself",
    "core_past_the_cable": "past the cable's cores",
    "dangling_board": "names no board",
    "dangling_cable": "names no cable",
    "dangling_connection": "names no connection made by wire",
    "dangling_device": "names no device of its board",
    "dangling_member": "names no component",
    "dangling_net": "names no net",
    "dangling_template": "names no embedded template",
    "design_not_canonical": "not in its stored",
    "design_unknown_field": "not a pp-design/1 design",
    "duplicate_component": "mirrored by two components",
    "duplicate_marking": "share a marking and level",
    "float_cross_section": "holds a float",
    "float_position": "holds a float",
    "given_without_a_value": "null exactly when",
    "lock_digest_changed": "does not match its template",
    "lock_entry_extra": "does not lock exactly",
    "lock_entry_missing": "does not lock exactly",
    "member_terminal_not_on_template": "is not a terminal of its template",
    "missing_model_key": r"missing \['assumptions'\]",
    "template_changed": "does not match its template",
    "template_unused": "does not use",
    "terminal_in_two_nets": "member of two nets",
    "two_wires_on_a_connection": "has two wires",
    "unknown_field": r"unknown \['rating'\]",
    "unknown_model_key": r"unknown \['cables'\]",
    "unset_with_a_value": "null exactly when",
    "unsorted_components": "is not sorted",
    "unsorted_points": "is not sorted",
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_a_tampered_snapshot_is_unreadable_for_its_reason(tamper: str) -> None:
    envelope = _composed()
    TAMPERS[tamper](envelope["model"])
    with pytest.raises(versions.SnapshotUnreadableError, match=REASONS[tamper]):
        read_snapshot(envelope)


def test_every_tamper_has_its_reason() -> None:
    assert set(REASONS) == set(TAMPERS)


def test_the_untampered_snapshot_reads() -> None:
    assert read_snapshot(_composed()).v2 is not None


@pytest.mark.parametrize(
    "envelope",
    [
        {"schema": "pp-design/2", "model": {}, "extra": 1},
        {"schema": "pp-design/2"},
        {"schema": "pp-design/2", "model": []},
    ],
)
def test_a_bad_envelope_is_unreadable(envelope: dict[str, Any]) -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(envelope)


def test_an_extra_envelope_key_beside_a_valid_model_is_unreadable() -> None:
    envelope = _composed()
    envelope["hash"] = "x"
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(envelope)


def test_v1_content_beside_a_v2_marker_is_unreadable() -> None:
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot({**v1_snapshot("residential"), "schema": "pp-design/2"})


def test_a_v1_envelope_is_still_refused_as_unknown() -> None:
    with pytest.raises(versions.SnapshotSchemaUnknownError):
        read_snapshot({"schema": "pp-design/1", "model": v1_snapshot("residential")})


def test_nesting_past_the_recursion_limit_is_unreadable() -> None:
    envelope = _composed()
    deep: Any = []
    for _ in range(sys.getrecursionlimit() + 100):
        deep = [deep]
    envelope["model"]["assumptions"] = deep
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(envelope)


# --------------------------------------------------------------------------- #
# Template ref grammar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ref", ["global/legacy-circuit_breaker/v1", "tenant/synth-io.4di/v12", "global/a/v999999999"]
)
def test_a_ref_is_scope_id_and_version(ref: str) -> None:
    template = copy.deepcopy(migrate_v2.LEGACY_TEMPLATES["other"])
    template["ref"] = ref
    assert codec_v2.parse_template(template).ref == ref


@pytest.mark.parametrize(
    "ref",
    [
        "global/legacy-other@1",
        "global/legacy-other/v1@1",
        "global/a@b/v1",
        "@global/a/v1",
        "global/a/v0",
        "global/a/1",
        "local/a/v1",
        "global/A/v1",
        "global/a/v1\n",
        "global/a/b/v1",
    ],
)
def test_a_ref_outside_the_grammar_is_refused(ref: str) -> None:
    template = copy.deepcopy(migrate_v2.LEGACY_TEMPLATES["other"])
    template["ref"] = ref
    with pytest.raises(versions.SnapshotUnreadableError, match="is not <global"):
        codec_v2.parse_template(template)


def test_no_shipped_ref_holds_an_at_sign() -> None:
    assert all("@" not in t["ref"] for t in migrate_v2.LEGACY_TEMPLATES.values())
    envelope = _composed()
    assert "@" not in json.dumps(envelope)
