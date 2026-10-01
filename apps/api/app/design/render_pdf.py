"""Render drawing sheets to a PDF.

The sheets' geometry is drawn as it is, in millimetres; this module only
flips y, since PDF counts from the bottom of the page. Text uses the PDF base
fonts, which every viewer has, so the file needs no embedded font to open.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

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

_FONT = "Helvetica"
_BOLD = "Helvetica-Bold"
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
                canvas.setFont(_BOLD if item.bold else _FONT, item.size * mm / 0.72)
                canvas.saveState()
                canvas.translate(item.x * mm, _y(item.y))
                if item.rotation:
                    canvas.rotate(item.rotation)
                if item.anchor is Anchor.MIDDLE:
                    canvas.drawCentredString(0, 0, item.text)
                elif item.anchor is Anchor.END:
                    canvas.drawRightString(0, 0, item.text)
                else:
                    canvas.drawString(0, 0, item.text)
                canvas.restoreState()
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
