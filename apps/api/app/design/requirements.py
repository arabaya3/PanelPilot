"""The requirements record of a revision, ``pp-req/1`` (M6a guided intake).

What the user asked for, kept beside what the designer made of it: the boards
and loads as entered, the feeders the designer generated, the control needs
of controlled loads, the questions the server derives, what this slice does
not design (open items), and a trace from each load and feeder to its design
element. Nothing here changes the design: the designer's defaults still
apply, and a question only makes them visible.

Questions are derived, never sent:

* ``missing_value``: a required input left empty that the request can hold
  (``answerable``): a main board's fault level (``fault_level_ka``) or
  ``Ze`` (``ze_ohm``, the supply's ``earth_loop_ohm``), a sub-board's feeder
  length (``feeder_length_m``);
* ``assumption``: a finding the designer already raises for an assumption.
  ``default_power_factor`` is asked per load left without a power factor
  (answerable: the value is that load's ``power_factor``); a motor's current
  taken from the table or the formula (``motor_current_table``,
  ``motor_current_formula``) is asked as ``motor_current_a``, which
  no input holds, so it can only be acknowledged; the other assumption codes
  (:data:`ASSUMPTION_CODES`) are asked per board, acknowledge only.

A question's id is a digest of what it asks about (kind, field, finding code,
board name, load ref), so the same question keeps its id across revisions.
Refs are the server's: a board keeps its ref while its name is unchanged, a
load while its board and description are, as a previous record gives them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.design import ai_intake, safety_review
from app.models.schemas.design import (
    DesignFinding,
    DesignProject,
    LoadKind,
    ProjectDesignRequest,
    RequirementsInput,
)

SCHEMA = "pp-req/1"

#: Finding codes that state an assumption the designer made, asked per board
#: (beside ``default_power_factor`` and the motor current codes, asked per load).
ASSUMPTION_CODES: tuple[str, ...] = (
    "fault_level_inherited",
    "demand_factors",
    "rules_unconfirmed",
    "contactor_ac1",
)

#: The motor current findings: the current was assumed, not read off a nameplate.
MOTOR_CURRENT_CODES: tuple[str, ...] = ("motor_current_table", "motor_current_formula")

#: The answerable fields, as a question names them.
ANSWERABLE: tuple[str, ...] = ("fault_level_ka", "ze_ohm", "feeder_length_m", "power_factor")


def question_id(kind: str, field: str | None, code: str | None, board: str, load: str) -> str:
    """A question's stable id: a digest of what it asks about."""
    key = "|".join([kind, field or "", code or "", board, load])
    return "q-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def board_refs(request: ProjectDesignRequest, prior: Mapping[str, Any] | None) -> dict[str, str]:
    """Each board's record ref, by name, as :func:`build` gives it (M7a: a link's requirement ref).

    Args:
        request: The request.
        prior: The previous revision's record, if any.

    Returns:
        ``{board name: ref}``.
    """
    return {
        board.name: ref
        for board, ref in zip(request.boards, _board_refs(request, prior), strict=True)
    }


def _board_refs(request: ProjectDesignRequest, prior: Mapping[str, Any] | None) -> list[str]:
    known = {b["name"]: b["ref"] for b in (prior or {}).get("boards", [])}
    used = set(known.values())
    refs: list[str] = []
    counter = 0
    for board in request.boards:
        ref = known.get(board.name)
        if ref is None or ref in refs:
            counter += 1
            while f"b{counter}" in used or f"b{counter}" in refs:
                counter += 1
            ref = f"b{counter}"
        refs.append(ref)
    return refs


def _load_refs(
    board_ref: str, descriptions: Sequence[str], prior: Mapping[str, Any] | None
) -> list[str]:
    earlier = [
        (load["description"], load["ref"])
        for load in (prior or {}).get("loads", [])
        if load.get("board_ref") == board_ref
    ]
    taken = {ref for _, ref in earlier}
    refs: list[str | None] = []
    for description in descriptions:
        match = next((ref for text, ref in earlier if text == description), None)
        if match is not None:
            earlier.remove((description, match))
        refs.append(match)
    counter = 0
    out: list[str] = []
    for ref in refs:
        if ref is None:
            counter += 1
            while f"{board_ref}.l{counter}" in taken:
                counter += 1
            ref = f"{board_ref}.l{counter}"
            taken.add(ref)
        out.append(ref)
    return out


def build(
    request: ProjectDesignRequest,
    designed: DesignProject | None,
    findings: Sequence[DesignFinding],
    given: RequirementsInput | None = None,
    *,
    prior: Mapping[str, Any] | None = None,
    answered: Sequence[Mapping[str, Any]] = (),
    derived: bool = False,
    ai: Mapping[str, Any] | None = None,
    linked: Collection[str] = (),
) -> dict[str, Any]:
    """The requirements record of a request, as its revision stores it.

    ``linked`` (M7a) names the fed boards whose feeder link the revision's
    ``pp-design/3`` design records; their feeders get no
    ``inter_board_wiring_not_modelled`` open item. Empty for v1 and v2.

    Args:
        request: The project as entered.
        designed: Its design, or ``None`` where the design was refused.
        findings: The design's findings (for the assumption questions).
        given: The guided input; ``None`` records the request as a table.
        prior: The previous revision's record, whose refs, control needs
            and acknowledgements carry over where they still apply.
        answered: Questions answered into this request, each the question as
            it was asked plus its ``answer``, ``by_user_id``, ``by_name`` and ``at``.
        derived: Built for an older revision with no stored record.
        ai: The ``ai`` block of a proposal this save applies (M6b), its
            placements given the record's refs here. Without one, the
            previous record's block carries unchanged: the request's
            provenance, with its own timestamps and indices, never applied
            again.

        linked: The fed boards whose feeder link a v3 design records (M7a).

    Returns:
        The record, JSON-shaped.
    """
    intake = given.intake if given is not None else (prior or {}).get("intake", "table")
    source = "imported" if intake == "import" else "typed"
    board_refs = _board_refs(request, prior)
    by_name = {board.name: ref for board, ref in zip(request.boards, board_refs, strict=True)}
    boards: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    load_refs: list[list[str]] = []
    for board, ref in zip(request.boards, board_refs, strict=True):
        boards.append(
            {
                "ref": ref,
                "name": board.name,
                "fed_from_ref": by_name.get(board.fed_from) if board.fed_from else None,
            }
        )
        refs = _load_refs(ref, [load.description for load in board.loads], prior)
        load_refs.append(refs)
        loads += [
            {"ref": lref, "board_ref": ref, "description": load.description, "source": source}
            for load, lref in zip(board.loads, refs, strict=True)
        ]

    control = _control(request, board_refs, load_refs, given, prior)
    feeders = [
        {
            "ref": f"{ref}.feeder",
            "board_ref": ref,
            "supply_board_ref": by_name[board.fed_from],
            "source": "generated",
        }
        for board, ref in zip(request.boards, board_refs, strict=True)
        if board.fed_from is not None and board.fed_from in by_name
    ]
    open_items: list[dict[str, Any]] = [
        {
            "code": "control_not_designed",
            "subject": {
                "board_ref": item["board_ref"],
                "load_ref": item["load_ref"],
                "control_ref": item["ref"],
            },
        }
        for item in control
    ]
    # M7a: a feeder whose link a v3 revision records is not an open item; the
    # link's own findings say what of it is not set (none says it is complete).
    linked_refs = {by_name[name] for name in linked if name in by_name}
    open_items += [
        {
            "code": "inter_board_wiring_not_modelled",
            "subject": {"board_ref": f["board_ref"], "supply_board_ref": f["supply_board_ref"]},
        }
        for f in feeders
        if f["board_ref"] not in linked_refs
    ]
    questions = _questions(request, board_refs, load_refs, findings)
    asked = {q["id"] for q in questions}
    for done in answered:
        if done["id"] not in asked:
            questions.append({**done, "state": "answered"})
            asked.add(done["id"])
    for earlier in (prior or {}).get("questions", []):
        # An answer stays on the record of every later revision while the
        # request still holds the value answered; changed since (typed in the
        # table), the value is the table's, attributed to no one.
        if (
            earlier.get("state") == "answered"
            and earlier["id"] not in asked
            and _still_answered(request, board_refs, load_refs, earlier)
        ):
            questions.append(dict(earlier))
            asked.add(earlier["id"])
    # An acknowledgement carries over while the very question still stands
    # (same id, so same kind, field, finding, board and load) over the same
    # inputs (same basis): what was acknowledged is what is assumed now.
    bases = {q["id"]: q.get("basis") for q in questions if q.get("state") == "open"}
    acknowledgements = (
        []
        if derived
        else [
            dict(ack)
            for ack in (prior or {}).get("acknowledgements", [])
            if ack.get("basis") is not None and bases.get(ack["question_id"]) == ack["basis"]
        ]
    )
    block: Mapping[str, Any] | None = (
        ai_intake.with_refs(ai, boards, loads) if ai is not None else (prior or {}).get("ai")
    )
    if block is not None and not derived:
        open_items += ai_intake.open_items(block)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "intake": intake,
        "derived": derived,
        "boards": boards,
        "loads": loads,
        "feeders": feeders,
        "control": control,
        "questions": questions,
        "open_items": open_items,
        "trace": _trace(request, board_refs, load_refs, feeders, designed),
        "acknowledgements": acknowledgements,
    }
    if block is not None and not derived:
        record["ai"] = dict(block)
    if not derived:
        # M6e: resolutions of safety concerns carry forward; each counts on a
        # later revision only while its concern is still one there.
        carried = safety_review.carried_resolutions(prior)
        if carried:
            record["safety_resolutions"] = carried
    return apply_acknowledgements(record)


def apply_acknowledgements(record: dict[str, Any]) -> dict[str, Any]:
    """Each open question an acknowledgement names, set ``acknowledged`` by it (the latest)."""
    if record.get("derived"):
        return record
    latest = {ack["question_id"]: ack for ack in record.get("acknowledgements", [])}
    for question in record["questions"]:
        ack = latest.get(question["id"])
        if ack is not None and question["state"] != "answered":
            question.update(
                state="acknowledged",
                by_user_id=ack["by_user_id"],
                by_name=ack.get("by_name"),
                at=ack["at"],
            )
    return record


def _still_answered(
    request: ProjectDesignRequest,
    board_refs: Sequence[str],
    load_refs: Sequence[Sequence[str]],
    question: Mapping[str, Any],
) -> bool:
    """Whether the request still holds the value an answered question was given."""
    subject = question.get("subject") or {}
    try:
        b = list(board_refs).index(str(subject.get("board_ref") or ""))
    except ValueError:
        return False
    board = request.boards[b]
    field = question.get("field")
    value: Any
    if field == "fault_level_ka":
        value = board.supply.fault_level_ka
    elif field == "ze_ohm":
        value = board.supply.earth_loop_ohm
    elif field == "feeder_length_m":
        value = board.feeder_length_m
    elif field == "power_factor":
        try:
            i = list(load_refs[b]).index(str(subject.get("load_ref") or ""))
        except ValueError:
            return False
        value = board.loads[i].power_factor
    else:
        return False
    try:
        return value is not None and Decimal(str(value)) == Decimal(str(question.get("answer")))
    except InvalidOperation:
        return False


def _control(
    request: ProjectDesignRequest,
    board_refs: Sequence[str],
    load_refs: Sequence[Sequence[str]],
    given: RequirementsInput | None,
    prior: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """One control requirement per controlled load, with the needs the input gives."""
    needs: dict[tuple[int, int], list[str]] = {}
    if given is not None:
        for item in given.control:
            needs[(item.board_index, item.load_index)] = [need.value for need in item.needs]
    else:
        earlier = {c["load_ref"]: c["needs"] for c in (prior or {}).get("control", [])}
        for b, refs in enumerate(load_refs):
            for i, ref in enumerate(refs):
                if ref in earlier:
                    needs[(b, i)] = list(earlier[ref])
    out: list[dict[str, Any]] = []
    for b, board in enumerate(request.boards):
        count = 0
        for i, load in enumerate(board.loads):
            if not load.controlled:
                continue
            count += 1
            out.append(
                {
                    "ref": f"{board_refs[b]}.c{count}",
                    "board_ref": board_refs[b],
                    "load_ref": load_refs[b][i],
                    "needs": needs.get((b, i), []),
                    "status": "not_designed",
                }
            )
    return out


def _question(
    kind: str,
    field: str | None,
    code: str | None,
    board: str,
    board_ref: str,
    load_ref: str | None = None,
    *,
    basis: str,
) -> dict[str, Any]:
    return {
        "id": question_id(kind, field, code, board, load_ref or ""),
        "basis": basis,
        "kind": kind,
        "subject": {"board_ref": board_ref, "load_ref": load_ref},
        "field": field,
        "finding_code": code,
        "answerable": field in ANSWERABLE,
        "state": "open",
        "answer": None,
        "by_user_id": None,
        "by_name": None,
        "at": None,
    }


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _basis(board: Any, load: Any = None, *, part: str) -> str:
    """A digest of the inputs a question's assumption rests on.

    An acknowledgement carries to a later revision only while its question's
    basis is unchanged: the same question id over changed inputs (a motor's
    power, a load's kind) is a different assumption, open again.
    """
    shown = board.model_dump(mode="json")
    # The supply as an assumption rests on it: a value answered into it (its
    # fault level, its Ze) is its own question, and changes no other.
    supply = {
        k: v
        for k, v in (shown.get("supply") or {}).items()
        if k not in ("fault_level_ka", "earth_loop_ohm")
    }
    value: Any
    if part == "supply":
        value = {"supply": supply, "fed_from": shown.get("fed_from")}
    elif part == "feeder":
        value = {"fed_from": shown.get("fed_from"), "feeder_length_m": shown.get("feeder_length_m")}
    elif part == "load":
        value = {"supply": supply, "load": load.model_dump(mode="json")}
    else:
        value = {**shown, "supply": supply}
    return _digest(value)


def _questions(
    request: ProjectDesignRequest,
    board_refs: Sequence[str],
    load_refs: Sequence[Sequence[str]],
    findings: Sequence[DesignFinding],
) -> list[dict[str, Any]]:
    codes: dict[str, set[str]] = {}
    on_circuit: dict[tuple[str, str], set[str]] = {}
    for found in findings:
        if found.board_id is None:
            continue
        codes.setdefault(found.board_id, set()).add(found.code)
        if found.circuit_id is not None:
            on_circuit.setdefault((found.board_id, found.circuit_id), set()).add(found.code)
    out: list[dict[str, Any]] = []
    for b, board in enumerate(request.boards):
        ref = board_refs[b]
        here = codes.get(board.name, set())
        if board.fed_from is None:
            if board.supply.fault_level_ka is None:
                code = "no_fault_level" if "no_fault_level" in here else None
                out.append(
                    _question(
                        "missing_value",
                        "fault_level_ka",
                        code,
                        board.name,
                        ref,
                        basis=_basis(board, part="supply"),
                    )
                )
            if board.supply.earth_loop_ohm is None:
                code = "earth_fault_no_ze" if "earth_fault_no_ze" in here else None
                out.append(
                    _question(
                        "missing_value",
                        "ze_ohm",
                        code,
                        board.name,
                        ref,
                        basis=_basis(board, part="supply"),
                    )
                )
        elif board.feeder_length_m is None:
            out.append(
                _question(
                    "missing_value",
                    "feeder_length_m",
                    None,
                    board.name,
                    ref,
                    basis=_basis(board, part="feeder"),
                )
            )
        for i, load in enumerate(board.loads):
            lref = load_refs[b][i]
            circuit = on_circuit.get((board.name, f"c{i + 1}"), set())
            for code in MOTOR_CURRENT_CODES:
                if code in circuit:
                    out.append(
                        _question(
                            "assumption",
                            "motor_current_a",
                            code,
                            board.name,
                            ref,
                            lref,
                            basis=_basis(board, load, part="load"),
                        )
                    )
            if (
                "default_power_factor" in here
                and load.power_factor is None
                and load.load is not LoadKind.MOTOR
            ):
                out.append(
                    _question(
                        "assumption",
                        "power_factor",
                        "default_power_factor",
                        board.name,
                        ref,
                        lref,
                        basis=_basis(board, load, part="load"),
                    )
                )
        for code in ASSUMPTION_CODES:
            if code in here:
                part = "supply" if code == "fault_level_inherited" else "board"
                out.append(
                    _question(
                        "assumption", None, code, board.name, ref, basis=_basis(board, part=part)
                    )
                )
    return out


def _trace(
    request: ProjectDesignRequest,
    board_refs: Sequence[str],
    load_refs: Sequence[Sequence[str]],
    feeders: Sequence[Mapping[str, Any]],
    designed: DesignProject | None,
) -> list[dict[str, Any]]:
    """Each load to its circuit (``<board>/c<n>``), each feeder to the circuit feeding it."""
    boards = {board.id: board for board in designed.boards} if designed is not None else {}
    names = dict(zip(board_refs, (b.name for b in request.boards), strict=True))
    out: list[dict[str, Any]] = []
    for b, board in enumerate(request.boards):
        circuits = {c.id for c in boards[board.name].circuits} if board.name in boards else set()
        for i, lref in enumerate(load_refs[b]):
            cid = f"c{i + 1}"
            out.append(
                {
                    "requirement_ref": lref,
                    "design_ref": f"{board.name}/{cid}" if cid in circuits else None,
                }
            )
    for feeder in feeders:
        supply = boards.get(names[feeder["supply_board_ref"]])
        child = names[feeder["board_ref"]]
        circuit = next((c for c in supply.circuits if c.feeds == child), None) if supply else None
        out.append(
            {
                "requirement_ref": feeder["ref"],
                "design_ref": f"{supply.name}/{circuit.id}" if supply and circuit else None,
            }
        )
    return out


#: Who an acknowledgement or answer names when its account has neither a
#: display name nor an email: never empty, never invented.
UNKNOWN_ACCOUNT = "unknown account"


def _subject_text(record: Mapping[str, Any], subject: Mapping[str, Any]) -> str:
    """``board > load`` (printed with U+203A), or the board alone, by the record's names."""
    boards = {b["ref"]: b["name"] for b in record.get("boards", [])}
    loads = {load["ref"]: load["description"] for load in record.get("loads", [])}
    parts = [boards.get(subject.get("board_ref") or "", "")]
    if subject.get("load_ref"):
        parts.append(loads.get(subject["load_ref"], subject["load_ref"]))
    return " \u203a ".join(part for part in parts if part)


def acknowledged_lines(
    record: Mapping[str, Any] | None, name_of: Callable[[str | None], str] | None = None
) -> list[str]:
    """What a review export prints for each acknowledged question (and clarified mention).

    Args:
        record: The record.
        name_of: Who an acknowledgement names, from its ``by_user_id``; an
            exported document passes the display name or "a team member", so
            it never prints an email. ``None`` prints the recorded ``by_name``.
    """
    if not record or record.get("derived"):
        return []
    lines: list[str] = []
    for question in record.get("questions", []):
        if question.get("state") != "acknowledged":
            continue
        where = _subject_text(record, question.get("subject") or {})
        what = question.get("finding_code") or question.get("field") or question["kind"]
        lines.append(
            f"[ACKNOWLEDGED] Assumption acknowledged by "
            f"{name_of(question.get('by_user_id')) if name_of else (question.get('by_name') or UNKNOWN_ACCOUNT)} on "
            f"{(question.get('at') or '')[:10]} — not engineer-validated: {what}"
            + (f" — {where}" if where else "")
        )
    # Resolutions of safety concerns print from ``safety_review`` (M6e).
    return lines


def open_item_lines(record: Mapping[str, Any] | None) -> list[str]:
    """What a review export prints for each open item: what this design does not cover.

    Printed for a derived record too: the open items follow from the request,
    and a review document must never read as complete without them.
    """
    if not record:
        return []
    boards = {b["ref"]: b["name"] for b in record.get("boards", [])}
    lines: list[str] = []
    for item in record.get("open_items", []):
        subject = item.get("subject") or {}
        if item["code"] == "control_not_designed":
            lines.append(f"[OPEN] Control circuit not designed: {_subject_text(record, subject)}")
        elif item["code"] == "inter_board_wiring_not_modelled":
            supply = boards.get(subject.get("supply_board_ref") or "", "?")
            board = boards.get(subject.get("board_ref") or "", "?")
            lines.append(f"[OPEN] Board-to-board wiring not modelled: {supply} to {board}")
        else:
            line = ai_intake.open_item_line(record.get("ai"), item)
            if line is not None:
                lines.append(line)
    return lines
