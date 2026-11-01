"""The review-only connection drawing (M4): generic, from the saved model alone, nothing invented.

Every configuration under ``model/configurations/`` is drawn, so a
configuration added there as data is drawn and checked with no code change
(``drawing_heldout_feeder`` is such a case, added in a commit of its own).
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber
import pytest

from app.design import connection_drawing_pdf, marking, snapshot
from app.design.model import connection_drawing, entities, findings_v2, schedules, write
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.model.v2_support import CONFIG_DIR, CONFIGS, config_case

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
    assert owners == {("component", c.id) for c in model.components} | {
        ("strip", s.id) for s in model.strips
    }
    for sheet in drawing.sheets:
        assert {p.owner_id for p in sheet.parts}
        boards = {
            next(
                (c.board_id for c in model.components if c.id == p.owner_id),
                next((s.board_id for s in model.strips if s.id == p.owner_id), None),
            )
            for p in sheet.parts
        }
        assert boards == {sheet.board_id}


@pytest.mark.parametrize("case", CASES)
def test_everything_unresolved_is_shown_and_listed(case: str) -> None:
    model, _digest = _model(case)
    drawing = connection_drawing.drawing_set(model)
    listed = {(item.kind, item.subject) for item in drawing.open_items}
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


@pytest.mark.parametrize("case", CASES)
def test_every_sheet_is_marked_and_carries_the_snapshot_hash(case: str) -> None:
    content, digest = _pdf(case)
    pages = _pages(content)
    total = len(pages)
    assert total >= 3
    for number, page in enumerate(pages, start=1):
        assert marking.DRAFT_LABEL in page
        assert connection_drawing_pdf.DRAWING_LABEL in page
        assert "REVIEW ONLY — NOT FOR EXECUTION" in page
        assert f"Snapshot SHA-256: {digest}" in page
        assert f"Sheet {number} of {total}" in page
    assert f"Design hash (SHA-256): {digest}" in pages[0]


def test_the_marks_are_drawn_in_the_schedules_red() -> None:
    content, _digest = _pdf("io_marshalling")
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            chars = [c for c in page.chars if c["size"] >= 9.5 and c["top"] < 30]  # the stamps
            words = "".join(c["text"] for c in chars)
            assert "NOTFOREXECUTION" in words.replace(" ", "")
            colours = {tuple(round(v, 3) for v in c["non_stroking_color"]) for c in chars}
            assert colours == {(0.69, 0.0, 0.125)}


@pytest.mark.parametrize("case", CASES)
def test_nothing_is_printed_that_the_model_does_not_hold(case: str) -> None:
    """Every synthetic value printed is one the configuration gives; nothing else is filled in."""
    content, _digest = _pdf(case)
    text = " ".join(_pages(content))
    source = (CONFIG_DIR / f"{case}.json").read_text(encoding="utf-8")
    for token in set(re.findall(r"SYN-[A-Z0-9-]+", text)):
        assert token in source, token
    model, _ = _model(case)
    if model.wires:
        assert connection_drawing.NOT_SET in text
    # No standard or rating vocabulary appears beyond the disclaimer's own words.
    for word in ("IEC", "EN 60", "NFPA", "UL 508", "BS 7671", "compliant", "complies"):
        assert word not in text, word


def test_a_wire_attribute_is_printed_with_its_source_or_as_not_set() -> None:
    model, _ = _model("drawing_control_relay")
    drawing = connection_drawing.drawing_set(model)
    texts = {connection_drawing.wire_text(w) for w in drawing.wire_rows}
    joined = " ".join(texts)
    assert "no. SYN-101 [user]" in joined
    assert "mm² 0.75 assumed (" in joined
    assert "colour SYN-COLOUR-B [user]" in joined
    assert "no. not set" in joined


def test_symbols_come_from_data_and_a_missing_one_is_a_labelled_placeholder() -> None:
    content, _ = _pdf("drawing_control_relay")
    pages = _pages(content)
    drawing_pages = " ".join(pages[1:-1])
    assert connection_drawing.SYMBOL_NOT_DEFINED in drawing_pages
    # The relay's own symbol text ("K") is drawn from its data.
    model, _ = _model("drawing_control_relay")
    relay = model.template("tenant/synth-drw-relay/v1")
    assert relay is not None
    assert relay.symbol is not None
    assert any(shape.text == "K" for shape in relay.symbol.shapes)
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        sheet = pdf.pages[1]
        assert any(w["text"] == "K" for w in sheet.extract_words())
        assert sheet.curves  # the power supply's circle


def _held_out() -> None:
    # Added as data in a commit of its own; it must stay, so its absence fails.
    assert HELD_OUT_DRAWING in CONFIGS


@pytest.mark.parametrize("case", ["motor_dol_with_control", HELD_OUT_DRAWING])
def test_a_cross_reference_names_the_far_sheet_and_zone(case: str) -> None:
    if case == HELD_OUT_DRAWING:
        _held_out()
    model, _ = _model(case)
    drawing = connection_drawing.drawing_set(model)
    across = [
        (row, end)
        for row in drawing.rows
        for end in row.ends
        if end.far_place.sheet != row.place.sheet
    ]
    assert across, "the case spans sheets"
    for row, end in across:
        first, _second = connection_drawing.end_texts(end, row.place.sheet)
        assert f"(sheet {end.far_place.sheet}, zone {end.far_place.zone})" in first
        assert re.fullmatch(r"[1-8][A-F]", end.far_place.zone)
    content, _ = _pdf(case)
    text = " ".join(_pages(content))
    row, end = across[0]
    assert connection_drawing.end_texts(end, row.place.sheet)[0] in text


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
