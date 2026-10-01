"""Tests for `app/design/render_pdf.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from io import BytesIO

import pdfplumber
import pytest

from app.design import render_pdf
from app.design.sheet import SHEET_HEIGHT, SHEET_WIDTH, Anchor, Circle, Line, Rect, Sheet, Text


def test_each_sheet_is_an_a3_landscape_page_with_its_text() -> None:
    first = Sheet(number=1, title="One")
    first.add(
        Line(10, 10, 100, 10, dashed=True),
        Rect(20, 20, 30, 10),
        Circle(50, 50, 2),
        Text(30, 40, "-Q1 C16 1P"),
        Text(100, 40, "middle", anchor=Anchor.MIDDLE, bold=True),
        Text(200, 40, "end", anchor=Anchor.END, rotation=90),
    )
    second = Sheet(number=2, title="Two")
    second.add(Text(30, 40, "page two"))
    data = render_pdf.render_pdf([first, second], title="Pocket", author="Acme")
    with pdfplumber.open(BytesIO(data)) as pdf:
        assert len(pdf.pages) == 2
        page = pdf.pages[0]
        assert page.width == pytest.approx(SHEET_WIDTH / 25.4 * 72, rel=1e-3)
        assert page.height == pytest.approx(SHEET_HEIGHT / 25.4 * 72, rel=1e-3)
        text = page.extract_text()
        assert "-Q1 C16 1P" in text
        assert "page two" in pdf.pages[1].extract_text()
        # y is flipped: a line 10 mm from the top is near the top of the PDF page.
        line = min(page.lines, key=lambda item: item["top"])
        assert line["top"] == pytest.approx(10 / 25.4 * 72, abs=1.0)
        assert pdf.metadata["Title"] == "Pocket"


def test_rendering_is_deterministic() -> None:
    sheet = Sheet(number=1, title="T")
    sheet.add(Text(10, 10, "x"))
    assert render_pdf.render_pdf([sheet]) == render_pdf.render_pdf([sheet])
