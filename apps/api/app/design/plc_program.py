"""The control program for a board's PLC-switched circuits.

Every circuit a load schedule marks ``controlled`` has a contactor the PLC
drives. This writes, from the design and nothing else, the program that
drives them and the I/O list an engineer wires it to:

* one output per contactor coil;
* one manual-on input per circuit (a wall switch, a KNX or BMS command);
* three shared inputs: ``EStop_OK`` (normally closed, TRUE while healthy),
  ``Auto_Mode`` (the hand/auto selector) and ``Schedule_On`` (a time clock or
  BMS enable).

Each coil follows one rule, written out per circuit:

    K := EStop_OK AND (Manual_On OR (Auto_Mode AND Schedule_On));

so a tripped or broken stop circuit drops every contactor, which is the
fail-safe convention the PLC writer is held to as well. The program is plain
IEC 61131-3 Structured Text without direct addresses (the I/O list carries
those), so the parser-based validator checks it in full.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from app.models.schemas.design import DesignProject, DeviceKind

#: The shared inputs, and what each is for.
SHARED_INPUTS: tuple[tuple[str, str], ...] = (
    ("EStop_OK", "Emergency stop chain healthy (normally closed: TRUE while healthy)"),
    ("Auto_Mode", "Hand/auto selector in auto"),
    ("Schedule_On", "Time clock or BMS enable"),
)


@dataclass(frozen=True)
class IoPoint:
    """One point on the I/O list.

    Attributes:
        tag: The variable name in the program.
        direction: "input" or "output".
        board: The board it belongs to; empty for shared inputs.
        device: The designation of the device it drives or reports.
        description: What it is, for the wiring list.
    """

    tag: str
    direction: str
    board: str
    device: str
    description: str


@dataclass(frozen=True)
class PlcProgram:
    """The program and its I/O.

    Attributes:
        name: The program unit's name.
        source: The Structured Text.
        io: Every input and output, shared inputs first.
    """

    name: str
    source: str
    io: list[IoPoint]


def _identifier(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"X_{cleaned}"
    return cleaned


def _comment(text: str) -> str:
    """Make text safe inside (* ... *): no closer, no characters the checker refuses."""
    return re.sub(r"[^\x20-\x7e]", "?", text).replace("*)", "* )").replace("#", "No.")


def _label(description: str, designation: str) -> str:
    """Name a circuit in the program's comments.

    Not every PLC editor reads non-ASCII source, so a description in Arabic
    or Hebrew is left to the I/O list (UTF-8) and the comment names the
    device by its designation instead of printing question marks.
    """
    return description if description.isascii() else designation


def _declaration(point: IoPoint) -> str:
    role = "manual on" if point.direction == "input" else "contactor coil"
    text = point.description if point.description.isascii() else f"{point.device}: {role}"
    return f"    {point.tag} : BOOL; (* {_comment(text)} *)"


def build_program(project: DesignProject) -> PlcProgram | None:
    """Write the control program for a project's PLC-switched circuits.

    Args:
        project: The project, designated.

    Returns:
        The program and its I/O list, or ``None`` when no circuit is
        PLC-switched.
    """
    io_points = [IoPoint(tag, "input", "", "", text) for tag, text in SHARED_INPUTS]
    rungs: list[str] = []
    seen: set[str] = set()
    for board in project.boards:
        for circuit in board.circuits:
            for device_id in circuit.device_ids:
                device = board.device(device_id)
                if device.kind is not DeviceKind.CONTACTOR:
                    continue
                product = device.designation.product if device.designation else device.id
                base = _identifier(f"{board.name}_{product}")
                while base in seen:
                    base = f"{base}_"
                seen.add(base)
                coil, manual = f"K_{base}", f"Man_{base}"
                designation = str(device.designation) if device.designation else device.id
                label = _label(circuit.description, designation)
                io_points.append(
                    IoPoint(
                        manual,
                        "input",
                        board.name,
                        designation,
                        f"{circuit.description}: manual on",
                    )
                )
                io_points.append(
                    IoPoint(
                        coil,
                        "output",
                        board.name,
                        designation,
                        f"{circuit.description}: contactor coil",
                    )
                )
                heading = designation if label == designation else f"{designation}: {label}"
                rungs.append(
                    f"(* {_comment(heading)} *)\n"
                    f"{coil} := EStop_OK AND ({manual} OR (Auto_Mode AND Schedule_On));"
                )
    if not rungs:
        return None
    name = _identifier(f"{project.info.name}_Control")
    inputs = "\n".join(_declaration(point) for point in io_points if point.direction == "input")
    outputs = "\n".join(_declaration(point) for point in io_points if point.direction == "output")
    body = "\n\n".join(rungs)
    source = (
        f"(* Control of PLC-switched circuits: {_comment(project.info.name)}. *)\n"
        "(* Generated from the design; every coil drops when EStop_OK is FALSE. *)\n"
        f"PROGRAM {name}\n"
        f"VAR_INPUT\n{inputs}\nEND_VAR\n"
        f"VAR_OUTPUT\n{outputs}\nEND_VAR\n\n"
        f"{body}\n\n"
        "END_PROGRAM\n"
    )
    return PlcProgram(name=name, source=source, io=io_points)


def io_list_csv(program: PlcProgram) -> str:
    """Write the I/O list as CSV, with a blank address column to fill in.

    Args:
        program: The program.

    Returns:
        The CSV text, with a byte-order mark for Excel.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["Tag", "Direction", "Type", "Board", "Device", "Description", "Address"])
    for point in program.io:
        writer.writerow(
            [point.tag, point.direction, "BOOL", point.board, point.device, point.description, ""]
        )
    return "﻿" + buffer.getvalue()
