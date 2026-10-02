"""Tests for `app/design/rtl.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import arabic_reshaper
import pytest

from app.design import rtl


def test_has_rtl() -> None:
    assert rtl.has_rtl("إنارة")
    assert rtl.has_rtl("שקעים")
    assert not rtl.has_rtl("-Q3 C16 10 kA")
    assert not rtl.has_rtl("")


def _shaped(text: str) -> str:
    return str(arabic_reshaper.reshape(text))


@pytest.mark.parametrize(
    ("typed", "drawn"),
    [
        # Latin alone is left as typed.
        ("Sockets -Q3", "Sockets -Q3"),
        # A right-to-left line reads from the right: its words are reversed.
        ("شقة", None),
    ],
)
def test_visual_leaves_latin_alone(typed: str, drawn: str | None) -> None:
    expected = drawn if drawn is not None else _shaped(typed)[::-1]
    assert rtl.visual(typed) == expected


def test_visual_keeps_latin_and_numbers_in_their_order() -> None:
    # The Arabic word is drawn rightmost, the designation and rating as typed.
    drawn = rtl.visual("قاطع -Q3 C16 10 kA")
    assert drawn == "-Q3 C16 10 kA " + _shaped("قاطع")[::-1]


def test_visual_keeps_a_number_with_its_signs() -> None:
    drawn = rtl.visual("الهبوط 3.5% ضمن")
    assert "3.5%" in drawn
    assert drawn.index(_shaped("ضمن")[::-1]) < drawn.index("3.5%")


def test_visual_mirrors_brackets_in_a_reversed_run() -> None:
    drawn = rtl.visual("ملاحظة (مهمة)")
    assert drawn == "(" + _shaped("مهمة")[::-1] + ") " + _shaped("ملاحظة")[::-1]


def test_visual_in_a_latin_line_reverses_only_the_arabic() -> None:
    drawn = rtl.visual("Pump مضخة 2")
    assert drawn == "Pump " + _shaped("مضخة")[::-1] + " 2"


def test_visual_joins_the_letters() -> None:
    # Presentation forms, not the isolated letters typed.
    assert all(not ("؀" <= char <= "ۿ") for char in rtl.visual("لوحة"))


def test_visual_takes_a_line_right_to_left_when_told() -> None:
    # An Arabic sentence opening with a Latin name: by its first letter it
    # would read left to right, the name first at the left.
    typed = "MDB: لوحة"
    assert rtl.visual(typed) == "MDB: " + _shaped("لوحة")[::-1]
    assert rtl.visual(typed, right_to_left=True) == _shaped("لوحة")[::-1] + " :MDB"
