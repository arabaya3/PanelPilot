"""Configurations composed from data: generic, deterministic, and refusing to guess."""

from __future__ import annotations

import ast
import copy
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.design import snapshot
from app.design.model import compose, entities, versions, write
from app.design.model.read import project_to_v1, read_snapshot
from app.tests.design.model.v2_support import CONFIGS, HELD_OUT, config_case, v1_snapshot

DESIGN_DIR = Path(compose.__file__).resolve().parents[1]


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_every_configuration_composes_into_a_strict_v2_snapshot(name: str) -> None:
    v1, configuration = config_case(name)
    envelope = write.snapshot_v2_from_v1(v1, configuration)
    read = read_snapshot(envelope)
    assert read.schema == versions.V2
    model = read.v2
    assert model is not None
    assert project_to_v1(model) == v1_snapshot(CONFIGS[name]["fixture"])
    expect = CONFIGS[name]["expect"]
    assert sum(len(s.points) for s in model.strips) == expect["strip_points"]
    assert len(model.nets) == expect["nets"]
    assert len(model.connections) == expect["connections"]
    assert len(model.wires) == expect["wires"]
    devices = sum(len(b.devices) for b in model.design.boards)
    assert len([c for c in model.components if c.legacy_device_id]) == devices


def test_the_held_out_configuration_is_data_only() -> None:
    assert HELD_OUT in CONFIGS
    assert len(CONFIGS) >= 4


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_composition_pins_its_hash(name: str) -> None:
    v1, configuration = config_case(name)
    digest = snapshot.sha256_of(write.snapshot_v2_from_v1(v1, configuration))
    # Pinned in the case's data file: a change is a change to the stored bytes.
    assert digest == CONFIGS[name]["expect"]["sha256"]


def _shuffled(value: Any, rng: random.Random) -> Any:
    """The same configuration with every list of records and every object reordered.

    Lists whose order is meaning (net members and the indexes connections use,
    pattern strip roles) are kept; every other list is a set of keyed records.
    """
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: (v if k in {"members", "connections"} else _shuffled(v, rng)) for k, v in items}
    if isinstance(value, list):
        out = [_shuffled(v, rng) for v in value]
        if all(isinstance(v, dict) for v in out):
            rng.shuffle(out)
        return out
    return value


@pytest.mark.parametrize("name", sorted(CONFIGS))
@pytest.mark.parametrize("seed", range(10))
def test_canonical_bytes_do_not_depend_on_input_order(name: str, seed: int) -> None:
    v1, configuration = config_case(name)
    expected = snapshot.canonical_json(write.snapshot_v2_from_v1(v1, configuration))
    shuffled = _shuffled(configuration, random.Random(seed))
    assert snapshot.canonical_json(write.snapshot_v2_from_v1(v1, shuffled)) == expected


def test_the_input_is_not_changed() -> None:
    v1, configuration = config_case("feeder_1p")
    before = copy.deepcopy((v1, configuration))
    write.snapshot_v2_from_v1(v1, configuration)
    assert (v1, configuration) == before


def test_wire_attributes_are_only_what_data_gives() -> None:
    v1, configuration = config_case("feeder_1p")
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    given = [w for w in model.wires if w.number.source != "unset"]
    assert [(w.number.value, w.number.source) for w in given] == [("EX-101", "data")]
    for wire in model.wires:
        assert wire.cross_section_mm2 == entities.UNSET
        assert wire.colour == entities.UNSET


def test_board_scoped_nets_are_shared_across_bindings() -> None:
    v1, configuration = config_case("feeder_1p")
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    by_signal = {n.signal: n for n in model.nets if n.signal in {"N", "PE"}}
    assert set(by_signal) == {"N", "PE"}
    assert all(len(n.members) == 1 + 3 * 3 for n in by_signal.values())


# --------------------------------------------------------------------------- #
# The composer is generic: no code names a configured template or pattern
# --------------------------------------------------------------------------- #


def _configured_ids() -> set[str]:
    ids: set[str] = set()
    for body in CONFIGS.values():
        configuration = body["configuration"]
        refs = [t["ref"] for t in configuration["templates"]]
        refs += [p["ref"] for p in configuration["patterns"]]
        for ref in refs:
            ids.add(ref)
            ids.add(ref.split("/")[1])
        ids.add(configuration["name"])
    return ids


def test_no_design_code_names_a_configured_template_or_pattern() -> None:
    ids = _configured_ids()
    assert "synth-heldout-radial" in ids
    assert "tenant/synth-pe-n-distribution/v1" in ids  # pattern refs are checked too
    hits = []
    for path in sorted(DESIGN_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                hits += [(path.name, i) for i in ids if i in node.value]
    assert hits == []


# --------------------------------------------------------------------------- #
# Refusals: missing, ambiguous, contradictory or unsupported information
# --------------------------------------------------------------------------- #


def _pattern(config: dict[str, Any], index: int = 0) -> dict[str, Any]:
    pattern: dict[str, Any] = config["patterns"][index]
    return pattern


def _template(config: dict[str, Any], ref: str) -> dict[str, Any]:
    return next(t for t in config["templates"] if t["ref"] == ref)


def _unknown_template(c: dict[str, Any]) -> None:
    _pattern(c)["slots"][0]["template"] = "global/not-there/v1"


def _unknown_pattern(c: dict[str, Any]) -> None:
    c["bindings"][0]["pattern"] = "global/not-there/v1"


def _no_device_kind(c: dict[str, Any]) -> None:
    del _pattern(c)["slots"][0]["device_kind"]


def _no_candidate(c: dict[str, Any]) -> None:
    _pattern(c)["slots"][0]["device_kind"] = "contactor"


def _no_marking(c: dict[str, Any]) -> None:
    del c["bindings"][0]["markings"]["pe"]


def _no_cable(c: dict[str, Any]) -> None:
    c["bindings"][0]["cable"] = None


def _no_strip(c: dict[str, Any]) -> None:
    c["bindings"][0]["strips"] = {}


def _terminal_not_on_template(c: dict[str, Any]) -> None:
    _pattern(c)["nets"][0]["members"][0]["terminal"] = "out9"


def _restricted_scope(c: dict[str, Any]) -> None:
    _template(c, "global/synth-mcb-1p/v1")["restricted_scope"] = ["fire_pump"]


def _contradictory_nets(c: dict[str, Any]) -> None:
    line = _pattern(c)["nets"][0]
    line["members"].append({"slot": "nbar", "terminal": "bar"})


def _unknown_circuit(c: dict[str, Any]) -> None:
    c["bindings"][0]["circuit"] = "no-circuit"


def _unknown_device(c: dict[str, Any]) -> None:
    c["bindings"][0]["slots"]["breaker"] = {"device": "no-device"}


def _unknown_board(c: dict[str, Any]) -> None:
    c["bindings"][0]["board"] = "NO-BOARD"


def _duplicate_marking(c: dict[str, Any]) -> None:
    c["bindings"][1]["markings"] = dict(c["bindings"][0]["markings"])


def _core_out_of_range(c: dict[str, Any]) -> None:
    _pattern(c)["nets"][0]["connections"][1]["core"] = 4


def _core_reused(c: dict[str, Any]) -> None:
    _pattern(c)["nets"][1]["connections"][1]["core"] = 1


def _unknown_wire(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {"line/1": {"number": "x"}}


def _unknown_key(c: dict[str, Any]) -> None:
    c["defaults"] = {}


def _unknown_binding_key(c: dict[str, Any]) -> None:
    c["bindings"][0]["guess"] = True


def _invalid_template(c: dict[str, Any]) -> None:
    c["templates"][0]["terminals"][0]["rating_a"] = "16"


def _legacy_redefined(c: dict[str, Any]) -> None:
    c["templates"].append(
        {**copy.deepcopy(c["templates"][0]), "ref": "global/legacy-circuit_breaker/v1"}
    )


def _board_mismatch(c: dict[str, Any]) -> None:
    c["components"][0]["board"] = "elsewhere"


def _numeric_wire_number(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {"line/0": {"number": 101}}


def _assumption_without_statement(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {"line/0": {"colour": {"value": "x", "source": "assumption"}}}


def _statement_without_assumption(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {
        "line/0": {"colour": {"value": "x", "source": "data", "statement": "s"}}
    }


def _unknown_wire_source(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {"line/0": {"colour": {"value": "x", "source": "rule"}}}


def _empty_given_value(c: dict[str, Any]) -> None:
    c["bindings"][0]["wires"] = {"line/0": {"colour": {"value": "", "source": "user"}}}


def _pattern_without_review(c: dict[str, Any]) -> None:
    del _pattern(c)["review"]


def _pattern_ref_with_at(c: dict[str, Any]) -> None:
    _pattern(c)["ref"] = "global/a@b/v1"
    c["bindings"][0]["pattern"] = "global/a@b/v1"


def _internal_to_a_component(c: dict[str, Any]) -> None:
    _pattern(c)["nets"][0]["connections"][0]["via"] = "internal"


REFUSALS: dict[str, tuple[Callable[[dict[str, Any]], None], str, set[str]]] = {
    "unknown_template": (_unknown_template, "unknown_template", {"binding", "slot", "ref"}),
    "unknown_pattern": (_unknown_pattern, "unknown_pattern", {"binding"}),
    "no_device_kind": (_no_device_kind, "missing_binding", {"binding", "slot"}),
    "no_candidate": (_no_candidate, "missing_binding", {"binding", "slot"}),
    "no_marking": (_no_marking, "missing_binding", {"binding", "point"}),
    "no_cable": (_no_cable, "missing_binding", {"binding"}),
    "no_strip": (_no_strip, "missing_binding", {"binding"}),
    "terminal_not_on_template": (
        _terminal_not_on_template,
        "terminal_not_on_template",
        {"template", "terminal"},
    ),
    "restricted_scope": (_restricted_scope, "restricted_scope", {"ref", "scope"}),
    "contradictory_nets": (_contradictory_nets, "contradictory_nets", {"binding", "role"}),
    "unknown_circuit": (_unknown_circuit, "unknown_reference", {"binding", "circuit"}),
    "unknown_device": (_unknown_device, "unknown_reference", {"binding", "device"}),
    "unknown_board": (_unknown_board, "unknown_reference", {"binding", "board"}),
    "duplicate_marking": (_duplicate_marking, "duplicate_marking", {"strip"}),
    "core_out_of_range": (_core_out_of_range, "core_out_of_range", {"cable", "core"}),
    "core_reused": (_core_reused, "core_reused", {"cable"}),
    "unknown_wire": (_unknown_wire, "unknown_wire", {"binding", "wires"}),
    "unknown_key": (_unknown_key, "configuration_invalid", {"where"}),
    "unknown_binding_key": (_unknown_binding_key, "configuration_invalid", {"where"}),
    "invalid_template": (_invalid_template, "template_invalid", set()),
    "legacy_redefined": (_legacy_redefined, "template_conflict", {"ref"}),
    # A component on a board the design lacks is refused on its own (M7b: every
    # configuration component is composed), before any binding uses it.
    "component_board_unknown": (_board_mismatch, "unknown_reference", {"board", "component"}),
    "numeric_wire_number": (_numeric_wire_number, "configuration_invalid", {"field"}),
    "assumption_without_statement": (
        _assumption_without_statement,
        "configuration_invalid",
        {"field"},
    ),
    "statement_without_assumption": (
        _statement_without_assumption,
        "configuration_invalid",
        {"field"},
    ),
    "unknown_wire_source": (_unknown_wire_source, "configuration_invalid", {"field"}),
    "empty_given_value": (_empty_given_value, "configuration_invalid", {"field"}),
    "pattern_without_review": (_pattern_without_review, "configuration_invalid", {"where"}),
    "pattern_ref_with_at": (_pattern_ref_with_at, "pattern_invalid", set()),
    "internal_to_a_component": (_internal_to_a_component, "pattern_invalid", set()),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_the_composer_refuses_rather_than_guesses(case: str) -> None:
    mutate, code, params = REFUSALS[case]
    v1, configuration = config_case("feeder_1p")
    mutate(configuration)
    with pytest.raises(compose.CompositionRefusedError) as refused:
        compose.compose(v1, configuration)
    assert refused.value.code == code
    assert set(refused.value.params) == params
    assert isinstance(refused.value, ValueError)


def test_two_candidate_devices_are_an_ambiguous_binding() -> None:
    v1, configuration = config_case("motor_dol_with_control")
    binding = copy.deepcopy(configuration["bindings"][0])
    binding.update(id="c2-power", circuit="c2", cable="c2-cable")
    binding["markings"] = {"u": "U2", "v": "V2", "w": "W2", "pe": "PE2"}
    configuration["bindings"].append(binding)
    with pytest.raises(compose.CompositionRefusedError) as refused:
        compose.compose(v1, configuration)
    assert refused.value.code == "ambiguous_binding"
    assert refused.value.params["slot"] == "contactor"
    assert refused.value.params["candidates"] == "c2-delta,c2-line,c2-star"


def test_naming_the_device_resolves_the_ambiguity() -> None:
    v1, configuration = config_case("motor_dol_with_control")
    binding = copy.deepcopy(configuration["bindings"][0])
    binding.update(id="c2-power", circuit="c2", cable="c2-cable")
    binding["markings"] = {"u": "U2", "v": "V2", "w": "W2", "pe": "PE2"}
    binding["slots"] = {"motor": {"component": "motor-c2"}, "contactor": {"device": "c2-line"}}
    configuration["bindings"].append(binding)
    configuration["components"].append({**configuration["components"][1], "id": "motor-c2"})
    payload = compose.compose(v1, configuration)
    assert read_snapshot(write.envelope(payload)).v2 is not None


def test_one_device_bound_to_two_templates_is_refused() -> None:
    v1, configuration = config_case("motor_dol_with_control")
    configuration["patterns"][1]["slots"][1]["template"] = "global/synth-overload-3p/v1"
    with pytest.raises(compose.CompositionRefusedError) as refused:
        compose.compose(v1, configuration)
    assert refused.value.code in {"conflicting_binding", "terminal_not_on_template"}


def test_an_invalid_v1_design_is_unreadable_not_composed() -> None:
    v1, configuration = config_case("feeder_1p")
    v1["boards"][0]["unexpected"] = 1
    with pytest.raises(versions.SnapshotUnreadableError):
        compose.compose(v1, configuration)


# --------------------------------------------------------------------------- #
# Patterns: embedded, locked and traceable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_every_used_pattern_is_embedded_and_every_created_entity_names_it(name: str) -> None:
    v1, configuration = config_case(name)
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    used = {b["pattern"] for b in configuration["bindings"]}
    assert {p.ref for p in model.patterns} == used
    assert {e.ref for e in model.pattern_lock} == used
    assert all(x.pattern_ref in used for x in model.connections)
    assert all(n.pattern_refs and set(n.pattern_refs) <= used for n in model.nets)
    assert all(p.pattern_ref in used for s in model.strips for p in s.points)
    for component in model.components:
        if component.legacy_device_id is None or component.pattern_refs:
            assert set(component.pattern_refs) <= used
    legacy = [c for c in model.components if c.template_ref.startswith("global/legacy")]
    assert all(c.pattern_refs == () for c in legacy)


def test_a_migrated_design_embeds_no_pattern() -> None:
    model = read_snapshot(write.snapshot_v2_from_v1(v1_snapshot("residential"))).v2
    assert model is not None
    assert model.patterns == ()
    assert model.pattern_lock == ()


def test_wire_values_keep_the_source_their_input_states() -> None:
    v1, configuration = config_case("pe_n_distribution")
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    sources = sorted(
        (getattr(w, n).source, getattr(w, n).value)
        for w in model.wires
        for n in ("number", "cross_section_mm2", "colour")
        if getattr(w, n).source != "unset"
    )
    assert sources == [("assumption", "SYN-COLOUR-A"), ("data", "SYN-1"), ("user", "SYN-2")]
    (assumed,) = [w.colour for w in model.wires if w.colour.source == "assumption"]
    (record,) = model.assumptions
    assert assumed.assumption == record.id
    assert record.statement.startswith("Synthetic illustrative assumption")


def test_no_assumption_is_ever_made_up() -> None:
    for name in sorted(CONFIGS):
        v1, configuration = config_case(name)
        model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
        assert model is not None
        stated = [
            given
            for b in configuration["bindings"]
            for wire in b["wires"].values()
            for given in wire.values()
            if isinstance(given, dict) and given.get("source") == "assumption"
        ]
        assert len(model.assumptions) == len(stated)
