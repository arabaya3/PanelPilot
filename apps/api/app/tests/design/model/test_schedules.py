"""Review schedules: rows copied from the saved model only, in a deterministic order."""

from __future__ import annotations

import random
from typing import Any

import pytest

from app.design.model import entities, schedules, write
from app.design.model.read import read_snapshot
from app.tests.design.model.test_compose import _shuffled
from app.tests.design.model.v2_support import CONFIGS, config_case, v1_snapshot

PE_N = "pe_n_distribution"


def _model(name: str, mutate: Any = None) -> entities.DesignModelV2:
    v1, configuration = config_case(name)
    if mutate is not None:
        mutate(configuration)
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    return model


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_the_terminal_schedule_lists_every_terminal_once(name: str) -> None:
    model = _model(name)
    rows = schedules.terminal_schedule(model)
    templates = {t.ref: t for t in model.templates}
    expected = sum(len(templates[c.template_ref].terminals) for c in model.components)
    expected += 2 * sum(len(s.points) for s in model.strips)
    assert len(rows) == expected
    keys = {(r.owner_id, r.terminal_id, r.side) for r in rows}
    assert len(keys) == len(rows)


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_the_wire_schedule_lists_every_wire_once(name: str) -> None:
    model = _model(name)
    rows = schedules.wire_schedule(model)
    assert sorted(r.wire_id for r in rows) == sorted(w.id for w in model.wires)


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_schedules_do_not_depend_on_input_order(name: str) -> None:
    v1, configuration = config_case(name)
    first = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    second = read_snapshot(
        write.snapshot_v2_from_v1(
            v1_snapshot(CONFIGS[name]["fixture"]), _shuffled(configuration, random.Random(3))
        )
    ).v2
    assert first is not None
    assert second is not None
    assert schedules.terminal_schedule(first) == schedules.terminal_schedule(second)
    assert schedules.wire_schedule(first) == schedules.wire_schedule(second)


def test_terminal_rows_are_ordered_by_board_owner_and_position() -> None:
    rows = schedules.terminal_schedule(_model(PE_N))
    strip = [r for r in rows if r.owner_kind == "strip"]
    assert [(r.position, r.level, r.side) for r in strip] == [
        (p, lvl, side) for p in (1, 2) for lvl in (1, 2) for side in ("inner", "outer")
    ]
    kinds = [r.owner_kind for r in rows]
    assert kinds == sorted(kinds, key=lambda k: k != "component")


def test_terminal_rows_show_net_peers_and_sources_from_the_model() -> None:
    rows = schedules.terminal_schedule(_model(PE_N))
    (row,) = [
        r
        for r in rows
        if r.designation is not None and r.designation.product == "NB" and r.marking == "1"
    ]
    assert row.signal == "SYN-N"
    assert row.connected_to == ("NB:2", "XD:1")
    assert row.template_ref == "tenant/synth-dist-bar-3way/v1"
    assert row.pattern_refs == ("tenant/synth-pe-n-distribution/v1",)
    assert row.required is True
    assert row.net_origin == "derived"
    (open_row,) = [
        r
        for r in rows
        if r.designation is not None and r.designation.product == "NB" and r.marking == "3"
    ]
    assert open_row.net_id is None
    assert open_row.signal is None
    assert open_row.connected_to == ()
    assert open_row.required is False


def test_a_row_is_flagged_by_the_findings_that_touch_it() -> None:
    rows = schedules.terminal_schedule(_model(PE_N))
    feed = [r for r in rows if r.terminal_id == "feed"]
    assert len(feed) == 2
    assert all("terminal_requiredness_unresolved" in r.flags for r in feed)
    assert all("template_unreviewed" in r.flags for r in feed)
    others = [r for r in rows if r.terminal_id != "feed"]
    assert all("terminal_requiredness_unresolved" not in r.flags for r in others)


def test_wire_cells_show_their_source_and_unset_as_unresolved() -> None:
    model = _model(PE_N)
    rows = schedules.wire_schedule(model)
    by_number = {r.number.value: r for r in rows}
    assert by_number["SYN-1"].number.source == "data"
    assert by_number["SYN-2"].number.source == "user"
    (assumed,) = [r for r in rows if r.colour.source == "assumption"]
    assert assumed.colour.value == "SYN-COLOUR-A"
    assert assumed.colour.assumption_id == model.assumptions[0].id
    assert assumed.colour.assumption_statement == model.assumptions[0].statement
    assert "wire_attribute_assumed" in assumed.flags
    for row in rows:
        assert row.cross_section_mm2 == schedules.AttributeCell(None, "unresolved", None, None)
        assert row.pattern_ref == "tenant/synth-pe-n-distribution/v1"
        assert row.start.startswith(("NB:", "PB:"))
        assert row.end.startswith("XD:")


def test_wire_values_are_only_the_models() -> None:
    for name in sorted(CONFIGS):
        model = _model(name)
        wires = {w.id: w for w in model.wires}
        for row in schedules.wire_schedule(model):
            wire = wires[row.wire_id]
            for cell, attribute in (
                (row.number, wire.number),
                (row.cross_section_mm2, wire.cross_section_mm2),
                (row.colour, wire.colour),
            ):
                assert cell.value == (None if attribute.value is None else str(attribute.value))


def test_a_duplicate_wire_number_flags_both_rows() -> None:
    def duplicate(configuration: dict[str, Any]) -> None:
        configuration["bindings"][0]["wires"]["neutral/2"] = {"number": "SYN-1"}

    rows = schedules.wire_schedule(_model(PE_N, duplicate))
    flagged = [r for r in rows if "wire_number_duplicate" in r.flags]
    assert len(flagged) == 2
    assert {r.number.value for r in flagged} == {"SYN-1"}


def test_a_migrated_design_has_no_wire_rows_and_no_invented_terminals() -> None:
    model = read_snapshot(write.snapshot_v2_from_v1(v1_snapshot("residential"))).v2
    assert model is not None
    assert schedules.wire_schedule(model) == ()
    assert schedules.terminal_schedule(model) == ()  # legacy templates have no terminals
