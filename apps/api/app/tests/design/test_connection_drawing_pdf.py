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
    if not across:  # boards share sheets (D-9): every reference reads "this sheet"
        across = [(row, end) for row in drawing.rows for end in row.ends]
        assert across
        row, end = across[0]
        assert (
            f"(this sheet, zone {end.far_place.zone})"
            in connection_drawing.end_texts(end, row.place.sheet)[0]
        )
    for row, end in across:
        if end.far_place.sheet == row.place.sheet:
            continue
        first, _second = connection_drawing.end_texts(end, row.place.sheet)
        assert f"(sheet {end.far_place.sheet}, zone {end.far_place.zone})" in first
        assert re.fullmatch(r"[1-8][A-F]", end.far_place.zone)
    content, _ = _pdf(case)
    text = " ".join(_pages(content))
    row, end = across[0]
    squashed = "".join(text.split())
    assert "".join(connection_drawing.end_texts(end, row.place.sheet)[0].split()) in squashed


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


def _segments(
    points: tuple[tuple[float, float], ...],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return list(itertools.pairwise(points))


def _meet(
    a: tuple[tuple[float, float], tuple[float, float]],
    b: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    """Whether two axis-parallel segments share any point."""
    (ax0, ax1), (ay0, ay1) = sorted((a[0][0], a[1][0])), sorted((a[0][1], a[1][1]))
    (bx0, bx1), (by0, by1) = sorted((b[0][0], b[1][0])), sorted((b[0][1], b[1][1]))
    return ax0 <= bx1 + 0.01 and bx0 <= ax1 + 0.01 and ay0 <= by1 + 0.01 and by0 <= ay1 + 0.01


@pytest.mark.parametrize("case", CASES)
def test_no_lead_crosses_another_or_enters_its_symbol(case: str) -> None:
    """Leads are orthogonal, end at their row's point, stay out of the symbol's box and never meet."""
    model, _ = _model(case)
    drawing = connection_drawing.drawing_set(model)
    seen = 0
    for sheet in drawing.sheets:
        for part in sheet.parts:
            points = {(round(x, 3), round(y, 3)) for x, y in part.points}
            for lead in part.leads:
                seen += 1
                end = lead.points[-1]
                assert (round(end[0], 3), round(end[1], 3)) in points
                for a, b in _segments(lead.points):
                    assert a[0] == b[0] or a[1] == b[1], (lead.terminal, a, b)
                    assert not _interior(lead.box, a, b), (lead.terminal, a, b)
            for one, two in itertools.combinations(part.leads, 2):
                for s1 in _segments(one.points):
                    for s2 in _segments(two.points):
                        assert not _meet(s1, s2), (part.title, one.terminal, two.terminal)
    if case in ("drawing_control_relay", "drawing_heldout_feeder"):
        assert seen >= 4


def test_a_symbol_whose_pins_interleave_its_rows_is_drawn_in_pin_order() -> None:
    """Rows are reordered within the block so leads from opposite edges never cross."""
    model, _ = _model(HELD_OUT_DRAWING)
    drawing = connection_drawing.drawing_set(model)
    parts = [
        p for s in drawing.sheets for p in s.parts if p.symbol is not None and len(p.leads) >= 6
    ]
    assert parts
    for part in parts:
        assert part.symbol is not None
        assert "rows in the symbol's pin order, so no leads cross" in part.notes
        ys = sorted(((lead.points[-1][1], lead.terminal) for lead in part.leads), reverse=True)
        anchors = [a for a in part.symbol.anchors if a.terminal in {t for _y, t in ys}]
        orders = [
            connection_drawing.exit_order(anchors, part.symbol, cut)
            for cut in range(len(anchors) + 1)
        ]
        assert [t for _y, t in ys] in orders


def test_long_text_wraps_in_full_and_a_token_too_wide_is_cut_with_a_reference() -> None:
    cuts = connection_drawing.FullTexts()
    words = "alpha beta gamma delta epsilon " * 6
    lines = connection_drawing.wrap(words, 60, 6, cuts=cuts)
    assert len(lines) > 1
    assert " ".join(lines).split() == words.split()
    assert not cuts.texts
    hyphenated = "SYN-" + "-".join(f"PART{n}" for n in range(20))
    lines = connection_drawing.wrap(hyphenated, 60, 6, cuts=cuts)
    assert "".join(lines) == hyphenated
    token = "X" * 200
    lines = connection_drawing.wrap(f"to {token} here", 60, 6, cuts=cuts)
    assert any(line.endswith("…[1]") for line in lines)
    assert cuts.texts == [f"to {token} here"]
    for line in lines:
        assert connection_drawing.text_width(line, 6) <= 60


@pytest.mark.parametrize("case", CASES)
def test_no_drawing_text_is_below_the_readability_minimum(case: str) -> None:
    content, _ = _pdf(case)
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for char in page.chars:
                grey = char["non_stroking_color"] not in ((0,), (0, 0, 0), [0], [0, 0, 0], 0)
                least = connection_drawing.SECONDARY_PT if grey else connection_drawing.BODY_PT
                assert char["size"] >= least - 1e-6, (char["text"], char["size"])


@pytest.mark.parametrize("case", CASES)
def test_zones_are_read_where_the_point_is_drawn(case: str) -> None:
    model, _ = _model(case)
    drawing = connection_drawing.drawing_set(model)
    for row in drawing.rows:
        place = row.place
        assert place.zone == connection_drawing.zone_at(place.x, place.y)
        part = next(
            p
            for s in drawing.sheets
            if s.number == place.sheet
            for p in s.parts
            if (place.x, place.y) in p.points
        )
        assert part.left <= place.x <= part.right
