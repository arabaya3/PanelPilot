"""Tests for `app/design/pdf_fonts.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from app.design import pdf_fonts, rtl


def test_font_for_latin_keeps_the_base_font() -> None:
    assert pdf_fonts.font_for("-Q3 C16 · 10 kA °C") == ("Helvetica", "-Q3 C16 · 10 kA °C")
    assert pdf_fonts.font_for("Total", bold=True) == ("Helvetica-Bold", "Total")


def test_font_for_arabic_embeds_a_font_and_lays_it_out() -> None:
    assert pdf_fonts.font_for("إنارة") == ("DejaVuSans", rtl.visual("إنارة"))
    assert pdf_fonts.font_for("שקעים", bold=True)[0] == "DejaVuSans-Bold"
    # Anything else Helvetica cannot draw, without reordering it.
    assert pdf_fonts.font_for("Δ 5 mm²") == ("DejaVuSans", "Δ 5 mm²")
