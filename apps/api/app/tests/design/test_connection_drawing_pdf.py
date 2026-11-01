"""The connection drawing PDF (M4): every sheet marked, hashed and numbered; symbols from data; nothing invented."""

from __future__ import annotations

import io
import itertools
import re
from pathlib import Path

import pdfplumber
import pytest

from app.design import connection_drawing_pdf, marking, snapshot
from app.design.model import connection_drawing, entities, findings_v2, write
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


def _held_out() -> None:
    # Added as data in a commit of its own; it must stay, so its absence fails.
    assert HELD_OUT_DRAWING in CONFIGS


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


def _interior(
    box: tuple[float, float, float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """Whether an axis-parallel segment passes through a box's open interior."""
    x0, y0, x1, y1 = box
    if a[0] == b[0]:
        low, high = sorted((a[1], b[1]))
        return x0 < a[0] < x1 and low < y1 and high > y0
    low, high = sorted((a[0], b[0]))
    return y0 < a[1] < y1 and low < x1 and high > x0


@pytest.mark.parametrize("case", CASES)
def test_no_lead_crosses_its_symbol(case: str) -> None:
    """Leads are orthogonal and stay out of their symbol's box (S1)."""
    model, _ = _model(case)
    drawing = connection_drawing.drawing_set(model)
    seen = 0
    for sheet in drawing.sheets:
        for part in sheet.parts:
            if part.symbol is None or part.owner_kind != "component":
                continue
            left = connection_drawing_pdf._column_x(part.place.column)
            top = connection_drawing_pdf._slot_y(part.place.slot) - connection_drawing_pdf._SLOT
            lines = connection_drawing_pdf._first_lines(part, top)
            bottom = top - max(len(part.lines), 1) * connection_drawing_pdf._SLOT
            rows_y = {line.row.terminal.terminal_id: y for line, y in lines}
            point_x = left + connection_drawing_pdf._POINT
            for lead in connection_drawing_pdf.leads(
                part.symbol, left, top, bottom, rows_y, point_x
            ):
                seen += 1
                for a, b in itertools.pairwise(lead.points):
                    assert a[0] == b[0] or a[1] == b[1], (lead.terminal, a, b)
                    assert not _interior(lead.box, a, b), (lead.terminal, a, b)
                assert lead.points[-1] == (point_x, rows_y[lead.terminal])
    if case in ("drawing_control_relay", "drawing_heldout_feeder"):
        assert seen >= 4


def test_text_too_long_for_its_room_is_cut_with_an_ellipsis() -> None:
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    connection_drawing_pdf._text(canvas, 0, 0, "x" * 400, 5, room=20)
    canvas.save()
    with pdfplumber.open(io.BytesIO(buffer.getvalue())) as pdf:
        text = pdf.pages[0].extract_text() or ""
    assert text.endswith("…")
    assert len(text) < 400
