"""Which font a PDF draws a text in, and the text as it is drawn.

Latin text keeps the PDF base fonts (Helvetica), which every viewer has and
the file need not embed. A text with a character they lack (Arabic, Hebrew,
Greek, a symbol outside Windows-1252) is drawn in DejaVu Sans, bundled in
``fonts/`` and embedded as a subset, and laid out in visual order
(``rtl``), since a PDF draws glyphs left to right as given.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.design import rtl

_FONTS = Path(__file__).parent / "fonts"

LATIN = "Helvetica"
LATIN_BOLD = "Helvetica-Bold"
UNICODE = "DejaVuSans"
UNICODE_BOLD = "DejaVuSans-Bold"


@cache
def _registered() -> bool:
    """Register the bundled fonts with ReportLab, once per process."""
    for name in (UNICODE, UNICODE_BOLD):
        pdfmetrics.registerFont(TTFont(name, str(_FONTS / f"{name}.ttf")))
    return True


def _latin(text: str) -> bool:
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def font_for(text: str, *, bold: bool = False) -> tuple[str, str]:
    """Pick the font for a text and lay it out for drawing.

    Args:
        text: The text in logical (typed) order.
        bold: Whether the bold face is wanted.

    Returns:
        The font name to set, and the text to draw with it.
    """
    if _latin(text):
        return (LATIN_BOLD if bold else LATIN), text
    _registered()
    return (UNICODE_BOLD if bold else UNICODE), rtl.visual(text)
