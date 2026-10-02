"""Turn a reviewer's comment into an edit to the load schedule, where it says one.

A comment placed on a circuit (``markups``: the board, and the labels
nearest it, the nearest of a circuit's own standing for it; or of a
residual current group's, standing for the group's one circuit, or for its
circuits for the engineer to choose among) is read for one change the schedule can take, and offered to the
engineer to apply or not; nothing is changed until they do. What is read:

* the circuit removed: "delete", "remove", "omit", "احذف";
* its power: a figure in kW;
* its cable's length: a figure in m, or after "length"/"طول";
* its power factor: after "pf", "cos phi" or "معامل القدرة";
* its phases: "3 phase", "3ph", "ثلاثي"; "single phase", "1ph", "أحادي";
* a motor's starter: "star-delta", "DOL", "VFD"/"drive"/"inverter".

On a feeder, only a length is read, and it is the sub-board's feeder length.
A comment that says none of these, or that sits on no circuit, is left to
the engineer as it was. Arabic-Indic digits are read as digits.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.schemas.design import Board, Circuit, DesignProject

#: Arabic-Indic digits and the Arabic decimal separator (U+066B), as ASCII.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩\u066b", "0123456789.")

_NUMBER = r"(\d+(?:\.\d+)?)"

_REMOVE = re.compile(r"\b(delete|remove|omit)\b|احذف|حذف|الغ", re.IGNORECASE)
_POWER = re.compile(_NUMBER + r"\s*kw\b", re.IGNORECASE)
_LENGTH = re.compile(
    r"(?:length|طول)\s*[:=]?\s*" + _NUMBER + r"|" + _NUMBER + r"\s*m(?![a-z²2])",
    re.IGNORECASE,
)
_POWER_FACTOR = re.compile(
    r"(?:\bpf\b|cos\s*(?:phi|φ)?|معامل القدرة)\s*[:=]?\s*(0?\.\d+|1(?:\.0+)?)\b",
    re.IGNORECASE,
)
_THREE_PHASE = re.compile(r"\b3\s*-?\s*(?:ph|phase)\b|three[\s-]phase|ثلاثي", re.IGNORECASE)
_SINGLE_PHASE = re.compile(r"\b1\s*-?\s*(?:ph|phase)\b|single[\s-]phase|أحادي", re.IGNORECASE)
_STARTERS = (
    (re.compile(r"star[\s-]*delta|نجمة", re.IGNORECASE), "star_delta"),
    (re.compile(r"\bdol\b|direct[\s-]on[\s-]line|مباشر", re.IGNORECASE), "dol"),
    (re.compile(r"\bvfd\b|\bdrive\b|inverter|انفرتر|عاكس", re.IGNORECASE), "drive"),
)


@dataclass(frozen=True)
class Suggestion:
    """One change to the schedule a comment asks for.

    Attributes:
        board: The board whose schedule changes.
        circuit: The circuit's description, as the schedule names it.
        load_index: Its place in that board's schedule; ``None`` for a
            change to the board itself (a feeder length).
        field: What changes: "remove", "power_kw", "length_m",
            "power_factor", "phases", "starter" or "feeder_length_m".
        value: Its new value; ``None`` for "remove".
        candidates: Where the comment sits on a residual current group of
            several circuits rather than on one: each circuit it may mean,
            as (description, place in the schedule), for the engineer to
            pick; ``circuit`` is then empty and ``load_index`` ``None``.
    """

    board: str
    circuit: str
    load_index: int | None
    field: str
    value: str | None
    candidates: tuple[tuple[str, int], ...] = ()


def _labels(board: Board, circuit: Circuit) -> set[str]:
    """Every label the drawing can show for a circuit."""
    found = {circuit.description}
    for device_id in circuit.device_ids:
        designation = board.device(device_id).designation
        if designation is not None:
            found.add(str(designation))
            found.add(f"-{designation.product}")
    if circuit.cable_id:
        cable = board.cable(circuit.cable_id).designation
        if cable is not None:
            found.add(str(cable))
            found.add(f"-{cable.product}")
    return found


def _upstream(board: Board, circuit: Circuit) -> set[str]:
    """The devices a circuit is fed through, its board's incomer aside."""
    found: set[str] = set()
    current = circuit.upstream_id
    while current is not None and current not in board.incomer_ids and current not in found:
        found.add(current)
        current = board.device(current).upstream_id
    return found


def _device_labels(board: Board, device_id: str) -> set[str]:
    designation = board.device(device_id).designation
    return {str(designation), f"-{designation.product}"} if designation else set()


def find_circuits(project: DesignProject, board_name: str, labels: Sequence[str]) -> list[Circuit]:
    """The circuits the labels nearest a mark on a board's drawing belong to.

    The nearest label that is a circuit's own (its breaker, cable or
    description) names that circuit; one that is a residual current group's
    (its RCD or its group breaker) names every circuit in the group.

    Args:
        project: The project, designated as the drawing set was.
        board_name: The board the page draws.
        labels: The labels nearest the mark, nearest first.

    Returns:
        The circuits, in schedule order; empty where no label is a circuit's
        or a group's.
    """
    board = next((b for b in project.boards if b.name == board_name), None)
    if board is None:
        return []
    owners = [(circuit, _labels(board, circuit)) for circuit in board.circuits]
    groups: dict[str, list[Circuit]] = {}
    for circuit in board.circuits:
        for device_id in _upstream(board, circuit):
            for label in _device_labels(board, device_id):
                groups.setdefault(label, []).append(circuit)
    for label in (raw.strip() for raw in labels):
        own = next((circuit for circuit, known in owners if label in known), None)
        if own is not None:
            return [own]
        if label in groups:
            return groups[label]
    return []


def _change(text: str, feeder: bool) -> tuple[str, str | None] | None:
    """The one change a comment's text asks for, if any."""
    text = text.translate(_DIGITS)
    if feeder:
        found = _LENGTH.search(text)
        return ("feeder_length_m", found.group(1) or found.group(2)) if found else None
    if _REMOVE.search(text):
        return ("remove", None)
    if found := _POWER.search(text):
        return ("power_kw", found.group(1))
    if found := _POWER_FACTOR.search(text):
        return ("power_factor", found.group(1))
    if found := _LENGTH.search(text):
        return ("length_m", found.group(1) or found.group(2))
    if _THREE_PHASE.search(text):
        return ("phases", "3")
    if _SINGLE_PHASE.search(text):
        return ("phases", "1")
    for pattern, starter in _STARTERS:
        if pattern.search(text):
            return ("starter", starter)
    return None


def suggest(
    project: DesignProject, board_name: str, labels: Sequence[str], text: str
) -> Suggestion | None:
    """The change to the schedule a comment on a drawing asks for.

    Args:
        project: The project, designated as the drawing set was.
        board_name: The board the comment's page draws.
        labels: The labels nearest the comment, nearest first.
        text: What the comment says.

    Returns:
        The change, or ``None`` where the comment sits on no circuit or asks
        for nothing the schedule holds.
    """
    circuits = find_circuits(project, board_name, labels)
    if not circuits:
        return None
    if len(circuits) > 1:
        change = _change(text, feeder=False)
        if change is None:
            return None
        field, value = change
        candidates = tuple(
            (circuit.description, _index(circuit)) for circuit in circuits if circuit.feeds is None
        )
        return Suggestion(board_name, "", None, field, value, candidates)
    (circuit,) = circuits
    change = _change(text, feeder=circuit.feeds is not None)
    if change is None:
        return None
    field, value = change
    if circuit.feeds is not None:
        return Suggestion(circuit.feeds, circuit.description, None, field, value)
    return Suggestion(board_name, circuit.description, _index(circuit), field, value)


def _index(circuit: Circuit) -> int:
    """A circuit's place in its board's schedule, from its id ("c3" is the third)."""
    return int(circuit.id.removeprefix("c")) - 1
