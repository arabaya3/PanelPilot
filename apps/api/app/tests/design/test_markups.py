"""Tests for `app/design/markups.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import io

import pytest
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.core.errors import ValidationError
from app.design import markups
from app.design.sheet import SHEET_HEIGHT, SHEET_WIDTH, Sheet, Text


def _sheets() -> list[Sheet]:
    first = Sheet(number=1, title="Main power", board="MDB")
    first.add(Text(100, 100, "-Q3"), Text(250, 100, "-Q4"), Text(100, 120, "x"))
    second = Sheet(number=2, title="Parts list", board="")
    return [first, second]


def _pdf(notes: list[tuple[int, float, float, str]], pages: int = 2) -> bytes:
    """A PDF of drawing-sized pages with a note at (x, y) mm on a page."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(SHEET_WIDTH * mm, SHEET_HEIGHT * mm))
    for page in range(1, pages + 1):
        for on, x, y, text in notes:
            if on == page:
                top = (SHEET_HEIGHT - y) * mm
                canvas.textAnnotation(text, Rect=(x * mm, top - 5, x * mm + 10, top + 5))
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_read_markups_places_each_mark_on_its_board_and_label() -> None:
    data = _pdf([(1, 104, 98, "Make this C20"), (1, 245, 103, "أضف مخرجاً")])
    found, matched = markups.read_markups(data, _sheets())
    assert matched
    assert [(m.page, m.text, m.board, m.near) for m in found] == [
        (1, "Make this C20", "MDB", "-Q3"),
        (1, "أضف مخرجاً", "MDB", "-Q4"),
    ]
    assert found[0].kind == "Text"
    assert found[0].sheet == "Main power"


def test_a_pdf_that_is_not_this_drawing_set_keeps_its_marks_unplaced() -> None:
    found, matched = markups.read_markups(_pdf([(1, 104, 98, "note")], pages=3), _sheets())
    assert not matched
    assert (found[0].board, found[0].near) == ("", "")


def test_marks_without_text_are_left_out() -> None:
    found, _ = markups.read_markups(_pdf([(1, 104, 98, "  ")]), None)
    assert found == []


def test_a_file_that_is_not_a_pdf_is_refused() -> None:
    with pytest.raises(ValidationError) as refused:
        markups.read_markups(b"not a pdf")
    assert refused.value.code == "markups_unreadable"


def test_too_many_pages_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markups, "MAX_PAGES", 1)
    with pytest.raises(ValidationError) as refused:
        markups.read_markups(_pdf([], pages=2))
    assert refused.value.code == "markups_too_long"
