"""A board's outgoing terminal strip: one terminal per conductor of each cable.

Every outgoing cable lands on the board's strip ``-X1``, numbered in circuit
order, one terminal per core:

* a single-phase circuit: its line (L1, L2 or L3), N and PE;
* a three-phase circuit with neutral: L1 L2 L3 N PE; without, L1 L2 L3 PE;
* a motor: U V W PE, or for star-delta U1 V1 W1 U2 V2 W2 PE.

Each terminal is chosen from Siemens 8WH1 through-type terminals for the
conductor it clamps and the circuit's rated current (``terminal_blocks``),
its PE terminal of the same size for the PE core. The range is for copper:
an aluminium conductor's terminal is left unselected, as is one the range
does not reach, rather than named wrongly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ai.tools import terminal_blocks
from app.core.errors import ValidationError
from app.models.schemas.design import Board, Circuit, MotorStarter, Phase

#: The strip every outgoing cable lands on.
STRIP = "X1"


@dataclass(frozen=True)
class Terminal:
    """One terminal of a strip.

    Attributes:
        number: Its place on the strip, from 1.
        function: The conductor it takes: "L1", "N", "PE", "U1", ...
        circuit: The circuit's description.
        cable: The cable's designation, or its id before designation.
        conductor_mm2: The conductor's cross-section.
        article: The 8WH1 article, or ``None`` where none is selected.
    """

    number: int
    function: str
    circuit: str
    cable: str
    conductor_mm2: Decimal
    article: str | None


def functions(circuit: Circuit, cores: int) -> list[str]:
    """The conductors a circuit's cable brings to the strip, in order.

    Args:
        circuit: The circuit.
        cores: Its cable's cores, PE included.

    Returns:
        One name per core.
    """
    if circuit.starter is MotorStarter.STAR_DELTA:
        return ["U1", "V1", "W1", "U2", "V2", "W2", "PE"][:cores]
    if circuit.starter is not None:
        return ["U", "V", "W", "PE"][:cores]
    if circuit.phase is not Phase.THREE_PHASE:
        return [circuit.phase.value, "N", "PE"][:cores]
    if cores >= 5:
        return ["L1", "L2", "L3", "N", "PE"]
    return ["L1", "L2", "L3", "PE"][:cores]


def _articles(board: Board, circuit: Circuit, section: Decimal, copper: bool) -> tuple[str, str]:
    """The line and PE terminal articles for a circuit's conductors, or ``("", "")``."""
    if not copper:
        return "", ""
    current = circuit.design_current_a
    if circuit.device_ids:
        rated = board.device(circuit.device_ids[0]).rated_current_a
        if rated is not None:
            current = max(current, rated)
    try:
        chosen = terminal_blocks.select_terminal(cross_section_mm2=section, current_a=current)
    except ValidationError:
        return "", ""
    return chosen.article, chosen.pe_article or ""


def strip(board: Board) -> list[Terminal]:
    """The board's outgoing terminal strip.

    Args:
        board: The board, designated or not.

    Returns:
        Its terminals, numbered in circuit order.
    """
    terminals: list[Terminal] = []
    for circuit in board.circuits:
        if circuit.cable_id is None:
            continue
        cable = board.cable(circuit.cable_id)
        name = f"-{cable.designation.product}" if cable.designation else cable.id
        line, pe = _articles(board, circuit, cable.cross_section_mm2, cable.material == "Cu")
        for function in functions(circuit, cable.cores):
            article = pe if function == "PE" else line
            terminals.append(
                Terminal(
                    number=len(terminals) + 1,
                    function=function,
                    circuit=circuit.description,
                    cable=name,
                    conductor_mm2=cable.cross_section_mm2,
                    article=article or None,
                )
            )
    return terminals
