"""Lay a design project out as a drawing set.

The pages a company issues, and their order, come from its profile; this
module decides what goes where on each. Pages are planned first and drawn
second, because a main page's cross-reference to a distribution page needs
that page's number, and the contents and every title block need the total.

What the set holds:

* a title page, with the revisions and whether the design rules were
  confirmed;
* contents;
* per board, single-line pages: the supply, the incomer and busbar, and each
  feeder (a residual current group, or a circuit straight off the busbar),
  nine to a page;
* per board, distribution pages: every outgoing circuit with its breaker,
  phase and cable, ten to a page, each group's circuits under their
  residual current device;
* per board, the design notes;
* the cable list and the parts list.

A page kind the profile names but the project has nothing for (terminals,
layout, safety text) is left out rather than printed empty.

The set's own words and the notes are in the profile's drawing language
(``drawing_text``); what the engineer typed is drawn as typed.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from decimal import Decimal

from app.design import rtl, symbols, terminals
from app.design.drawing_text import Words
from app.design.sheet import (
    AREA_BOTTOM,
    AREA_LEFT,
    AREA_RIGHT,
    AREA_TOP,
    COLUMNS,
    FRAME_BOTTOM,
    FRAME_LEFT,
    FRAME_RIGHT,
    FRAME_TOP,
    INDEX_STRIP,
    ROWS,
    TITLE_BLOCK_HEIGHT,
    Anchor,
    Item,
    Line,
    Rect,
    Sheet,
    Text,
    column_x,
)
from app.models.schemas.design import (
    Board,
    Circuit,
    CompanyProfile,
    DesignProject,
    Device,
    DeviceKind,
    MotorStarter,
    PageKind,
    TitleField,
)

FEEDERS_PER_PAGE = COLUMNS - 1
CIRCUITS_PER_PAGE = COLUMNS
ROWS_PER_TABLE_PAGE = 38
CONTENTS_ROWS_PER_PAGE = 38

#: Average character width as a share of text height, for the base font:
#: what a cell can hold before its text is wrapped.
_CHAR_WIDTH = 0.75

#: The same for a text drawn in the embedded font (``pdf_fonts``), whose
#: Latin and Arabic glyphs run wider than the base font's.
_RTL_CHAR_WIDTH = 0.95

_TITLE_LABELS: dict[TitleField, str] = {
    TitleField.COMPANY: "Company",
    TitleField.PROJECT_NAME: "Project",
    TitleField.PROJECT_NUMBER: "Job number",
    TitleField.BOARD_NAME: "Board",
    TitleField.CUSTOMER: "Customer",
    TitleField.CONSULTANT: "Consultant",
    TitleField.CONTRACTOR: "Contractor",
    TitleField.PAGE_TITLE: "Title of page",
    TitleField.PAGE_NUMBER: "Page",
    TitleField.REVISION: "Rev.",
    TitleField.DATE: "Date",
    TitleField.DRAWN_BY: "Drawn",
    TitleField.CHECKED_BY: "Checked",
    TitleField.APPROVED_BY: "Approved",
}

_KIND_NAMES: dict[DeviceKind, str] = {
    DeviceKind.CIRCUIT_BREAKER: "Circuit-breaker",
    DeviceKind.RESIDUAL_CURRENT_DEVICE: "Residual current circuit-breaker",
    DeviceKind.SWITCH_DISCONNECTOR: "Switch-disconnector",
    DeviceKind.CONTACTOR: "Contactor",
    DeviceKind.OVERLOAD_RELAY: "Overload relay",
    DeviceKind.FUSE: "Fuse",
    DeviceKind.SURGE_PROTECTOR: "Surge protective device",
    DeviceKind.DRIVE: "Variable speed drive",
    DeviceKind.MOTOR: "Motor",
    DeviceKind.RELAY: "Relay",
    DeviceKind.BUS_ACTUATOR: "Bus actuator",
    DeviceKind.POWER_SUPPLY: "Power supply",
    DeviceKind.METER: "Meter",
    DeviceKind.INDICATOR_LAMP: "Indicator lamp",
    DeviceKind.TERMINAL_STRIP: "Terminal strip",
    DeviceKind.CABLE: "Cable",
    DeviceKind.BUSBAR: "Busbar",
    DeviceKind.OTHER: "Device",
}


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _product(device: Device) -> str:
    return f"-{device.designation.product}" if device.designation else f"({device.id})"


def rating_text(device: Device) -> str:
    """Describe a device's rating the way a single-line diagram labels it.

    Args:
        device: The device.

    Returns:
        "C16 1P", "40 A 30 mA 4P", "MCCB, not selected", and so on.
    """
    poles = f"{device.poles}P" if device.poles else ""
    article = device.part_key.split("/", 1)[-1] if device.part_key else None
    if device.kind is DeviceKind.FUSE:
        parts = [f"{_plain(device.rated_current_a)} A {device.curve or ''}".strip(), poles]
    elif device.kind is DeviceKind.DRIVE:
        parts = [article or "drive, not selected"]
    elif device.kind is DeviceKind.OVERLOAD_RELAY:
        parts = [article or "", f"set {_plain(device.rated_current_a)} A"]
    elif device.kind is DeviceKind.CONTACTOR and article and device.rated_current_a is None:
        parts = [article, poles]
    elif device.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE:
        parts = [
            f"{_plain(device.rated_current_a)} A" if device.rated_current_a else "",
            f"{_plain(device.residual_current_ma)} mA" if device.residual_current_ma else "",
            poles,
        ]
    elif device.rated_current_a is None:
        parts = ["rating not selected", poles]
    elif device.kind is DeviceKind.CONTACTOR:
        parts = [f"{_plain(device.rated_current_a)} A AC-1", poles]
    elif device.kind is DeviceKind.SWITCH_DISCONNECTOR:
        parts = [f"{_plain(device.rated_current_a)} A", poles]
    else:
        parts = [f"{device.curve or ''}{_plain(device.rated_current_a)}", poles]
    if device.breaking_capacity_ka:
        parts.append(f"{_plain(device.breaking_capacity_ka)} kA")
    return " ".join(p for p in parts if p)


def cable_text(board: Board, circuit: Circuit) -> str:
    """Describe a circuit's cable: cores, section, metal, insulation.

    Args:
        board: The board holding the cable.
        circuit: The circuit.

    Returns:
        "3G2.5 Cu PVC", or "" for a circuit with no cable.
    """
    if circuit.cable_id is None:
        return ""
    cable = board.cable(circuit.cable_id)
    return f"{cable.cores}G{_plain(cable.cross_section_mm2)} {cable.material} {cable.insulation}"


@dataclass
class _Plan:
    kind: PageKind
    title: str
    board: Board | None = None
    payload: list[object] = field(default_factory=list)
    group: str | None = None


def _feeders(board: Board) -> list[str]:
    """What hangs off the busbar, in drawing order: group breakers and direct circuits."""
    feeders: list[str] = []
    for circuit in board.circuits:
        if circuit.upstream_id is None:
            feeders.append(f"circuit:{circuit.id}")
            continue
        rcd = board.device(circuit.upstream_id)
        key = f"group:{rcd.id}"
        if key not in feeders:
            feeders.append(key)
    return feeders


def _groups(board: Board) -> list[tuple[str | None, list[Circuit]]]:
    """Circuits per residual current device, in order; ``None`` for the busbar."""
    groups: list[tuple[str | None, list[Circuit]]] = []
    for circuit in board.circuits:
        if groups and groups[-1][0] == circuit.upstream_id:
            groups[-1][1].append(circuit)
        else:
            groups.append((circuit.upstream_id, [circuit]))
    return groups


def _chunks(items: list[object], size: int) -> list[list[object]]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def _part_rows(project: DesignProject, say: Words) -> list[list[str]]:
    """The parts list: identical devices counted together."""
    groups: dict[tuple[object, ...], tuple[Device, list[str]]] = {}
    for board in project.boards:
        prefix = f"{board.name} " if len(project.boards) > 1 else ""
        for device in board.devices:
            key: tuple[object, ...] = (
                device.kind,
                device.poles,
                device.rated_current_a,
                device.curve,
                device.residual_current_ma,
                device.breaking_capacity_ka,
                device.part_key,
            )
            groups.setdefault(key, (device, []))[1].append(f"{prefix}{_product(device)}")
    table: list[list[str]] = []
    for sample, names in groups.values():
        if sample.part_key:
            part = project.part(sample.part_key)
            article = f"{part.manufacturer} {part.type_number}"
            order_number = part.order_number or ""
        else:
            article, order_number = say("not selected"), ""
        table.append(
            [
                str(len(names)),
                f"{say(_KIND_NAMES[sample.kind])} {rating_text(sample)}",
                article,
                order_number,
                ", ".join(names),
            ]
        )
    # Terminals, counted by article across the project.
    counts: dict[str, int] = {}
    places: dict[str, list[str]] = {}
    pe_articles: set[str] = set()
    for board in project.boards:
        where = f"{board.name} -{terminals.STRIP}"
        for terminal in terminals.strip(board):
            if terminal.article is None:
                continue
            counts[terminal.article] = counts.get(terminal.article, 0) + 1
            if where not in places.setdefault(terminal.article, []):
                places[terminal.article].append(where)
            if terminal.function == "PE":
                pe_articles.add(terminal.article)
    for article, count in counts.items():
        kind = say("PE terminal") if article in pe_articles else say("Terminal")
        table.append(
            [str(count), f"{kind} 8WH1", f"Siemens {article}", article, ", ".join(places[article])]
        )
    return table


def _terminal_rows(board: Board, say: Words) -> list[list[str]]:
    """The board's terminal strip, one row per terminal."""
    return [
        [
            f"-{terminals.STRIP}:{terminal.number}",
            terminal.function,
            terminal.circuit,
            terminal.cable,
            _plain(terminal.conductor_mm2),
            terminal.article or say("not selected"),
        ]
        for terminal in terminals.strip(board)
    ]


def _cable_rows(project: DesignProject) -> list[list[str]]:
    table: list[list[str]] = []
    for board in project.boards:
        for circuit in board.circuits:
            if circuit.cable_id is None:
                continue
            cable = board.cable(circuit.cable_id)
            source = board.device(circuit.device_ids[0]) if circuit.device_ids else None
            table.append(
                [
                    f"{board.name} -{cable.designation.product}" if cable.designation else cable.id,
                    f"{board.name} {_product(source)}" if source else board.name,
                    circuit.description,
                    cable_text(board, circuit),
                    f"{_plain(cable.length_m)} m" if cable.length_m else "",
                ]
            )
    return table


def _plan(project: DesignProject, profile: CompanyProfile, say: Words) -> list[_Plan]:
    plans: list[_Plan] = []
    for kind in profile.page_order:
        if kind is PageKind.TITLE:
            plans.append(_Plan(kind, say("Title page")))
        elif kind is PageKind.CONTENTS:
            plans.append(_Plan(kind, say("Table of contents")))
        elif kind is PageKind.SINGLE_LINE:
            for board in project.boards:
                for chunk in _chunks(list(_feeders(board)), FEEDERS_PER_PAGE):
                    plans.append(_Plan(kind, say("Main power"), board, chunk))
        elif kind is PageKind.DISTRIBUTION:
            for board in project.boards:
                for group, circuits in _groups(board):
                    for chunk in _chunks(list(circuits), CIRCUITS_PER_PAGE):
                        plans.append(_Plan(kind, say("Distribution loads"), board, chunk, group))
        elif kind is PageKind.NOTES:
            for board in project.boards:
                if board.notes:
                    plans.append(
                        _Plan(kind, say("Design notes"), board, [say.note(n) for n in board.notes])
                    )
        elif kind is PageKind.TERMINALS:
            for board in project.boards:
                strip_rows = _terminal_rows(board, say)
                for chunk in _chunks(list(strip_rows), ROWS_PER_TABLE_PAGE):
                    plans.append(_Plan(kind, say("Terminals"), board, chunk))
        elif kind is PageKind.CABLES:
            rows = _cable_rows(project)
            if rows:
                for chunk in _chunks(list(rows), ROWS_PER_TABLE_PAGE):
                    plans.append(_Plan(kind, say("Cable list"), None, chunk))
        elif kind is PageKind.PARTS:
            rows = _part_rows(project, say)
            if rows:
                for chunk in _chunks(list(rows), ROWS_PER_TABLE_PAGE):
                    plans.append(_Plan(kind, say("Parts list"), None, chunk))
    # Contents may need more than one page once the set is long.
    expanded: list[_Plan] = []
    for plan in plans:
        if plan.kind is PageKind.CONTENTS:
            pages = max(1, -(-len(plans) // CONTENTS_ROWS_PER_PAGE))
            expanded.extend(_Plan(plan.kind, plan.title) for _ in range(pages))
        else:
            expanded.append(plan)
    return expanded


def _frame(
    sheet: Sheet, total: int, project: DesignProject, profile: CompanyProfile, say: Words
) -> None:
    """Draw the frame, the column and row indexes, and the title block."""
    title_top = FRAME_BOTTOM - TITLE_BLOCK_HEIGHT
    sheet.add(Rect(FRAME_LEFT, FRAME_TOP, FRAME_RIGHT - FRAME_LEFT, FRAME_BOTTOM - FRAME_TOP, 0.5))
    # Column indexes along the top and above the title block, row letters
    # down both sides: how a cross-reference finds its point.
    width = (AREA_RIGHT - AREA_LEFT) / COLUMNS
    for column in range(COLUMNS):
        x = column_x(column)
        sheet.add(
            Text(x, FRAME_TOP + 3.6, str(column), size=2.5, anchor=Anchor.MIDDLE),
            Text(x, title_top - 1.4, str(column), size=2.5, anchor=Anchor.MIDDLE),
        )
        if column:
            left = AREA_LEFT + width * column
            sheet.add(
                Line(left, FRAME_TOP, left, FRAME_TOP + INDEX_STRIP, 0.18),
                Line(left, title_top - INDEX_STRIP, left, title_top, 0.18),
            )
    sheet.add(
        Line(FRAME_LEFT, AREA_TOP, FRAME_RIGHT, AREA_TOP, 0.18),
        Line(FRAME_LEFT, title_top - INDEX_STRIP, FRAME_RIGHT, title_top - INDEX_STRIP, 0.18),
        Line(AREA_LEFT, AREA_TOP, AREA_LEFT, title_top - INDEX_STRIP, 0.18),
        Line(AREA_RIGHT, AREA_TOP, AREA_RIGHT, title_top - INDEX_STRIP, 0.18),
    )
    height = (AREA_BOTTOM - AREA_TOP) / len(ROWS)
    for index, row in enumerate(ROWS):
        y = AREA_TOP + height * (index + 0.5) + 1.0
        sheet.add(
            Text(FRAME_LEFT + INDEX_STRIP / 2, y, row, size=2.5, anchor=Anchor.MIDDLE),
            Text(FRAME_RIGHT - INDEX_STRIP / 2, y, row, size=2.5, anchor=Anchor.MIDDLE),
        )

    latest = project.info.revisions[-1] if project.info.revisions else None
    values: dict[TitleField, str] = {
        TitleField.COMPANY: profile.name,
        TitleField.PROJECT_NAME: project.info.name,
        TitleField.PROJECT_NUMBER: project.info.number,
        TitleField.BOARD_NAME: sheet.board,
        TitleField.CUSTOMER: project.info.customer,
        TitleField.CONSULTANT: project.info.consultant,
        TitleField.CONTRACTOR: project.info.contractor,
        TitleField.PAGE_TITLE: sheet.title,
        TitleField.PAGE_NUMBER: say("{number} of {total}", number=sheet.number, total=total),
        TitleField.REVISION: latest.index if latest else "",
        TitleField.DATE: latest.date if latest else "",
        TitleField.DRAWN_BY: latest.drawn_by if latest else "",
        TitleField.CHECKED_BY: latest.checked_by if latest else "",
        TitleField.APPROVED_BY: latest.approved_by if latest else "",
    }
    fields = profile.title_fields
    per_row = max(1, -(-len(fields) // 2))
    cell_width = (FRAME_RIGHT - FRAME_LEFT) / per_row
    cell_height = TITLE_BLOCK_HEIGHT / 2
    sheet.add(Line(FRAME_LEFT, title_top, FRAME_RIGHT, title_top, 0.5))
    sheet.add(Line(FRAME_LEFT, title_top + cell_height, FRAME_RIGHT, title_top + cell_height))
    for index, name in enumerate(fields):
        line_index, column = divmod(index, per_row)
        x = FRAME_LEFT + column * cell_width
        y = title_top + line_index * cell_height
        if column:
            sheet.add(Line(x, y, x, y + cell_height))
        value = _fit(values[name], cell_width - 3.0, 3.0)
        sheet.add(
            Text(x + 1.5, y + 4.0, say(_TITLE_LABELS[name]), size=1.8),
            Text(x + 1.5, y + 11.0, value, size=3.0, bold=name is TitleField.PAGE_TITLE),
        )


def _per_line(text: str, width: float, size: float) -> int:
    """How many of a text's characters fit a width, by its script's average."""
    factor = _RTL_CHAR_WIDTH if rtl.has_rtl(text) else _CHAR_WIDTH
    return max(1, int(width / (size * factor)))


def _fit(text: str, width: float, size: float) -> str:
    """Cut a text to what fits a width, marking the cut."""
    limit = _per_line(text, width, size)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cell(
    x: float, width: float, y: float, text: str, size: float, *, right: bool, bold: bool = False
) -> Text:
    """One line of a table cell, right-aligned for a cell that reads right to left."""
    if right:
        return Text(x + width - 1.5, y, text, size=size, bold=bold, anchor=Anchor.END)
    return Text(x + 1.5, y, text, size=size, bold=bold)


def _table(
    sheet: Sheet,
    top: float,
    headers: list[tuple[str, float]],
    rows: list[list[str]],
    size: float = 2.4,
) -> None:
    """Draw a ruled table, wrapping long cells onto further lines."""
    x0 = AREA_LEFT + 4.0
    widths = [w for _, w in headers]
    line_height = size * 1.45
    y = top
    sheet.add(Line(x0, y, x0 + sum(widths), y, 0.35))
    if any(header for header, _ in headers):
        x = x0
        for header, width in headers:
            right = rtl.has_rtl(header)
            sheet.add(_cell(x, width, y + line_height, header, size, right=right, bold=True))
            x += width
        y += line_height + 1.5
        sheet.add(Line(x0, y, x0 + sum(widths), y, 0.35))
    for row in rows:
        wrapped = [
            textwrap.wrap(cell, _per_line(cell, width - 3.0, size)) or [""]
            for cell, width in zip(row, widths, strict=True)
        ]
        height = max(len(w) for w in wrapped) * line_height + 1.5
        x = x0
        for cell, lines, width in zip(row, wrapped, widths, strict=True):
            right = rtl.has_rtl(cell)
            for n, text in enumerate(lines):
                sheet.add(_cell(x, width, y + line_height * (n + 1), text, size, right=right))
            x += width
        y += height
        sheet.add(Line(x0, y, x0 + sum(widths), y, 0.18))
        if y > AREA_BOTTOM - 4:
            break
    x = x0
    for width in [0.0, *widths]:
        x += width
        sheet.add(Line(x, top, x, y, 0.18))


def _draw_title(sheet: Sheet, project: DesignProject, profile: CompanyProfile, say: Words) -> None:
    info = project.info
    middle = (AREA_LEFT + AREA_RIGHT) / 2
    sheet.add(
        Text(middle, AREA_TOP + 45, info.name, size=9.0, anchor=Anchor.MIDDLE, bold=True),
        Text(
            middle,
            AREA_TOP + 58,
            ", ".join(b.name for b in project.boards),
            size=5.0,
            anchor=Anchor.MIDDLE,
        ),
    )
    rows = [
        [say("Customer"), info.customer],
        [say("Consultant"), info.consultant],
        [say("Contractor"), info.contractor],
        [say("Job number"), info.number],
        [say("Issued under"), profile.name or profile.key],
        [
            say("Design rules"),
            (
                say("confirmed by {name}", name=profile.rules_confirmed_by)
                if profile.rules_confirmed_by
                else say("software defaults, not confirmed by the company's engineers")
            ),
        ],
    ]
    _table(sheet, AREA_TOP + 75, [("", 50.0), ("", 150.0)], [r for r in rows if r[1]])
    if info.revisions:
        _table(
            sheet,
            AREA_TOP + 135,
            [
                (say("Rev."), 15.0),
                (say("Date"), 25.0),
                (say("Description"), 120.0),
                (say("Drawn"), 30.0),
                (say("Checked"), 30.0),
                (say("Approved"), 30.0),
            ],
            [
                [r.index, r.date, r.description, r.drawn_by, r.checked_by, r.approved_by]
                for r in info.revisions
            ],
        )


def _fed_from_text(
    board: Board, numbers: dict[str, int], supplies: dict[str, str], say: Words
) -> str:
    """Where a sub-board's supply comes from, with the page and column it is drawn on."""
    source = f"{board.fed_from} {supplies.get(board.name, '')}".rstrip()
    page = numbers.get(f"feeder:{board.name}")
    if page:
        source += f" /{page}.{numbers.get(f'feedercol:{board.name}', 0)}"
    return say("from {source}", source=source)


def _draw_main(
    sheet: Sheet,
    plan: _Plan,
    numbers: dict[str, int],
    supplies: dict[str, str],
    first: bool,
    say: Words,
) -> None:
    board = plan.board
    assert board is not None
    bus_y = AREA_TOP + 52.0
    feeders = [str(f) for f in plan.payload]
    last_x = column_x(len(feeders)) if feeders else column_x(1)
    if first:
        supply = board.supply
        x = column_x(0)
        system = "3/N/PE" if supply.phases == 3 else "1/N/PE"
        sheet.add(
            Text(x, AREA_TOP + 6, f"{system} ~ {_plain(supply.frequency_hz)} Hz", size=2.5),
            Text(x, AREA_TOP + 9.5, f"{_plain(supply.voltage_v)} V {supply.earthing}", size=2.5),
        )
        if supply.fault_level_ka:
            sheet.add(Text(x, AREA_TOP + 13, f"Ik {_plain(supply.fault_level_ka)} kA", size=2.5))
        if board.fed_from:
            sheet.add(
                Text(
                    x + 30.0, AREA_TOP + 6, _fed_from_text(board, numbers, supplies, say), size=2.5
                )
            )
        top = AREA_TOP + 18.0
        for incomer_id in board.incomer_ids:
            incomer = board.device(incomer_id)
            items, bottom = _symbol(incomer, x, top)
            sheet.add(*items)
            sheet.add(*symbols.labels(x, top + 8, [_product(incomer), rating_text(incomer)]))
            top = bottom
        sheet.add(Line(x, top, x, bus_y), *symbols.busbar(x, last_x, bus_y))
    else:
        sheet.add(
            *symbols.busbar(AREA_LEFT + 4.0, last_x, bus_y),
            Text(AREA_LEFT + 4.0, bus_y - 2.0, say("Busbar, continued"), size=2.2),
        )
    for column, feeder in enumerate(feeders, start=1):
        x = column_x(column)
        kind, ident = feeder.split(":", 1)
        top = bus_y
        if kind == "group":
            rcd = board.device(ident)
            chain = [board.device(rcd.upstream_id)] if rcd.upstream_id else []
            chain.append(rcd)
            sheet.add(Line(x, top, x, top + 6))
            top += 6
            for device in chain:
                if device.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE:
                    items, bottom = symbols.residual_current_device(x, top, device.poles or 4)
                else:
                    items, bottom = symbols.circuit_breaker(x, top, device.poles or 3)
                sheet.add(*items)
                sheet.add(*symbols.labels(x, top + 8, [_product(device), rating_text(device)]))
                top = bottom
            target = numbers.get(f"dist:{board.id}:{ident}")
            sheet.add(Line(x, top, x, top + 8))
            if target:
                sheet.add(
                    Text(
                        x,
                        top + 12,
                        say("to {target}", target=f"/{target}.0"),
                        size=2.2,
                        anchor=Anchor.MIDDLE,
                    )
                )
        else:
            circuit = next(c for c in board.circuits if c.id == ident)
            sheet.add(Line(x, top, x, top + 6))
            top += 6
            breaker = board.device(circuit.device_ids[0])
            items, bottom = _symbol(breaker, x, top)
            sheet.add(*items)
            sheet.add(*symbols.labels(x, top + 8, [_product(breaker), rating_text(breaker)]))
            target = numbers.get(f"dist:{board.id}:{circuit.id}")
            sheet.add(Line(x, bottom, x, bottom + 8))
            if target:
                sheet.add(
                    Text(
                        x,
                        bottom + 12,
                        say("to {target}", target=f"/{target}.0"),
                        size=2.2,
                        anchor=Anchor.MIDDLE,
                    )
                )
            for n, line in enumerate(textwrap.wrap(circuit.description, 18)[:3]):
                sheet.add(Text(x, bottom + 17 + n * 3.0, line, size=2.2, anchor=Anchor.MIDDLE))


def _symbol(device: Device, x: float, top: float) -> tuple[list[Item], float]:
    """The single-line symbol for a device, by what it does."""
    poles = device.poles or 1
    if device.kind is DeviceKind.FUSE:
        return symbols.fuse(x, top, poles)
    if device.kind is DeviceKind.DRIVE:
        return symbols.drive(x, top)
    if device.kind is DeviceKind.OVERLOAD_RELAY:
        return symbols.overload_relay(x, top, poles)
    if device.kind is DeviceKind.CONTACTOR:
        return symbols.contactor(x, top, device.poles or 2)
    if device.kind is DeviceKind.SWITCH_DISCONNECTOR:
        return symbols.switch_disconnector(x, top, poles)
    return symbols.circuit_breaker(x, top, poles)


def _draw_chain(sheet: Sheet, board: Board, circuit: Circuit, x: float, top: float) -> float:
    """Draw a circuit's devices down a column from ``top``; return the bottom.

    A star-delta starter's delta and star contactors are drawn as one Y/D
    block labelled with both, the way a single-line diagram shows them.
    """
    devices = [board.device(device_id) for device_id in circuit.device_ids]
    changeover: list[Device] = []
    if circuit.starter is MotorStarter.STAR_DELTA:
        changeover = [d for d in devices if d.id.endswith(("-delta", "-star"))]
        devices = [d for d in devices if d not in changeover]
    if changeover:
        # The block goes where the delta contactor was: after the line contactor.
        line = next((i for i, d in enumerate(devices) if d.kind is DeviceKind.CONTACTOR), 0)
        devices.insert(line + 1, changeover[0])
    bottom = top
    for index, device in enumerate(devices):
        start = bottom if index == 0 else bottom + 2.0
        if index:
            sheet.add(Line(x, bottom, x, start))
        clearance = 4.0
        if changeover and device is changeover[0]:
            items, bottom = symbols.star_delta(x, start)
            labels = [
                " ".join(_product(d) for d in changeover),
                "/".join(rating_text(d).split(" ")[0] for d in changeover),
            ]
            clearance = symbols.WIDE_HALF_WIDTH + 1.0
        elif device.kind is DeviceKind.OVERLOAD_RELAY:
            # Article and setting on lines of their own: a column is narrow.
            items, bottom = _symbol(device, x, start)
            article, _, setting = rating_text(device).partition(" set ")
            labels = [_product(device), article, f"set {setting}" if setting else ""]
        else:
            items, bottom = _symbol(device, x, start)
            labels = [_product(device), rating_text(device)]
            if device.kind is DeviceKind.DRIVE:
                clearance = symbols.WIDE_HALF_WIDTH + 1.0
        sheet.add(*items)
        sheet.add(*symbols.labels(x, start + 8, labels, clearance=clearance))
    return bottom


def _draw_distribution(sheet: Sheet, plan: _Plan, numbers: dict[str, int], say: Words) -> None:
    board = plan.board
    assert board is not None
    circuits = [c for c in plan.payload if isinstance(c, Circuit)]
    bus_y = AREA_TOP + 14.0
    last_x = column_x(max(0, len(circuits) - 1))
    if plan.group:
        rcd = board.device(plan.group)
        source = say("from {source}", source=f"{_product(rcd)} {rating_text(rcd)}")
        back_page = numbers.get(f"main:{board.id}:{plan.group}")
        back_column = numbers.get(f"column:{board.id}:{plan.group}", 0)
        back = f"{back_page}.{back_column}" if back_page else None
    else:
        source = say("from busbar")
        busbar_page = numbers.get(f"main:{board.id}:busbar")
        back = f"{busbar_page}.0" if busbar_page else None
    if back:
        source += f"  /{back}"
    sheet.add(
        Line(AREA_LEFT + 2.0, bus_y, last_x + 6.0, bus_y, 0.6),
        Text(AREA_LEFT + 2.0, bus_y - 2.5, source, size=2.4),
    )
    for column, circuit in enumerate(circuits):
        x = column_x(column)
        top = bus_y + 12.0
        sheet.add(Line(x, bus_y, x, top), Text(x + 1.0, bus_y + 5.0, circuit.phase.value, size=2.0))
        bottom = _draw_chain(sheet, board, circuit, x, top)
        # The column has room for the cable under up to three devices.
        cable_length = max(8.0, 30.0 - (bottom - top - symbols.DEVICE_HEIGHT) * 0.4)
        items, end = symbols.cable_end(x, bottom, cable_length)
        sheet.add(*items)
        cable = board.cable(circuit.cable_id) if circuit.cable_id else None
        sheet.add(
            *symbols.labels(
                x,
                bottom + 10,
                [
                    f"-{cable.designation.product}" if cable and cable.designation else "",
                    cable_text(board, circuit),
                ],
            )
        )
        y = end + 6.0
        for n, line in enumerate(textwrap.wrap(circuit.description, 20)[:4]):
            sheet.add(Text(x, y + n * 3.0, line, size=2.2, anchor=Anchor.MIDDLE))
        if circuit.feeds:
            target = numbers.get(f"main:{circuit.feeds}:busbar")
            reference = say(
                "to {target}", target=circuit.feeds + (f" /{target}.0" if target else "")
            )
            sheet.add(Text(x, y + 19.0, reference, size=2.2, anchor=Anchor.MIDDLE))
        sheet.add(
            Text(
                x,
                y + 15.0,
                f"{_plain(circuit.power_kw)} kW  Ib {_plain(circuit.design_current_a)} A",
                size=2.0,
                anchor=Anchor.MIDDLE,
            )
        )


def _draw_contents(sheet: Sheet, rows: list[list[str]], say: Words) -> None:
    _table(
        sheet,
        AREA_TOP + 6,
        [(say("Page"), 20.0), (say("Title"), 150.0), (say("Board"), 80.0)],
        rows,
    )


def _draw_notes(sheet: Sheet, plan: _Plan, say: Words) -> None:
    rows = [[str(n), str(note)] for n, note in enumerate(plan.payload, start=1)]
    _table(sheet, AREA_TOP + 6, [(say("No."), 15.0), (say("Note"), 340.0)], rows, size=2.6)


def build_drawing_set(project: DesignProject, profile: CompanyProfile) -> list[Sheet]:
    """Lay a project out as the pages of its drawing set.

    Args:
        project: The project, designated under ``profile``.
        profile: The company whose pages, order and title block apply.

    Returns:
        The sheets, numbered from 1, frame and title block drawn.
    """
    say = Words(profile.language)
    plans = _plan(project, profile, say)
    numbers: dict[str, int] = {}
    seen_main: set[str] = set()
    for number, plan in enumerate(plans, start=1):
        board = plan.board
        if board is None:
            continue
        if plan.kind is PageKind.SINGLE_LINE:
            if board.id not in seen_main:
                seen_main.add(board.id)
                numbers[f"main:{board.id}:busbar"] = number
            for column, feeder in enumerate(plan.payload, start=1):
                _, ident = str(feeder).split(":", 1)
                numbers[f"main:{board.id}:{ident}"] = number
                numbers[f"column:{board.id}:{ident}"] = column
        elif plan.kind is PageKind.DISTRIBUTION:
            key = plan.group or "busbar"
            numbers.setdefault(f"dist:{board.id}:{key}", number)
            circuits = [c for c in plan.payload if isinstance(c, Circuit)]
            for column, circuit in enumerate(circuits):
                if circuit.upstream_id is None:
                    numbers.setdefault(f"dist:{board.id}:{circuit.id}", number)
                if circuit.feeds:
                    numbers[f"feeder:{circuit.feeds}"] = number
                    numbers[f"feedercol:{circuit.feeds}"] = column
    # Each sub-board's feeder breaker, by the sub-board it supplies.
    supplies = {
        circuit.feeds: _product(board.device(circuit.device_ids[0]))
        for board in project.boards
        for circuit in board.circuits
        if circuit.feeds and circuit.device_ids
    }

    total = len(plans)
    contents = [
        [str(n), p.title, p.board.name if p.board else ""]
        for n, p in enumerate(plans, start=1)
        if p.kind is not PageKind.CONTENTS
    ]
    contents_pages = _chunks(list(contents), CONTENTS_ROWS_PER_PAGE)
    sheets: list[Sheet] = []
    started: set[str] = set()
    contents_index = 0
    for number, plan in enumerate(plans, start=1):
        sheet = Sheet(number=number, title=plan.title, board=plan.board.name if plan.board else "")
        if plan.kind is PageKind.TITLE:
            _draw_title(sheet, project, profile, say)
        elif plan.kind is PageKind.CONTENTS:
            page_rows = contents_pages[min(contents_index, len(contents_pages) - 1)]
            _draw_contents(
                sheet, [[str(c) for c in row] for row in page_rows if isinstance(row, list)], say
            )
            contents_index += 1
        elif plan.kind is PageKind.SINGLE_LINE and plan.board is not None:
            first = plan.board.id not in started
            started.add(plan.board.id)
            _draw_main(sheet, plan, numbers, supplies, first, say)
        elif plan.kind is PageKind.DISTRIBUTION:
            _draw_distribution(sheet, plan, numbers, say)
        elif plan.kind is PageKind.NOTES:
            _draw_notes(sheet, plan, say)
        elif plan.kind is PageKind.TERMINALS:
            _table(
                sheet,
                AREA_TOP + 6,
                [
                    (say("Terminal"), 25.0),
                    (say("Conductor"), 22.0),
                    (say("Circuit"), 140.0),
                    (say("Cable"), 40.0),
                    ("mm²", 20.0),
                    (say("Article"), 60.0),
                ],
                [[str(c) for c in row] for row in plan.payload if isinstance(row, list)],
            )
        elif plan.kind is PageKind.CABLES:
            _table(
                sheet,
                AREA_TOP + 6,
                [
                    (say("Cable"), 45.0),
                    (say("From"), 55.0),
                    (say("To"), 120.0),
                    (say("Type"), 60.0),
                    (say("Length"), 25.0),
                ],
                [[str(c) for c in row] for row in plan.payload if isinstance(row, list)],
            )
        elif plan.kind is PageKind.PARTS:
            _table(
                sheet,
                AREA_TOP + 6,
                [
                    (say("Qty"), 15.0),
                    (say("Description"), 105.0),
                    (say("Article"), 70.0),
                    (say("Order number"), 45.0),
                    (say("Devices"), 125.0),
                ],
                [[str(c) for c in row] for row in plan.payload if isinstance(row, list)],
            )
        _frame(sheet, total, project, profile, say)
        sheets.append(sheet)
    return sheets
