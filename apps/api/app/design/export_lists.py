"""The project's lists as CSV: what every ECAD tool and spreadsheet imports.

EPLAN, AutoCAD Electrical, SEE Electrical, WSCAD and PC|SCHEMATIC all read a
device or parts list from a spreadsheet, and Excel opens CSV directly; so
each list is written once, in plain CSV, with column names the tools'
import dialogs can be mapped from. UTF-8 with a byte-order mark, so Excel
reads accented and Arabic text correctly instead of guessing a code page.

A cell starting with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is
written with a leading apostrophe. A spreadsheet would otherwise take it as a
formula: an IEC 81346 designation such as ``=DB1-Q3`` shows as ``#NAME?``,
and a description typed as ``=HYPERLINK(...)`` would run (OWASP "CSV
injection"). Excel shows the cell as text without the apostrophe.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from app.models.schemas.design import Board, DesignProject, Device

_BOM = "\ufeff"

#: First characters a spreadsheet reads as the start of a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _designation(device: Device) -> str:
    return str(device.designation) if device.designation else device.id


def text_cell(value: str) -> str:
    """Make a value read as text in a spreadsheet, never as a formula.

    Args:
        value: The cell's text.

    Returns:
        The value, with a leading apostrophe if it starts like a formula.
    """
    return f"'{value}" if value.startswith(_FORMULA_STARTS) else value


def write_csv(header: list[str], rows: list[list[str]]) -> str:
    """Write rows as CSV for Excel: a byte-order mark, CRLF lines, text-safe cells.

    Args:
        header: The column names.
        rows: The rows; a row may be shorter than the header.

    Returns:
        The CSV text.
    """
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows([text_cell(cell) for cell in row] for row in rows)
    return _BOM + buffer.getvalue()


def _part_columns(project: DesignProject, part_key: str | None) -> list[str]:
    if part_key is None:
        return ["", "", ""]
    part = project.part(part_key)
    return [part.manufacturer, part.type_number, part.order_number or ""]


def device_list(project: DesignProject) -> str:
    """Write every device, one row each, with what feeds it.

    Args:
        project: The project, designated.

    Returns:
        The CSV text.
    """
    rows: list[list[str]] = []
    for board in project.boards:
        by_id = {d.id: d for d in board.devices}
        for device in board.devices:
            upstream = by_id.get(device.upstream_id or "")
            rows.append(
                [
                    board.name,
                    _designation(device),
                    device.kind.value,
                    str(device.poles or ""),
                    _plain(device.rated_current_a),
                    device.curve or "",
                    _plain(device.residual_current_ma),
                    _plain(device.breaking_capacity_ka),
                    *_part_columns(project, device.part_key),
                    _designation(upstream) if upstream else "",
                    device.description,
                ]
            )
    return write_csv(
        [
            "Board",
            "Designation",
            "Kind",
            "Poles",
            "Rated current A",
            "Curve",
            "Residual current mA",
            "Breaking capacity kA",
            "Manufacturer",
            "Type number",
            "Order number",
            "Fed from",
            "Function text",
        ],
        rows,
    )


def parts_list(project: DesignProject) -> str:
    """Write the bill of materials: identical devices counted together.

    Args:
        project: The project, designated.

    Returns:
        The CSV text.
    """
    groups: dict[tuple[object, ...], tuple[Device, list[str]]] = {}
    for board in project.boards:
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
            groups.setdefault(key, (device, []))[1].append(_designation(device))
    rows = [
        [
            str(len(names)),
            sample.kind.value,
            str(sample.poles or ""),
            _plain(sample.rated_current_a),
            sample.curve or "",
            _plain(sample.residual_current_ma),
            *_part_columns(project, sample.part_key),
            " ".join(names),
        ]
        for sample, names in groups.values()
    ]
    return write_csv(
        [
            "Quantity",
            "Kind",
            "Poles",
            "Rated current A",
            "Curve",
            "Residual current mA",
            "Manufacturer",
            "Type number",
            "Order number",
            "Designations",
        ],
        rows,
    )


def _cable_rows(board: Board) -> list[list[str]]:
    rows: list[list[str]] = []
    for circuit in board.circuits:
        if circuit.cable_id is None:
            continue
        cable = board.cable(circuit.cable_id)
        source = board.device(circuit.device_ids[0]) if circuit.device_ids else None
        rows.append(
            [
                board.name,
                str(cable.designation) if cable.designation else cable.id,
                _designation(source) if source else "",
                circuit.description,
                str(cable.cores),
                _plain(cable.cross_section_mm2),
                cable.material,
                cable.insulation,
                _plain(cable.length_m),
                _plain(circuit.voltage_drop_percent),
            ]
        )
    return rows


def cable_list(project: DesignProject) -> str:
    """Write every outgoing cable with where it runs from and to.

    Args:
        project: The project, designated.

    Returns:
        The CSV text.
    """
    rows = [row for board in project.boards for row in _cable_rows(board)]
    return write_csv(
        [
            "Board",
            "Cable",
            "From",
            "To",
            "Cores",
            "Cross-section mm2",
            "Material",
            "Insulation",
            "Length m",
            "Voltage drop %",
        ],
        rows,
    )


def circuit_schedule(project: DesignProject) -> str:
    """Write the designed load schedule: each circuit's load, phase and protection.

    Args:
        project: The project, designated.

    Returns:
        The CSV text.
    """
    rows: list[list[str]] = []
    for board in project.boards:
        for circuit in board.circuits:
            breaker = board.device(circuit.device_ids[0]) if circuit.device_ids else None
            upstream = board.device(circuit.upstream_id) if circuit.upstream_id else None
            cable = board.cable(circuit.cable_id) if circuit.cable_id else None
            rows.append(
                [
                    board.name,
                    circuit.description,
                    circuit.load.value,
                    _plain(circuit.power_kw),
                    _plain(circuit.design_current_a),
                    circuit.phase.value,
                    _designation(breaker) if breaker else "",
                    _plain(breaker.rated_current_a) if breaker else "",
                    breaker.curve or "" if breaker else "",
                    _designation(upstream) if upstream else "",
                    (
                        f"{cable.cores}G{_plain(cable.cross_section_mm2)} {cable.material}"
                        if cable
                        else ""
                    ),
                ]
            )
    return write_csv(
        [
            "Board",
            "Circuit",
            "Load",
            "Power kW",
            "Design current A",
            "Phase",
            "Breaker",
            "Rated current A",
            "Curve",
            "Residual current device",
            "Cable",
        ],
        rows,
    )
