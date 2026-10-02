"""Render drawing sheets to a PDF.

The sheets' geometry is drawn as it is, in millimetres; this module only
flips y, since PDF counts from the bottom of the page. Latin text uses the
PDF base fonts, which every viewer has; Arabic, Hebrew and anything else they
lack is drawn in an embedded font, laid out right to left (``pdf_fonts``).
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl
from app.design.sheet import (
    SHEET_HEIGHT,
    SHEET_WIDTH,
    Anchor,
    Circle,
    Line,
    Rect,
    Sheet,
    Text,
)

_DASH = (1.5 * mm, 1.0 * mm)


def _y(value: float) -> float:
    return float((SHEET_HEIGHT - value) * mm)


def render_pdf(sheets: list[Sheet], *, title: str = "", author: str = "") -> bytes:
    """Draw sheets as the pages of one PDF.

    Args:
        sheets: The pages, in order.
        title: The document title the PDF's properties carry.
        author: The author the PDF's properties carry.

    Returns:
        The PDF file's bytes.
    """
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(SHEET_WIDTH * mm, SHEET_HEIGHT * mm), invariant=True)
    canvas.setTitle(title)
    canvas.setAuthor(author)
    canvas.setCreator("PanelPilot")
    for sheet in sheets:
        for item in sheet.items:
            if isinstance(item, Line):
                canvas.setLineWidth(item.width * mm)
                canvas.setDash(*_DASH) if item.dashed else canvas.setDash()
                canvas.line(item.x1 * mm, _y(item.y1), item.x2 * mm, _y(item.y2))
            elif isinstance(item, Rect):
                canvas.setLineWidth(item.width * mm)
                canvas.setDash(*_DASH) if item.dashed else canvas.setDash()
                canvas.rect(item.x * mm, _y(item.y + item.h), item.w * mm, item.h * mm)
            elif isinstance(item, Circle):
                canvas.setLineWidth(item.width * mm)
                canvas.setDash()
                canvas.circle(item.x * mm, _y(item.y), item.r * mm)
            elif isinstance(item, Text):
                font, text = font_for(item.text, bold=item.bold, right_to_left=has_rtl(item.text))
                canvas.setFont(font, item.size * mm / 0.72)
                canvas.saveState()
                canvas.translate(item.x * mm, _y(item.y))
                if item.rotation:
                    canvas.rotate(item.rotation)
                if item.anchor is Anchor.MIDDLE:
                    canvas.drawCentredString(0, 0, text)
                elif item.anchor is Anchor.END:
                    canvas.drawRightString(0, 0, text)
                else:
                    canvas.drawString(0, 0, text)
                canvas.restoreState()
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
