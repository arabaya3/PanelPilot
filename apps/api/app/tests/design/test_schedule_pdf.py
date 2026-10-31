"""Review-only terminal and wire schedules: marked on every page, sources and findings shown."""

from __future__ import annotations

import io

import pdfplumber
import pytest

from app.design import marking, schedule_pdf
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
