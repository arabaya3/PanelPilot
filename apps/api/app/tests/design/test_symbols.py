"""Tests for `app/design/symbols.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.design import symbols
from app.design.sheet import Circle, Line, Rect, Text


@pytest.mark.parametrize(
    "draw", [symbols.circuit_breaker, symbols.residual_current_device, symbols.contactor]
)
def test_a_device_runs_from_its_top_to_its_bottom(draw: object) -> None:
    items, bottom = draw(100.0, 50.0, 3)  # type: ignore[operator]
    assert bottom == 50.0 + symbols.DEVICE_HEIGHT
    ys = [y for i in items if isinstance(i, Line) for y in (i.y1, i.y2)]
    assert min(ys) == pytest.approx(50.0 - 1.0, abs=1.0)
    assert max(ys) == bottom
    assert any(isinstance(i, Text) and i.text == "3" for i in items)


def test_the_rcd_has_its_transformer() -> None:
    items, _ = symbols.residual_current_device(0.0, 0.0)
    assert any(isinstance(i, Rect) for i in items)
    assert any(isinstance(i, Line) and i.dashed for i in items)


def test_the_contactor_carries_its_mark() -> None:
    items, _ = symbols.contactor(0.0, 0.0)
    assert any(isinstance(i, Circle) for i in items)


def test_single_pole_marks_carry_no_number() -> None:
    assert not any(isinstance(i, Text) for i in symbols.pole_marks(0.0, 0.0, 1))


def test_cable_end() -> None:
    items, below = symbols.cable_end(10.0, 20.0, 8.0)
    assert below == 29.0
    assert any(isinstance(i, Circle) for i in items)


def test_busbar_and_labels() -> None:
    (bar,) = symbols.busbar(1.0, 9.0, 5.0)
    assert isinstance(bar, Line)
    assert bar.width > 0.5
    texts = symbols.labels(0.0, 10.0, ["-Q1", "", "C16 1P"])
    assert [t.text for t in texts if isinstance(t, Text)] == ["-Q1", "C16 1P"]
