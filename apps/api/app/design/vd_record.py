"""The voltage-drop calculation record (``pp-vdrec/1``): what a design computed, as it ran.

The board design fills a :class:`ProjectTrace` while it designs (ADR 0004):
each circuit's inputs and where they came from, the current and voltage it
used, every table cell it read for every section it tried, how the cable ended
at its final size and why, the final drop raw and rounded, the limit and the
key it came from, and what the feeders before the board contributed. Nothing
here calculates: it only keeps what ``distribution`` and ``voltage_drop``
computed. :func:`build` turns the trace into the stored JSON document.

The record is evidence of software execution, not engineering validation. It
names what the software does not know (an edition the code does not record, a
feeder whose drop was not checked) rather than filling it in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools.cable_sizing import TableReading

#: The record's schema marker.
SCHEMA = "pp-vdrec/1"

#: Said by every record, verbatim.
STATEMENT = (
    "Evidence of what this software computed when the revision was saved. "
    "It is not engineering validation: the method, tables and limits are not "
    "confirmed by it."
)

#: What the software does not model, said by every record.
NOT_MODELLED = (
    "The drop before the project's first board (the supply cable to it) is not "
    "modelled; the software takes it as 0.",
)


@dataclass
class Attempt:
    """One section the drop was read at, and what it read."""

    section_mm2: Decimal
    raw_percent: Decimal
    readings: tuple[TableReading, ...]


@dataclass
class CheckTrace:
    """One drop check (running, or while a motor starts) as it ran."""

    kind: str
    status: str = "checked"
    reason: str | None = None
    limit_percent: Decimal | None = None
    limit_source: str | None = None
    upstream_percent: Decimal = Decimal(0)
    attempts: list[Attempt] = field(default_factory=list)
    selected_mm2: Decimal | None = None
    final: Attempt | None = None
    #: The design's own verdict on the final cable (``fit`` or the settle step).
    within: bool | None = None


@dataclass
class CircuitTrace:
    """One circuit of a board, as its drop was worked."""

    circuit_id: str
    description: str
    load: str
    inputs: dict[str, Any]
    current: dict[str, Any] = field(default_factory=dict)
    voltage: dict[str, Any] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)
    running: CheckTrace | None = None
    starting: CheckTrace | None = None
    final_section_mm2: Decimal | None = None
    saved_percent: Decimal | None = None

    def section(self, reason: str, before: Decimal | None, after: Decimal) -> None:
        """Note the cable's section at a step, and why it changed there, if it did."""
        if before is None:
            self.sections.append({"step": reason, "section_mm2": _text(after)})
        elif after != before:
            self.sections.append(
                {"step": reason, "from_mm2": _text(before), "section_mm2": _text(after)}
            )


@dataclass
class BoardTrace:
    """One board's circuits, and what its feeders dropped before it."""

    board: str
    supply: dict[str, Any]
    conditions: dict[str, Any]
    upstream_percent: Decimal = Decimal(0)
    upstream: list[dict[str, Any]] = field(default_factory=list)
    upstream_unknown: bool = False
    circuits: list[CircuitTrace] = field(default_factory=list)


@dataclass
class ProjectTrace:
    """Every board's trace, from the design pass that is saved."""

    boards: list[BoardTrace] = field(default_factory=list)


class CalculationRecord(BaseModel):
    """The stored record's envelope, checked on read (its body is the hashed JSON)."""

    model_config = ConfigDict(extra="forbid")

    schema_: str = Field(alias="schema", pattern=r"^pp-vdrec/1$")
    statement: str
    snapshot_sha256: str = Field(min_length=64, max_length=64)
    profile_sha256: str = Field(min_length=64, max_length=64)
    rules_version: str = Field(min_length=1, max_length=40)
    not_modelled: list[str]
    boards: list[dict[str, Any]]


def build(
    trace: ProjectTrace, *, snapshot_sha256: str, profile_sha256: str, rules_version: str
) -> dict[str, Any]:
    """The JSON document stored for a revision, bound to its snapshot, profile and rules.

    Every number is a string (``Decimal`` as written), so the document has a
    canonical encoding (``app.design.snapshot.canonical_json``).
    """
    return {
        "schema": SCHEMA,
        "statement": STATEMENT,
        "snapshot_sha256": snapshot_sha256,
        "profile_sha256": profile_sha256,
        "rules_version": rules_version,
        "not_modelled": list(NOT_MODELLED),
        "boards": [_board(b) for b in trace.boards],
    }


def _board(board: BoardTrace) -> dict[str, Any]:
    return {
        "board": board.board,
        "supply": board.supply,
        "conditions": board.conditions,
        "upstream": {
            "percent_counted": _text(board.upstream_percent),
            "complete": not board.upstream_unknown,
            "contributions": board.upstream,
        },
        "circuits": [_circuit(c, board) for c in board.circuits],
    }


def _circuit(circuit: CircuitTrace, board: BoardTrace) -> dict[str, Any]:
    return {
        "circuit_id": circuit.circuit_id,
        "description": circuit.description,
        "load": circuit.load,
        "inputs": circuit.inputs,
        "current": circuit.current,
        "voltage": circuit.voltage,
        "sections": circuit.sections,
        "final_section_mm2": _text(circuit.final_section_mm2),
        "saved_voltage_drop_percent": _text(circuit.saved_percent),
        "running": _check(circuit.running, board),
        "starting": _check(circuit.starting, board),
    }


def _check(check: CheckTrace | None, board: BoardTrace) -> dict[str, Any] | None:
    if check is None:
        return None
    if check.status != "checked" or check.final is None:
        return {"kind": check.kind, "status": check.status, "reason": check.reason}
    rounded = check.final.raw_percent.quantize(Decimal("0.01"))
    total = rounded + check.upstream_percent
    return {
        "kind": check.kind,
        "status": "checked",
        "limit_percent": _text(check.limit_percent),
        "limit_source": check.limit_source,
        "upstream_percent_counted": _text(check.upstream_percent),
        "attempts": [_attempt(a) for a in check.attempts],
        "selected_by_check_mm2": _text(check.selected_mm2),
        "final": {
            **_attempt(check.final),
            "rounded_percent": _text(rounded),
        },
        # The design's own verdict: the unrounded drop against the limit less
        # what was counted upstream.
        "within_limit": check.within,
        "total_percent": _text(total),
        # Never complete while a feeder before the board was not checked: the
        # software counted it as 0, which is not known to be its drop.
        "total_complete": not board.upstream_unknown,
    }


def _attempt(attempt: Attempt) -> dict[str, Any]:
    return {
        "section_mm2": _text(attempt.section_mm2),
        "raw_percent": _text(attempt.raw_percent),
        "readings": [_reading(r) for r in attempt.readings],
    }


def _reading(reading: TableReading) -> dict[str, Any]:
    return {
        "table": reading.table,
        "document_id": reading.document_id,
        "edition": reading.edition if reading.edition is not None else "unknown",
        "column": reading.column,
        "cross_section_mm2": _text(reading.cross_section_mm2),
        "power_factor": _text(reading.power_factor),
        "cell_v_per_a_km": _text(reading.cell_v_per_a_km),
        "volts": _text(reading.volts),
    }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
