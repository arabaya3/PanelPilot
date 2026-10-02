"""The control program for a board's PLC-driven circuits.

Written from the design and nothing else: one program, plain IEC 61131-3
Structured Text without direct addresses (the I/O list carries those), so the
parser-based validator checks it in full. Three inputs are shared:
``EStop_OK`` (normally closed, TRUE while healthy), ``Auto_Mode`` (the
hand/auto selector) and ``Schedule_On`` (a time clock or BMS enable).

Each circuit the PLC drives gets the logic its devices call for:

* **Switched circuit** (a contactor on a lighting or socket circuit): a
  manual-on input; the coil is
  ``EStop_OK AND (Man OR (Auto_Mode AND Schedule_On))``.
* **Direct-on-line motor**: start (NO) and stop (NC) pushbuttons and the
  overload relay's NC contact. In hand the start latches through the coil;
  in auto the schedule runs it. The stop, the overload and the emergency stop
  drop it either way.
* **Star-delta motor**: the same run condition drives the line contactor; a
  TON times the star period, and the delta contactor closes 100 ms after the
  star contactor opens, each interlocked against the other.
* **Drive**: the same run condition becomes the drive's run command, gated
  by its ready (no fault) signal; the speed reference is left to the drive.

Every output drops when ``EStop_OK`` is FALSE: the fail-safe convention the
PLC writer is held to as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.design.export_lists import write_csv
from app.models.schemas.design import Board, Circuit, DesignProject, DeviceKind, MotorStarter

#: The shared inputs, and what each is for.
SHARED_INPUTS: tuple[tuple[str, str], ...] = (
    ("EStop_OK", "Emergency stop chain healthy (normally closed: TRUE while healthy)"),
    ("Auto_Mode", "Hand/auto selector in auto"),
    ("Schedule_On", "Time clock or BMS enable"),
)

#: How long a star-delta motor runs in star, and the gap before delta.
STAR_TIME = "T#6S"
CHANGEOVER_GAP = "T#100MS"


@dataclass(frozen=True)
class IoPoint:
    """One point on the I/O list.

    Attributes:
        tag: The variable name in the program.
        direction: "input" or "output".
        board: The board it belongs to; empty for shared inputs.
        device: The designation of the device it drives or reports.
        description: What it is, for the wiring list, in the design's words.
        role: What it is, in plain ASCII, for the program's comments.
    """

    tag: str
    direction: str
    board: str
    device: str
    description: str
    role: str = ""


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


@dataclass
class _Writer:
    """What a program is assembled from, circuit by circuit."""

    io: list[IoPoint] = field(
        default_factory=lambda: [
            IoPoint(tag, "input", "", "", text, text) for tag, text in SHARED_INPUTS
        ]
    )
    internals: list[tuple[str, str, str]] = field(default_factory=list)
    rungs: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    def base(self, board: Board, product: str) -> str:
        base = _identifier(f"{board.name}_{product}")
        while base in self.seen:
            base = f"{base}_"
        self.seen.add(base)
        return base

    def point(
        self,
        tag: str,
        direction: str,
        board: Board,
        device: str,
        circuit: Circuit,
        role: str,
    ) -> str:
        self.io.append(
            IoPoint(tag, direction, board.name, device, f"{circuit.description}: {role}", role)
        )
        return tag


def _identifier(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"X_{cleaned}"
    return cleaned


def _comment(text: str) -> str:
    """Make text safe inside (* ... *): no closer, no characters the checker refuses."""
    return re.sub(r"[^\x20-\x7e]", "?", text).replace("*)", "* )").replace("#", "No.")


def _heading(circuit: Circuit, designation: str) -> str:
    """A rung's comment: the device, and the circuit's name where it is ASCII.

    Not every PLC editor reads non-ASCII source, so a name in Arabic or Hebrew
    is left to the I/O list (UTF-8) rather than printed as question marks.
    """
    if circuit.description.isascii():
        return f"(* {_comment(f'{designation}: {circuit.description}')} *)"
    return f"(* {_comment(designation)} *)"


def _designation(board: Board, device_id: str) -> tuple[str, str]:
    device = board.device(device_id)
    product = device.designation.product if device.designation else device.id
    return product, str(device.designation) if device.designation else device.id


def _run_condition(run: str, start: str, stop: str, healthy: str) -> str:
    """Hand: start latches until stop; auto: the schedule runs it; trips drop it."""
    return (
        f"{run} := EStop_OK AND {stop} AND {healthy} AND "
        f"((NOT Auto_Mode AND ({start} OR {run})) OR (Auto_Mode AND Schedule_On));"
    )


def _switched(writer: _Writer, board: Board, circuit: Circuit, contactor_id: str) -> None:
    product, designation = _designation(board, contactor_id)
    base = writer.base(board, product)
    manual = writer.point(f"Man_{base}", "input", board, designation, circuit, "manual on")
    coil = writer.point(f"K_{base}", "output", board, designation, circuit, "contactor coil")
    writer.rungs.append(
        f"{_heading(circuit, designation)}\n"
        f"{coil} := EStop_OK AND ({manual} OR (Auto_Mode AND Schedule_On));"
    )


def _motor(writer: _Writer, board: Board, circuit: Circuit, starter: MotorStarter) -> None:
    devices = {board.device(d).kind: d for d in reversed(circuit.device_ids)}
    by_role = {d.rsplit("-", 1)[-1]: d for d in circuit.device_ids}
    main_id = (
        devices[DeviceKind.DRIVE] if starter is MotorStarter.DRIVE else by_role.get("line")
    ) or circuit.device_ids[0]
    product, designation = _designation(board, main_id)
    base = writer.base(board, product)
    start = writer.point(f"Start_{base}", "input", board, designation, circuit, "start (NO)")
    stop = writer.point(f"Stop_{base}", "input", board, designation, circuit, "stop (NC)")
    heading = _heading(circuit, designation)

    if starter is MotorStarter.DRIVE:
        ready = writer.point(
            f"Ready_{base}", "input", board, designation, circuit, "drive ready, no fault"
        )
        run = writer.point(f"Run_{base}", "output", board, designation, circuit, "drive run")
        writer.rungs.append(f"{heading}\n{_run_condition(run, start, stop, ready)}")
        return

    relay = by_role.get("overload")
    relay_designation = _designation(board, relay)[1] if relay else designation
    healthy = writer.point(
        f"OL_{base}", "input", board, relay_designation, circuit, "overload healthy (NC)"
    )
    if starter is MotorStarter.DIRECT_ON_LINE:
        coil = writer.point(f"K_{base}", "output", board, designation, circuit, "contactor coil")
        writer.rungs.append(f"{heading}\n{_run_condition(coil, start, stop, healthy)}")
        return

    star = writer.point(
        f"KY_{base}",
        "output",
        board,
        _designation(board, by_role["star"])[1],
        circuit,
        "star contactor coil",
    )
    delta = writer.point(
        f"KD_{base}",
        "output",
        board,
        _designation(board, by_role["delta"])[1],
        circuit,
        "delta contactor coil",
    )
    line = writer.point(f"KM_{base}", "output", board, designation, circuit, "line contactor coil")
    run, star_timer, gap = f"Run_{base}", f"TStar_{base}", f"TGap_{base}"
    writer.internals += [
        (run, "BOOL", "motor commanded to run"),
        (star_timer, "TON", "star period"),
        (gap, "TON", "star-to-delta gap"),
    ]
    writer.rungs.append(
        f"{heading}\n"
        f"{_run_condition(run, start, stop, healthy)}\n"
        f"{star_timer}(IN := {run}, PT := {STAR_TIME});\n"
        f"{gap}(IN := {star_timer}.Q, PT := {CHANGEOVER_GAP});\n"
        f"{line} := {run};\n"
        f"{star} := {run} AND NOT {star_timer}.Q AND NOT {delta};\n"
        f"{delta} := {run} AND {gap}.Q AND NOT {star};"
    )


def build_program(project: DesignProject) -> PlcProgram | None:
    """Write the control program for a project's PLC-driven circuits.

    Args:
        project: The project, designated.

    Returns:
        The program and its I/O list, or ``None`` when the PLC drives
        nothing.
    """
    writer = _Writer()
    for board in project.boards:
        for circuit in board.circuits:
            if circuit.starter is not None:
                _motor(writer, board, circuit, circuit.starter)
                continue
            for device_id in circuit.device_ids:
                if board.device(device_id).kind is DeviceKind.CONTACTOR:
                    _switched(writer, board, circuit, device_id)
    if not writer.rungs:
        return None
    name = _identifier(f"{project.info.name}_Control")

    def declarations(direction: str) -> str:
        return "\n".join(
            f"    {p.tag} : BOOL; (* {_comment(f'{p.device}: {p.role}' if p.device else p.role)} *)"
            for p in writer.io
            if p.direction == direction
        )

    blocks = [
        f"VAR_INPUT\n{declarations('input')}\nEND_VAR",
        f"VAR_OUTPUT\n{declarations('output')}\nEND_VAR",
    ]
    if writer.internals:
        internal = "\n".join(
            f"    {tag} : {kind}; (* {_comment(text)} *)" for tag, kind, text in writer.internals
        )
        blocks.append(f"VAR\n{internal}\nEND_VAR")
    body = "\n\n".join(writer.rungs)
    source = (
        f"(* Control of PLC-driven circuits: {_comment(project.info.name)}. *)\n"
        "(* Generated from the design; every output drops when EStop_OK is FALSE. *)\n"
        f"PROGRAM {name}\n" + "\n".join(blocks) + f"\n\n{body}\n\nEND_PROGRAM\n"
    )
    return PlcProgram(name=name, source=source, io=writer.io)


def io_list_csv(program: PlcProgram) -> str:
    """Write the I/O list as CSV, with a blank address column to fill in.

    Args:
        program: The program.

    Returns:
        The CSV text, with a byte-order mark for Excel.
    """
    return write_csv(
        ["Tag", "Direction", "Type", "Board", "Device", "Description", "Address"],
        [[p.tag, p.direction, "BOOL", p.board, p.device, p.description, ""] for p in program.io],
    )
