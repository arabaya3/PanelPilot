"""Render a quotation as a one- or two-page PDF.

A4 portrait, a table of lines and the totals under it. A quotation with
unpriced lines says so above the totals, in the same weight as the totals,
so it cannot be sent as though it were complete.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.schemas.design import DesignProject, Quotation


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return (
        f"{value:,.2f}" if value != value.to_integral_value() or "." in str(value) else f"{value:,}"
    )


def _amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_quotation_pdf(
    quotation: Quotation, project: DesignProject, *, company: str = ""
) -> bytes:
    """Draw a quotation as a PDF.

    Args:
        quotation: The priced quotation.
        project: The project it prices, for the heading.
        company: The issuing company's name.

    Returns:
        The PDF file's bytes.
    """
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Quotation - {project.info.name}",
        author=company,
        creator="PanelPilot",
        invariant=True,
    )
    story: list[object] = []
    if company:
        story.append(Paragraph(company, styles["Heading2"]))
    story.append(Paragraph(f"Quotation: {project.info.name}", styles["Title"]))
    details = [
        f"Job number: {project.info.number}" if project.info.number else "",
        f"Customer: {project.info.customer}" if project.info.customer else "",
        "Boards: " + ", ".join(board.name for board in project.boards),
        f"Currency: {quotation.currency}",
    ]
    for detail in details:
        if detail:
            story.append(Paragraph(detail, styles["BodyText"]))
    story.append(Spacer(1, 6 * mm))

    rows: list[list[object]] = [["Description", "Qty", "Unit", "Unit price", "Total"]]
    for line in quotation.lines:
        description = line.description
        if line.designations:
            description += f"<br/><font size=7>{' '.join(line.designations)}</font>"
        rows.append(
            [
                Paragraph(description, small),
                _plain(line.quantity),
                line.unit,
                _amount(line.unit_price) if line.unit_price is not None else "not priced",
                _amount(line.total) if line.total is not None else "-",
            ]
        )
    table = Table(rows, colWidths=[95 * mm, 15 * mm, 12 * mm, 28 * mm, 30 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7eef4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    if not quotation.complete:
        story.append(
            Paragraph(
                "<b>Incomplete: these lines have no price and are not in the total:</b> "
                + "; ".join(quotation.unpriced),
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 3 * mm))
    totals = Table(
        [
            ["Materials", _amount(quotation.materials)],
            ["Labour and enclosure", _amount(quotation.labour)],
            ["Markup", _amount(quotation.markup)],
            ["VAT", _amount(quotation.vat)],
            [f"Total ({quotation.currency})", _amount(quotation.total)],
        ],
        colWidths=[50 * mm, 30 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
            ]
        )
    )
    story.append(totals)
    document.build(story)
    return buffer.getvalue()
