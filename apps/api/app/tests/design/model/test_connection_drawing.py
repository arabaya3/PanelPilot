"""The connection drawing layout (M4): the schedules' terminals, nets and wires; every open item shown.

Every configuration under ``model/configurations/`` is laid out, so a
configuration added there as data is checked with no code change
(``drawing_heldout_feeder`` is such a case, added in a commit of its own).
"""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path

import pdfplumber
import pytest

from app.design import connection_drawing_pdf, marking, snapshot
from app.design.model import connection_drawing, entities, findings_v2, schedules, write
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.model.v2_support import CONFIGS, config_case

DESIGN_DIR = Path(connection_drawing_pdf.__file__).parent

#: The configuration added as data only, after the renderer: never named by app code.
HELD_OUT_DRAWING = "drawing_heldout_feeder"


def _model(case: str) -> tuple[entities.DesignModelV2, str]:
    v1, configuration = config_case(case)
    stored = write.snapshot_v2_from_v1(v1, configuration)
    model = read_snapshot(stored).v2
    assert model is not None
    return model, snapshot.sha256_of(stored)


def _pdf(case: str, label: str = marking.DRAFT_LABEL) -> tuple[bytes, str]:
    model, digest = _model(case)
    header = connection_drawing_pdf.DrawingHeader(
        project=f"Synthetic {case}",
        revision="01",
        design_sha256=digest,
        schema="pp-design/2",
        rules_version="rules-under-test",
        renderer_version="renderer-under-test",
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(model)
    ]
    mark = marking.ExportMark(label=label, identity="Synthetic, revision 01")
    drawing = connection_drawing.drawing_set(model)
    return (
        connection_drawing_pdf.render_connection_drawing(drawing, header, mark, structural),
        digest,
    )


def _pages(content: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [" ".join((page.extract_text() or "").split()) for page in pdf.pages]


CASES = sorted(CONFIGS)


def _held_out() -> None:
    # Added as data in a commit of its own; it must stay, so its absence fails.
    assert HELD_OUT_DRAWING in CONFIGS


@pytest.mark.parametrize("case", CASES)
def test_the_drawing_is_the_schedules_terminals_nets_and_wires(case: str) -> None:
    model, _digest = _model(case)
    drawing = connection_drawing.drawing_set(model)
    assert drawing.terminal_rows == schedules.terminal_schedule(model)
    assert drawing.wire_rows == schedules.wire_schedule(model)
    assert [row.terminal for row in drawing.rows] == list(drawing.terminal_rows)
    assert drawing.wire_ids() == {wire.id for wire in model.wires}
    assert drawing.connection_ids() == {c.id for c in model.connections}
    assert drawing.net_ids() == {n.id for n in model.nets} == set(drawing.nets)
    # Every connection is drawn at both of its ends, each naming the other.
    ends = Counter(end.connection_id for row in drawing.rows for end in row.ends)
    assert set(ends.values()) <= {2}
    for row in drawing.rows:
        for end in row.ends:
            assert end.far_place == drawing.places[end.far_key]
            assert drawing.places[row.key] == row.place
    # Every row and every block is on a sheet, numbered after the cover.
    numbers = [sheet.number for sheet in drawing.sheets]
    assert numbers == list(range(connection_drawing.FIRST_SHEET, len(numbers) + 2))
    drawn = {line.row.key for s in drawing.sheets for p in s.parts for line in p.lines}
    assert drawn == drawing.terminal_keys()
    owners = {(p.owner_kind, p.owner_id) for s in drawing.sheets for p in s.parts}
    # Components whose templates define no terminals and no symbol are listed
    # once per board, by name, in one block (M5-0).
    listed = [p for s in drawing.sheets for p in s.parts if p.owner_kind == "no_terminals"]
    owners = {o for o in owners if o[0] != "no_terminals"}
    owners |= {("component", m) for p in listed for m in p.members}
    assert owners == {("component", c.id) for c in model.components} | {
        ("strip", s.id) for s in model.strips
    }
    for sheet in drawing.sheets:
        assert {p.owner_id for p in sheet.parts}
        boards = {p.board_id for p in sheet.parts}
        for p in sheet.parts:
            held = next(
                (c.board_id for c in model.components if c.id == p.owner_id),
                next((s.board_id for s in model.strips if s.id == p.owner_id), p.board_id),
            )
            assert held == p.board_id
            assert f"board {p.board_id}".lower() in p.title.lower()
        assert boards == set(sheet.boards)
        assert sheet.board_id == sheet.boards[0]


@pytest.mark.parametrize("case", CASES)
def test_everything_unresolved_is_shown_and_listed(case: str) -> None:
    model, _digest = _model(case)
    drawing = connection_drawing.drawing_set(model)
    listed = {(item.kind, item.subject) for item in drawing.open_items}
    listed |= {
        ("symbol_not_defined", member)
        for item in drawing.open_items
        if item.kind == "no_terminals_defined"
        for member in item.members
    }
    for row in drawing.rows:
        if row.terminal.net_id is None:
            assert row.notes, row.label
            assert row.notes[0].lower().startswith("unconnected"), row.label
            assert any(subject == row.label for _kind, subject in listed), row.label
    for wire in drawing.wire_rows:
        for cell in (wire.number, wire.cross_section_mm2, wire.colour):
            if cell.value is None:
                assert ("wire_attribute_unset", wire.wire_id) in listed
            elif cell.source == "assumption":
                assert ("wire_attribute_assumed", wire.wire_id) in listed
    groups = connection_drawing._groups(model)
    for net in model.nets:
        if len(groups[net.id]) > 1:
            assert ("unrealised_net", net.id) in listed
            for member in net.members:
                row = next(r for r in drawing.rows if r.key == member.key)
                assert any(note.startswith("UNREALISED") for note in row.notes)
    for template in model.templates:
        if template.symbol is None:
            users = [c for c in model.components if c.template_ref == template.ref]
            for component in users:
                assert ("symbol_not_defined", component.id) in listed


def test_a_wire_attribute_is_printed_with_its_source_or_as_not_set() -> None:
    model, _ = _model("drawing_control_relay")
    drawing = connection_drawing.drawing_set(model)
    texts = {connection_drawing.wire_text(w) for w in drawing.wire_rows}
    joined = " ".join(texts)
    assert "no. SYN-101 [user]" in joined
    assert "mm² 0.75 assumed (" in joined
    assert "colour SYN-COLOUR-B [user]" in joined
    assert "no. not set" in joined


def test_a_block_too_tall_for_a_column_continues_and_says_where() -> None:
    _held_out()
    model, _ = _model(HELD_OUT_DRAWING)
    drawing = connection_drawing.drawing_set(model)
    continued = [p for s in drawing.sheets for p in s.parts if p.part > 1]
    assert continued
    for part in continued:
        assert part.continued_from is not None
        before = next(
            p
            for s in drawing.sheets
            for p in s.parts
            if p.owner_id == part.owner_id and p.part == part.part - 1
        )
        assert before.continues_on == part.place


def test_a_wire_record_missing_is_shown_not_dropped() -> None:
    model, _ = _model("drawing_control_relay")
    wire = model.wires[0]
    model = entities.DesignModelV2(
        **{
            **{f: getattr(model, f) for f in model.__dataclass_fields__},
            "wires": tuple(w for w in model.wires if w.id != wire.id),
        }
    )
    drawing = connection_drawing.drawing_set(model)
    assert any(i.kind == "wire_record_missing" for i in drawing.open_items)
    ends = [e for r in drawing.rows for e in r.ends if e.connection_id == wire.connection_id]
    assert len(ends) == 2
    assert all(e.wire_missing for e in ends)
    assert "WIRE RECORD MISSING" in connection_drawing.end_texts(ends[0], 2)[1]


def test_the_held_out_configuration_is_data_only() -> None:
    """The renderer and layout name no configuration, template, pattern or device."""
    _held_out()
    code = "".join(
        path.read_text(encoding="utf-8")
        for path in (
            DESIGN_DIR / "connection_drawing_pdf.py",
            DESIGN_DIR / "model" / "connection_drawing.py",
            DESIGN_DIR / "model" / "connection_routing.py",
        )
    )
    names: set[str] = set()
    for case, body in CONFIGS.items():
        names.add(case)
        configuration = body["configuration"]
        for item in configuration.get("templates", []) + configuration.get("patterns", []):
            names.add(item["ref"])
            names.add(item["ref"].split("/")[1])
        for item in configuration.get("components", []) + configuration.get("strips", []):
            names.add(item["id"])
            if item.get("designation"):
                names.add(item["designation"]["product"])
        names.add(json.dumps(body["fixture"]))
    for name in sorted(names):
        if len(name) < 3:
            continue
        assert name not in code, name
    for word in ("synth", "relay", "contactor", "isolator", "breaker", "motor", "busbar"):
        assert word not in code.lower(), word


@pytest.mark.parametrize("share", [True, False])
def test_boards_share_sheets_only_when_allowed_and_every_block_names_its_board(share: bool) -> None:
    """D-9 is open: sharing is the default, behind one switch."""
    _held_out()
    model, _ = _model(HELD_OUT_DRAWING)
    drawing = connection_drawing.drawing_set(model, boards_share_sheets=share)
    assert drawing.boards_share_sheets is share
    boards = {c.board_id for c in model.components} | {s.board_id for s in model.strips}
    assert len(boards) > 1
    for sheet in drawing.sheets:
        if not share:
            assert len(sheet.boards) == 1
        for part in sheet.parts:
            assert f"board {part.board_id}".lower() in part.title.lower()
    if share:
        assert any(len(sheet.boards) > 1 for sheet in drawing.sheets)
    assert drawing.terminal_keys() == connection_drawing.drawing_set(model).terminal_keys()


def test_components_with_no_terminals_are_listed_once_per_board_and_each_is_an_open_item() -> None:
    _held_out()
    model, _ = _model(HELD_OUT_DRAWING)
    drawing = connection_drawing.drawing_set(model)
    templates = {t.ref: t for t in model.templates}
    legacy = {
        c.id
        for c in model.components
        if not templates[c.template_ref].terminals and templates[c.template_ref].symbol is None
    }
    assert legacy
    lists = [p for s in drawing.sheets for p in s.parts if p.owner_kind == "no_terminals"]
    assert {m for p in lists if p.part == 1 for m in p.members} == legacy
    grouped = [i for i in drawing.open_items if i.kind == "no_terminals_defined"]
    assert {m for i in grouped for m in i.members} == legacy
    for item in grouped:
        assert f"{len(item.members)} component(s)" in item.text
        for member in item.members:
            assert f"component {member}," in item.text
    assert not [
        i for i in drawing.open_items if i.kind == "symbol_not_defined" and i.subject in legacy
    ]


def test_a_finding_location_is_named_by_board_designation_marking_sheet_and_zone() -> None:
    model, _ = _model("drawing_control_relay")
    drawing = connection_drawing.drawing_set(model)
    row = next(r for r in drawing.rows if r.terminal.owner_kind == "component")
    said = drawing.locate(
        {"component": row.terminal.owner_id, "terminal": row.terminal.terminal_id}
    )
    assert said == (
        f"{row.terminal.board_id}/{row.label} (sheet {row.place.sheet}, zone {row.place.zone})"
    )
    assert drawing.locate({"template": "anything"}) is None


def test_every_strip_point_bridge_gets_one_mark_and_asserts_only_its_via() -> None:
    """One rule for every connection between a point's two sides, whatever its via."""
    _held_out()
    model, _ = _model(HELD_OUT_DRAWING)
    drawing = connection_drawing.drawing_set(model)
    bridges = [(row, end) for row in drawing.rows for end in row.ends if end.bridge]
    assert {end.via for _row, end in bridges} >= {"internal", "jumper"}
    for row, end in bridges:
        assert row.key[0] == "point"
        assert end.far_key[:2] == row.key[:2]
        second = connection_drawing.end_texts(end, row.place.sheet)[1]
        if end.wire is None:
            source = f"per connection pattern {end.pattern_ref}" if end.pattern_ref else "per model"
            assert second == f"bridge between the sides: {end.via} {source}"
    for part in (p for s in drawing.sheets for p in s.parts):
        seen = Counter(
            line.row.ends[line.index].connection_id
            for line in part.lines
            if line.kind == "end" and line.row.ends[line.index].bridge
        )
        assert len(part.bridges) == sum(1 for n in seen.values() if n == 2)
    for part in (p for s in drawing.sheets for p in s.parts):
        assert not [t for t in part.texts if "per template" in t.text]
