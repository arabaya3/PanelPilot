"""The calculations report: what a consultant checks a design against.

A4 landscape. For each board, its supply and incomer, then one row per
circuit with every figure the design was checked on: Ib, the protective
device, the cable, its voltage drop, its earth fault loop against the most
its breaker allows, and the let-through energy its cable withstands. Under
each board, the notes it made, in the company's drawing language. It opens
with the methods and standards behind those figures and closes with the
revision's approval, or says plainly that it has none. It is written in the
company's drawing language throughout (``drawing_text``), an Arabic or
Hebrew one with its table read from the right.

Typed text is escaped and drawn in a font that holds it (``pdf_fonts``).
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.design import disconnection
from app.design.drawing_text import Words
from app.design.pdf_fonts import font_for
from app.design.rtl import has_rtl
from app.models.schemas.design import Board, CompanyProfile, DesignProject, Device, DeviceKind

#: The methods each column rests on, as the report states them.
BASIS: tuple[str, ...] = (
    "Design current Ib = P / (k U cos phi); breaker In >= Ib, cable Iz >= In "
    "(IEC 60364-4-43 §433.1; ABB Electrical Installation Handbook Vol. 2, §2.1-2.3).",
    "Current-carrying capacity from IEC 60364-5-52 Annex B, corrected for ambient "
    "temperature and grouping.",
    "Voltage drop from the origin, from Schneider EIG Fig. G28 (copper) and ABB §2.2.2 "
    "(aluminium), against IEC 60364-5-52 Annex G or the company's limits.",
    "Fault level at a sub-board through its feeder, IEC 60909-0 (cmax 1.10); breaking "
    "capacity at or above it.",
    "Earth fault disconnection, IEC 60364-4-41 §411.4.4: Zs x Ia <= 0.95 U0, Ia the top "
    "of the breaker's instantaneous band (IEC 60898-1).",
    "Short circuit along the cable, ABB §2.4: the far-end Ikmin trips the breaker at "
    "once; k²S² from Table 1 for comparison with the breaker's let-through I²t.",
)

#: The circuit table's column headings, in reading order.
COLUMNS: tuple[str, ...] = (
    "Circuit",
    "Ib A",
    "Protection",
    "Cable",
    "L m",
    "dU %",
    "Zs ohm",
    "Zs max",
    "k²S² (kA)²s",
)

#: Every other phrase the report says, as ``Words`` looks it up; a test keeps
#: the Arabic catalogue holding each.
PHRASES: tuple[str, ...] = (
    "Calculations report: {name}",
    "Job number: {value}",
    "Customer: {value}",
    "Consultant: {value}",
    "Revision {index}, {date}",
    "Approved by {name}",
    "NOT APPROVED: issued for review only.",
    "The design rules are this software's defaults; the company has not confirmed them.",
    "Basis of calculation",
    "Board",
    "Notes",
    "{voltage} V, {phases}-phase, {earthing}",
    "Incomer {protection}",
    "fed from {board}",
    "not selected",
    "Prepared",
    "Checked",
    "Approved",
)

_HEAD = colors.HexColor("#e7eef4")


def _paragraph(text: str, style: ParagraphStyle, *, bold: bool = False) -> Paragraph:
    """Text as a paragraph: escaped, in a font that holds it, right-aligned if RTL.

    A line holding Arabic or Hebrew is laid out right to left even where it
    opens with a Latin name, as a note on "Server room" does.
    """
    font, drawn = font_for(
        text, bold=bold or style.fontName.endswith("Bold"), right_to_left=has_rtl(text)
    )
    if has_rtl(text):
        style = style.clone(f"{style.name}-rtl", alignment=TA_RIGHT)
    if font == style.fontName:
        return Paragraph(escape(drawn), style)
    return Paragraph(escape(drawn), style.clone(f"{style.name}-{font}", fontName=font))


def _plain(value: Decimal | None, places: str | None = None) -> str:
    if value is None:
        return "-"
    if places is not None:
        value = value.quantize(Decimal(places))
    return format(value.normalize(), "f")


def _protection(device: Device | None, words: Words) -> str:
    if device is None:
        return "-"
    if device.rated_current_a is None:
        return words("not selected")
    if device.kind is DeviceKind.CIRCUIT_BREAKER and device.curve:
        text = f"{device.curve}{_plain(device.rated_current_a)} {device.poles or ''}P"
    else:
        text = f"{_plain(device.rated_current_a)} A"
    if device.breaking_capacity_ka is not None:
        text += f" {_plain(device.breaking_capacity_ka)} kA"
    return text


def _max_loop(board: Board, device: Device | None) -> Decimal | None:
    """The most Zs the circuit's breaker allows, where it is a miniature breaker."""
    if device is None or device.rated_current_a is None or not device.curve:
        return None
    supply = board.supply
    phase_voltage = (
        supply.voltage_v
        if supply.phases == 1
        else (supply.voltage_v / Decimal(3).sqrt()).quantize(Decimal(1))
    )
    return disconnection.max_loop_ohm(phase_voltage, device.rated_current_a, device.curve)


def _right_to_left(words: Words) -> bool:
    return words.language in ("ar", "he")


def _board_section(board: Board, words: Words, styles: dict[str, ParagraphStyle]) -> list[object]:
    supply = board.supply
    incomer = next((d for d in board.devices if d.id in board.incomer_ids), None)
    story: list[object] = [_paragraph(f"{words('Board')} {board.name}", styles["h2"])]
    facts = [
        words(
            "{voltage} V, {phases}-phase, {earthing}",
            voltage=_plain(supply.voltage_v),
            phases=supply.phases,
            earthing=supply.earthing,
        ),
        f"Ik {_plain(supply.fault_level_ka)} kA",
        f"Ze {_plain(supply.earth_loop_ohm)} ohm",
        words("Incomer {protection}", protection=_protection(incomer, words)),
    ]
    if board.fed_from:
        facts.append(words("fed from {board}", board=board.fed_from))
    story.append(_paragraph(" · ".join(facts), styles["body"]))
    story.append(Spacer(1, 2 * mm))

    rows: list[list[object]] = [[_paragraph(words(column), styles["head"]) for column in COLUMNS]]
    for circuit in board.circuits:
        device = board.device(circuit.device_ids[0]) if circuit.device_ids else None
        cable = board.cable(circuit.cable_id) if circuit.cable_id else None
        cable_text = f"{cable.size} {cable.material} {cable.insulation}" if cable else "-"
        limit = _max_loop(board, device) if circuit.starter is None else None
        rows.append(
            [
                _paragraph(circuit.description, styles["cell"]),
                _plain(circuit.design_current_a, "0.1"),
                _paragraph(_protection(device, words), styles["cell"]),
                cable_text,
                _plain(cable.length_m if cable else None),
                _plain(circuit.voltage_drop_percent),
                _plain(circuit.earth_loop_ohm),
                _plain(limit, "0.001"),
                _plain(cable.withstand_ka2s if cable else None),
            ]
        )
    widths = [62, 16, 34, 40, 14, 16, 18, 18, 24]
    numbers = [1, 4, 5, 6, 7, 8]
    if _right_to_left(words):
        # Read from the right: the circuit's name first, at the right edge.
        rows = [row[::-1] for row in rows]
        widths = widths[::-1]
        numbers = [len(COLUMNS) - 1 - column for column in numbers]
    table = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                ("BACKGROUND", (0, 0), (-1, 0), _HEAD),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                *(("ALIGN", (column, 1), (column, -1), "RIGHT") for column in numbers),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)
    if board.notes:
        story.append(Spacer(1, 2 * mm))
        story.append(_paragraph(words("Notes"), styles["h3"]))
        story.extend(_paragraph(f"- {words.note(made)}", styles["small"]) for made in board.notes)
    story.append(Spacer(1, 6 * mm))
    return story


def render_calculations_pdf(project: DesignProject, company: CompanyProfile) -> bytes:
    """Draw a designed project's calculations report as a PDF.

    Args:
        project: The project, designed and designated.
        company: The company it is issued under, for its name and language.

    Returns:
        The PDF file's bytes.
    """
    words = Words(company.language)
    sample = getSampleStyleSheet()
    styles = {
        "title": sample["Title"],
        "h2": sample["Heading2"],
        "h3": sample["Heading4"],
        "body": sample["BodyText"].clone("body", fontSize=9, leading=12),
        "small": sample["BodyText"].clone("small", fontSize=8, leading=10),
        "cell": sample["BodyText"].clone("cell", fontSize=8, leading=10),
        "head": sample["BodyText"].clone("head", fontName="Helvetica-Bold", fontSize=8, leading=10),
    }
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Calculations - {project.info.name}",
        author=company.name,
        creator="PanelPilot",
        invariant=True,
    )
    info = project.info
    latest = info.revisions[-1] if info.revisions else None
    story: list[object] = [
        _paragraph(company.name, sample["Heading3"]),
        _paragraph(words("Calculations report: {name}", name=info.name), styles["title"]),
    ]
    details = [
        words("Job number: {value}", value=info.number) if info.number else "",
        words("Customer: {value}", value=info.customer) if info.customer else "",
        words("Consultant: {value}", value=info.consultant) if info.consultant else "",
        words("Revision {index}, {date}", index=latest.index, date=latest.date) if latest else "",
    ]
    story.extend(_paragraph(d, styles["body"]) for d in details if d)
    if latest and latest.approved_by:
        approved = words("Approved by {name}", name=latest.approved_by)
        story.append(_paragraph(approved, styles["body"], bold=True))
    else:
        refused = words("NOT APPROVED: issued for review only.")
        story.append(_paragraph(refused, styles["body"], bold=True))
    if not company.rules_confirmed_by:
        story.append(
            _paragraph(
                words(
                    "The design rules are this software's defaults; the company has not "
                    "confirmed them."
                ),
                styles["body"],
            )
        )
    story.append(Spacer(1, 4 * mm))
    story.append(_paragraph(words("Basis of calculation"), styles["h2"]))
    story.extend(_paragraph(f"- {words(line)}", styles["small"]) for line in BASIS)
    story.append(Spacer(1, 6 * mm))
    for board in project.boards:
        story.extend(_board_section(board, words, styles))
    signature = Table(
        [
            [
                _paragraph(words(role), styles["head"])
                for role in ("Prepared", "Checked", "Approved")
            ],
            [
                _paragraph(name, styles["body"])
                for name in (
                    (latest.drawn_by, latest.checked_by, latest.approved_by)
                    if latest
                    else ("", "", "")
                )
            ],
        ],
        colWidths=[60 * mm] * 3,
        rowHeights=[6 * mm, 14 * mm],
    )
    signature.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(KeepTogether([signature]))
    document.build(story)
    return buffer.getvalue()
