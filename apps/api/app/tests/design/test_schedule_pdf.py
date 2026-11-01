"""Review-only terminal and wire schedules: marked on every page, sources and findings shown."""

from __future__ import annotations

import dataclasses
import io

import pdfplumber
import pytest

from app.design import marking, schedule_pdf
from app.design.model import entities, schedules
from app.design.sheet import FRAME_RIGHT, SHEET_WIDTH
from app.tests.design.test_renderer_version import IDENTITY, v2_exports


def _pages(content: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [" ".join((page.extract_text() or "").split()) for page in pdf.pages]


@pytest.mark.parametrize("case", ["pe_n_distribution", "io_marshalling"])
@pytest.mark.parametrize("fmt", ["terminal_schedule_pdf", "wire_schedule_pdf"])
def test_every_page_is_marked_not_for_execution(case: str, fmt: str) -> None:
    pages = _pages(v2_exports(case)[fmt])
    assert pages
    for page in pages:
        assert marking.REVIEW_APPROVED_LABEL in page
        assert schedule_pdf.SCHEDULE_LABEL in page
        assert "NOT FOR EXECUTION" in page
    first = pages[0]
    assert "Design hash (SHA-256):" in first
    assert "Rules " in first
    assert "renderer " in first
    assert "pp-design/2" in first
    assert IDENTITY.split(",")[0] in first


def test_the_wire_schedule_shows_each_values_source_and_the_assumption() -> None:
    text = " ".join(_pages(v2_exports("pe_n_distribution")["wire_schedule_pdf"]))
    for source in ("[user]", "[data]", "[unresolved]", "[assumption"):
        assert source in text, source
    assert "Synthetic illustrative assumption" in text
    assert "wire_attribute_assumed" in text
    assert "wire_attribute_unset" in text


def test_the_terminal_schedule_shows_unresolved_requiredness_and_lists_all_findings() -> None:
    text = " ".join(_pages(v2_exports("pe_n_distribution")["terminal_schedule_pdf"]))
    assert "[unresolved]" in text
    assert "terminal_requiredness_unresolved" in text
    assert "template_unreviewed" in text
    assert "pattern_unreviewed" in text
    assert "execution blocker" in text
    assert "Structural findings" in text


def test_a_draft_mark_is_printed_as_is() -> None:
    pages = _pages(v2_exports("io_marshalling", marking.DRAFT_LABEL)["wire_schedule_pdf"])
    assert all(marking.DRAFT_LABEL in page for page in pages)
    assert not any(marking.REVIEW_APPROVED_LABEL in page for page in pages)


#: The longest statement and text value the configuration accepts (max_text).
_MAX_TEXT = 2000


def _offpage(content: bytes) -> list[tuple[int, str]]:
    """Every character drawn (partly) outside its page, as (page number, char)."""
    out: list[tuple[int, str]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            for char in page.chars:
                if (
                    char["x0"] < 0
                    or char["x1"] > page.width
                    or char["top"] < 0
                    or char["bottom"] > page.height
                ):
                    out.append((number, char["text"]))
    return out


def _all_text(content: bytes) -> str:
    """The document's text with every whitespace removed (cells wrap anywhere)."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "".join("".join((page.extract_text() or "").split()) for page in pdf.pages)


def _long_wires(model: entities.DesignModelV2) -> entities.DesignModelV2:
    """Every wire attribute an assumption of the maximum length, and long values."""
    statement = " ".join(f"Synthetic-statement-word-{i:04d}" for i in range(80))[:_MAX_TEXT]
    assert len(statement) == _MAX_TEXT
    assumptions = tuple(
        entities.Assumption(id=f"assume-{i:03d}", subject="synthetic", statement=statement)
        for i, _ in enumerate(model.wires)
    )
    wires = tuple(
        dataclasses.replace(
            wire,
            id=wire.id + "-" + "w" * 150,
            number=entities.WireAttribute("N" * 180, "assumption", f"assume-{i:03d}"),
            cross_section_mm2=entities.WireAttribute("9" * 180, "assumption", f"assume-{i:03d}"),
            colour=entities.WireAttribute("C" * 180, "assumption", f"assume-{i:03d}"),
        )
        for i, wire in enumerate(model.wires)
    )
    nets = tuple(dataclasses.replace(net, signal="S" * 200) for net in model.nets)
    return dataclasses.replace(model, wires=wires, assumptions=assumptions, nets=nets)


def test_a_maximum_length_assumption_statement_renders_the_wire_schedule() -> None:
    """A 2,000-character statement renders: an excerpt in the cell, in full after.

    A table row taller than a page cannot be split, so the cell must stay bounded.
    """
    content = v2_exports("pe_n_distribution", v2_edit=_long_wires)["wire_schedule_pdf"]
    pages = _pages(content)
    assert len(pages) > 1
    for page in pages:
        assert "NOT FOR EXECUTION" in page
        assert schedule_pdf.SCHEDULE_LABEL in page
    assert _offpage(content) == []
    text = _all_text(content)
    assert "Assumptions" in text
    statement = " ".join(f"Synthetic-statement-word-{i:04d}" for i in range(80))[:_MAX_TEXT]
    assert "".join(statement.split()) in text
    cell = schedule_pdf._cell(
        schedules.AttributeCell("N", "assumption", "assume-000", statement),
        schedule_pdf._Overflow(),
    )
    assert cell.endswith("… (full statement under Assumptions)]")
    assert len(cell) < schedule_pdf.STATEMENT_EXCERPT + 80


def _long_terminals(model: entities.DesignModelV2) -> entities.DesignModelV2:
    """Long component ids and signals: a terminal schedule's cells grow with them."""
    nets = tuple(dataclasses.replace(net, signal="S" * 200) for net in model.nets)
    return dataclasses.replace(model, nets=nets)


def test_a_long_cell_is_bounded_and_printed_in_full_after_the_tables() -> None:
    overflow = schedule_pdf._Overflow()
    value = "x" * (schedule_pdf.CELL_LIMIT + 500)
    cell = overflow.bounded(value)
    assert len(cell) < schedule_pdf.CELL_LIMIT + 80
    assert overflow.values == [value]
    content = schedule_pdf.render_terminal_schedule(
        [],
        schedule_pdf.ScheduleHeader("p", "01", "0" * 64, "pp-design/2", "r", "v"),
        marking.ExportMark(label=marking.DRAFT_LABEL, identity="draft"),
        [],
    )
    assert _offpage(content) == []
    content = v2_exports("io_marshalling", v2_edit=_long_terminals)["terminal_schedule_pdf"]
    assert _offpage(content) == []
    assert all("NOT FOR EXECUTION" in page for page in _pages(content))


def _conflicting_long_signal(model: entities.DesignModelV2) -> entities.DesignModelV2:
    """Every net on the first net's board carries one 200-character unbroken label."""
    board = model.nets[0].board_id
    nets = tuple(
        dataclasses.replace(net, signal="W" * 200) if net.board_id == board else net
        for net in model.nets
    )
    return dataclasses.replace(model, nets=nets)


def test_a_long_unbroken_signal_stays_inside_the_drawing_findings_sheet() -> None:
    exported = v2_exports("pe_n_distribution", v2_edit=_conflicting_long_signal)
    content = exported["pdf"]
    assert "net_signal_conflict" in _all_text(content)
    assert _offpage(content) == []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            assert "NOT FOR EXECUTION" in (page.extract_text() or "")
            # The sheet is drawn in millimetres onto the page: nothing past the frame.
            frame_right = page.width * FRAME_RIGHT / SHEET_WIDTH
            assert [c["text"] for c in page.chars if c["x1"] > frame_right + 0.5] == []
