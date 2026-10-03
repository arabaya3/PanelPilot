"""Hand a design to EPLAN Electric P8 and AutoCAD Electrical through their own importers.

Neither tool opens another program's project, and neither publishes its
project format. Each does document a list importer, and these files are
written to those documented layouts:

* **EPLAN Electric P8**, *File > Import > Project data > Devices*: a CSV or
  Excel device list whose columns the user maps once to EPLAN properties in a
  "scheme" (eplan.help, "Import device data" and "Field assignment"). EPLAN
  needs "DT (full)" (property 20006) to create the main functions, and finds
  the part by order number (20919) or type designation (20200); terminals of
  one strip are told apart by "Terminal / pin designation" (20030). Devices
  arrive as unplaced functions, which the engineer drags onto pages where the
  part's macro is placed. The DT is written as IEC 81346 ``=DB1+HALL-Q3``,
  the form EPLAN's own help uses. Semicolon-separated, the separator EPLAN's
  examples use.
* **AutoCAD Electrical**, *Insert Footprint / Insert Terminal (Schematic
  List)*: component and terminal spreadsheets "in this order", 28 and 30
  columns, "CSV comma delimited", no header (Autodesk help, "About Panel
  Footprints", component and panel terminals spreadsheet data format). TAG is
  the product aspect, INST the function aspect and LOC the location aspect,
  as AutoCAD Electrical's IEC mode splits a designation.

The designation columns are written as they are, since they are the very
values the importers read: a leading ``=`` is not escaped. Text the engineer
typed (descriptions) is, so that a description such as ``=HYPERLINK(...)``
cannot run in a spreadsheet the file is opened in on the way.

Neither file was tried in the programs themselves; the importers' own
dialogs are where the mapping is confirmed.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from app.design import terminals
from app.design.export_lists import text_cell
from app.models.schemas.design import Board, Cable, DesignProject, Device, DeviceKind

_BOM = "﻿"

#: EPLAN device-import columns, in the order written.
EPLAN_COLUMNS: tuple[str, ...] = (
    "DT (full)",
    "Terminal / pin designation",
    "Order number",
    "Type designation of part",
    "Manufacturer",
    "Function text",
    "Rated current A",
    "Poles",
    "Characteristic",
    "Breaking capacity kA",
    "Residual current mA",
)

#: AutoCAD Electrical's component and terminal spreadsheet widths.
ACE_COMPONENT_COLUMNS = 28
ACE_TERMINAL_COLUMNS = 30


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _write(rows: list[list[str]], *, delimiter: str, header: tuple[str, ...] | None) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    if header is not None:
        writer.writerow(header)
    writer.writerows(rows)
    return _BOM + buffer.getvalue()


def _part(project: DesignProject, part_key: str | None) -> tuple[str, str, str]:
    """Manufacturer, type designation and order number of a part, or blanks."""
    if part_key is None:
        return "", "", ""
    part = project.part(part_key)
    return part.manufacturer, part.type_number, part.order_number or ""


def _aspects(board: Board, product: str) -> tuple[str, str, str]:
    """A designation's function, location and product aspects on a board."""
    known = next(
        (device.designation for device in board.devices if device.designation is not None), None
    )
    function = known.function if known and known.function else board.name
    location = known.location if known and known.location else (board.location or "")
    return function, location, product


def _full(function: str, location: str, product: str) -> str:
    return f"={function}" + (f"+{location}" if location else "") + f"-{product}"


def _ratings(device: Device) -> list[str]:
    return [
        _plain(device.rated_current_a),
        str(device.poles or ""),
        device.curve or "",
        _plain(device.breaking_capacity_ka),
        _plain(device.residual_current_ma),
    ]


def _product(device: Device) -> str:
    return device.designation.product if device.designation else device.id


def _cable_type(cable: Cable) -> str:
    return f"{cable.size} {cable.material} {cable.insulation}"


def eplan_device_list(project: DesignProject) -> str:
    """Write the device list EPLAN's *Import device data* reads.

    One row per device and per cable, and one per terminal of each board's
    outgoing strip.

    Args:
        project: The project, designated.

    Returns:
        The CSV text, semicolon-separated, with a header row to map from.
    """
    rows: list[list[str]] = []
    for board in project.boards:
        for device in board.devices:
            function, location, product = _aspects(board, _product(device))
            manufacturer, type_number, order_number = _part(project, device.part_key)
            rows.append(
                [
                    _full(function, location, product),
                    "",
                    order_number,
                    type_number,
                    manufacturer,
                    text_cell(device.description),
                    *_ratings(device),
                ]
            )
        for cable in board.cables:
            product = cable.designation.product if cable.designation else cable.id
            function, location, _ = _aspects(board, product)
            manufacturer, type_number, order_number = _part(project, cable.part_key)
            rows.append(
                [
                    _full(function, location, product),
                    "",
                    order_number,
                    type_number or _cable_type(cable),
                    manufacturer,
                    "",
                    *([""] * 5),
                ]
            )
        function, location, _ = _aspects(board, terminals.STRIP)
        for terminal in terminals.strip(board):
            rows.append(
                [
                    _full(function, location, terminals.STRIP),
                    str(terminal.number),
                    terminal.article or "",
                    "",
                    "",
                    text_cell(f"{terminal.circuit} {terminal.function}".strip()),
                    *([""] * 5),
                ]
            )
    return _write(rows, delimiter=";", header=EPLAN_COLUMNS)


def _ace_kind(device: Device) -> str:
    return {
        DeviceKind.CIRCUIT_BREAKER: "CB",
        DeviceKind.RESIDUAL_CURRENT_DEVICE: "RCD",
        DeviceKind.SWITCH_DISCONNECTOR: "DS",
        DeviceKind.CONTACTOR: "K",
        DeviceKind.OVERLOAD_RELAY: "OL",
        DeviceKind.DRIVE: "VFD",
    }.get(device.kind, device.kind.value.upper())


def ace_component_list(project: DesignProject) -> str:
    """Write AutoCAD Electrical's 28-column component spreadsheet.

    Columns, as Autodesk lists them: TAG, INST, LOC, MOUNT, GROUPWIDTH, MFG,
    CAT, ASM, CNT, UM, DESC1-3, BLKNAM, RATING1-12, ITEM, blank. The ratings
    hold In, poles, curve, breaking capacity and IΔn; BLKNAM is left blank
    for the user's catalogue lookup to fill.

    Args:
        project: The project, designated.

    Returns:
        The CSV text, comma-delimited, no header.
    """
    rows: list[list[str]] = []
    for board in project.boards:
        for device in board.devices:
            function, location, product = _aspects(board, _product(device))
            manufacturer, type_number, order_number = _part(project, device.part_key)
            ratings = _ratings(device)
            row = [
                product,
                function,
                location,
                "",
                "",
                manufacturer,
                order_number or type_number,
                "",
                "1",
                "",
                text_cell(device.description),
                _ace_kind(device),
                "",
                "",
                *ratings,
                *([""] * (12 - len(ratings))),
                "",
                "",
            ]
            rows.append(row)
    return _write(rows, delimiter=",", header=None)


def ace_terminal_list(project: DesignProject) -> str:
    """Write AutoCAD Electrical's 30-column panel terminal spreadsheet.

    Columns, as Autodesk lists them: TAGSTRIP, INST, LOC, MOUNT, GROUPWIDTH,
    MFG, CAT, ASM, CNT, UM, DESC1-3, BLOCK, RATING1-12, ITEM, TERMNO, blank,
    WIRENO.

    Args:
        project: The project, designated.

    Returns:
        The CSV text, comma-delimited, no header.
    """
    rows: list[list[str]] = []
    for board in project.boards:
        function, location, _ = _aspects(board, terminals.STRIP)
        for terminal in terminals.strip(board):
            rows.append(
                [
                    terminals.STRIP,
                    function,
                    location,
                    "",
                    "",
                    "",
                    terminal.article or "",
                    "",
                    "1",
                    "",
                    text_cell(terminal.circuit),
                    terminal.function,
                    terminal.cable,
                    "",
                    _plain(terminal.conductor_mm2),
                    *([""] * 11),
                    "",
                    str(terminal.number),
                    "",
                    "",
                ]
            )
    return _write(rows, delimiter=",", header=None)
