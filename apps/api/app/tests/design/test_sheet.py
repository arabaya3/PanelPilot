"""Tests for `app/design/sheet.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.design import sheet


def test_columns_divide_the_drawing_area() -> None:
    xs = [sheet.column_x(c) for c in range(sheet.COLUMNS)]
    assert xs == sorted(xs)
    assert sheet.AREA_LEFT < xs[0] < xs[-1] < sheet.AREA_RIGHT
    for column, x in enumerate(xs):
        assert sheet.column_of(x) == column


@pytest.mark.parametrize(("x", "column"), [(-50.0, 0), (10_000.0, 9)])
def test_column_of_clamps_to_the_frame(x: float, column: int) -> None:
    assert sheet.column_of(x) == column


def test_cross_reference_names_page_and_column() -> None:
    assert sheet.cross_reference(4, sheet.column_x(3)) == "/4.3"


def test_sheet_add() -> None:
    page = sheet.Sheet(number=1, title="T")
    page.add(sheet.Line(0, 0, 1, 1), sheet.Text(0, 0, "x"))
    assert len(page.items) == 2
