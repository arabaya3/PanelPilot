"""Every safety concern a revision's approval must see resolved (M6d/M6e).

A review approval is held while any concern below is unresolved. Concerns are
re-derived on every check, never read from a stored list:

* **AI concerns** of the revision's own AI block: each life-safety or
  possibly-safety mention of the stored description and each item
  ``unsupported`` as (possibly) life-safety (``ai_intake.safety_targets``).
* **Request concerns**: the same keyword detector over the request itself,
  whatever the intake: the project information text, each board's name and
  each load's description (``request_life_safety`` /
  ``request_possibly_life_safety``).
* **Carried concerns**: every AI and request concern of an *earlier* revision
  of the project (re-derived from that revision's stored description, items
  and request) that the revision being approved no longer holds. A fresh AI
  proposal, a re-save with another intake or an edited request does not make
  an earlier concern go away: it is resolved on the revision approved, or it
  holds the approval. Each says on which revision it was first raised.

Resolutions are recorded on the revision approved (``safety_resolutions`` of
its record, carried forward to later revisions; an M6d ``ai.resolutions``
entry and a pre-M6d clarification count for its own block's concerns). Only
two exist: ``excluded_from_scope`` and ``false_positive``; what each may
resolve is :func:`refusal`. Detection cannot find every life-safety
requirement; nothing here is engineer validation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.design import ai_intake, safety_terms

EXCLUDED_FROM_SCOPE = ai_intake.EXCLUDED_FROM_SCOPE
FALSE_POSITIVE = ai_intake.FALSE_POSITIVE

REQUEST_LIFE_SAFETY = "request_life_safety"
REQUEST_POSSIBLY_SAFETY = "request_possibly_life_safety"

#: The project information fields read for safety terms.
INFO_FIELDS = ("name", "number", "customer", "consultant", "contractor")

Kind = Literal["mention", "item", "request"]
Level = Literal["life_safety", "possibly"]


#: A load of the request checked: ``(board, index, description, power_kw, kind)``.
Candidate = tuple[str, int, str, str, str]


@dataclass(frozen=True)
class Concern:
    """One safety concern of a revision, wherever it was raised.

    Attributes:
        kind: ``mention`` / ``item`` (an AI proposal's) or ``request``.
        target_id: The mention id, item id, or request concern id (``r`` and
            16 hex digits: level, place, text, term and offset).
        proposal_id: The AI proposal an AI concern belongs to; ``None`` for a
            request concern.
        level: ``life_safety`` or ``possibly``.
        code: The concern code (``ai_*`` or ``request_*``).
        label: The term or item text, as printed.
        start: Offset in its text.
        end: Offset after it.
        raised_on: The earliest revision it is a concern of.
        present: Whether the revision checked still holds it (its own AI
            block, or its own request); ``False`` for a carried concern.
        place: For a request concern: ``board``, ``load``, ``link`` (M7a: a feeder
            link's label or text attribute) or ``info.<field>``.
        where: For a request concern: the board, ``board > load`` or field, as printed.
        board: The board's name (a request board or load concern).
        load_index: The load's index on that board (a request load concern).
    """

    kind: Kind
    target_id: str
    proposal_id: str | None
    level: Level
    code: str
    label: str
    start: int
    end: int
    raised_on: int
    present: bool
    place: str | None = None
    where: str | None = None
    board: str | None = None
    load_index: int | None = None
    overlaps_life_safety_item: bool = field(default=False, compare=False)
    #: The drawn rows it is tied to, as ``(board, load index, power_kw, kind)``
    #: of the revision that raised it: a request load, or rows an AI item
    #: overlapping its span was placed on.
    rows: tuple[tuple[str, int, str, str], ...] = field(default=(), compare=False)
    #: A carried concern whose row the request checked still draws (same
    #: board and position, or same board, power and kind): not excludable.
    still_in_request: bool = field(default=False, compare=False)
    #: The flagged load's power (``power_kw``, normalised), "" when unknown.
    power: str = field(default="", compare=False)
    #: A carried life-safety concern whose exclusion needs the per-load
    #: confirmation, and the request's candidate loads it must list.
    needs_confirmation: bool = field(default=False, compare=False)
    candidates: tuple[Candidate, ...] = field(default=(), compare=False)

    @property
    def key(self) -> tuple[str, str, str]:
        """``(kind, target_id, proposal_id or "")``: how a resolution names it."""
        return (self.kind, self.target_id, self.proposal_id or "")


def _number(value: Any) -> str:
    try:
        return str(Decimal(str(value)).normalize())
    except (InvalidOperation, ValueError):
        return str(value or "")


def _row(board: str, index: int, load: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (board, index, _number(load.get("power_kw")), str(load.get("load") or ""))


def load_rows(request: Mapping[str, Any] | None) -> list[Candidate]:
    """Every load of a request as ``(board, index, description, power_kw, kind)``."""
    out: list[Candidate] = []
    if not isinstance(request, Mapping):
        return out
    for board in request.get("boards") or []:
        if not isinstance(board, Mapping):
            continue
        for index, load in enumerate(board.get("loads") or []):
            if isinstance(load, Mapping):
                out.append(
                    (
                        str(board.get("name") or ""),
                        index,
                        str(load.get("description") or ""),
                        _number(load.get("power_kw")),
                        str(load.get("load") or ""),
                    )
                )
    return out


def candidates_for(power: str, request: Mapping[str, Any] | None) -> tuple[Candidate, ...]:
    """The loads a person must confirm are not the flagged equipment before excluding it.

    Every load on every board of the request checked (M6f, owner decision):
    deliberately exhaustive. Kind and power filters were tried and leaked (a
    pump split into small loads of another kind, a pump re-powered between
    revisions), so no load is left out of the question. ``power`` is kept
    for the caller's record and no longer filters.
    """
    del power
    return tuple(load_rows(request))


def drawn_rows(request: Mapping[str, Any] | None) -> list[tuple[str, int, str, str]]:
    """Every load row of a request as ``(board, index, power_kw, kind)``."""
    out: list[tuple[str, int, str, str]] = []
    if not isinstance(request, Mapping):
        return out
    for board in request.get("boards") or []:
        if not isinstance(board, Mapping):
            continue
        for index, load in enumerate(board.get("loads") or []):
            if isinstance(load, Mapping):
                out.append(_row(str(board.get("name") or ""), index, load))
    return out


def _still_drawn(
    rows: Iterable[tuple[str, int, str, str]], now: Sequence[tuple[str, int, str, str]]
) -> bool:
    """Whether any of ``rows`` matches a row drawn now: same board and position, or same board, power and kind."""
    for board, index, power, kind in rows:
        for b, i, p, k in now:
            if b == board and (i == index or (p == power and k == kind and p)):
                return True
    return False


#: The text attributes of a feeder link the scanner reads (M7a).
LINK_TEXT_FIELDS = ("material", "insulation", "number", "colour")


def _request_id(level: str, place: str, text: str, term: str, start: int, at: str = "") -> str:
    """A request concern's id: level, place, where (board and load position), text, term, offset.

    ``at`` (M6f) is the board's name and the load's index for a load and the board's name for a
    board, so two same-named loads on two boards, or two rows of one board,
    are two concerns. An unchanged row keeps its id. Resolutions recorded
    under the earlier id (no position) name no concern now: they stop
    counting and the approval is held until it is resolved again.
    """
    norm = " ".join(text.casefold().split())
    digest = hashlib.sha256(f"{level}\x00{place}\x00{at}\x00{norm}\x00{term}\x00{start}".encode())
    return "r" + digest.hexdigest()[:16]


def request_concerns(request: Mapping[str, Any] | None, number: int) -> list[Concern]:
    """The request's own safety terms: project information, board names, load descriptions."""
    if not isinstance(request, Mapping):
        return []
    out: list[Concern] = []

    def scan(
        text: Any,
        place: str,
        where: str,
        board: str | None = None,
        load_index: int | None = None,
        rows: tuple[tuple[str, int, str, str], ...] = (),
        power: str = "",
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        at = f"{board}\x00{load_index}" if load_index is not None else (board or "")
        for m in safety_terms.mentions(text):
            out.append(
                Concern(
                    kind="request",
                    target_id=_request_id(m.level, place, text, m.term, m.start, at),
                    proposal_id=None,
                    level=m.level,
                    code=(
                        REQUEST_LIFE_SAFETY if m.level == "life_safety" else REQUEST_POSSIBLY_SAFETY
                    ),
                    label=m.term,
                    start=m.start,
                    end=m.end,
                    raised_on=number,
                    present=True,
                    place=place,
                    where=where,
                    board=board,
                    load_index=load_index,
                    rows=rows,
                    power=power,
                )
            )

    info = request.get("info")
    if isinstance(info, Mapping):
        for name in INFO_FIELDS:
            scan(info.get(name), f"info.{name}", f"project {name}")
    for board in request.get("boards") or []:
        if not isinstance(board, Mapping):
            continue
        name = str(board.get("name") or "")
        scan(board.get("name"), "board", name, board=name)
        for index, load in enumerate(board.get("loads") or []):
            if isinstance(load, Mapping):
                description = str(load.get("description") or "")
                scan(
                    load.get("description"),
                    "load",
                    f"{name} \u203a {description}",
                    board=name,
                    load_index=index,
                    rows=(_row(name, index, load),),
                    power=_number(load.get("power_kw")) if load.get("power_kw") is not None else "",
                )
    # M7a: every text of a feeder link (the ends' labels, the text attributes)
    # is request text printed on the review documents, scanned like a load's.
    for index, link in enumerate(request.get("feeder_links") or []):
        if not isinstance(link, Mapping):
            continue
        fed = str(link.get("fed_board") or "")
        texts: list[tuple[str, Any]] = []
        for end in ("from_end", "to_end"):
            given = link.get(end)
            if isinstance(given, Mapping):
                texts.append((f"{end}.label", given.get("label")))
        attributes = link.get("attributes")
        if isinstance(attributes, Mapping):
            texts += [(name, attributes.get(name)) for name in LINK_TEXT_FIELDS]
        for part, text in texts:
            scan(
                text,
                "link",
                f"feeder link to {fed} \u203a {part}",
                board=f"{fed}\x00{index}\x00{part}",
            )
    return out


def ai_concerns(
    ai: Mapping[str, Any] | None, number: int, request: Mapping[str, Any] | None = None
) -> list[Concern]:
    """An AI block's concerns (``ai_intake.safety_targets``), tagged with its proposal.

    Each is tied to the rows of ``request`` (the revision's own) that an AI
    item overlapping its span was placed on.
    """
    if not ai:
        return []
    proposal = str((ai.get("proposal") or {}).get("id") or "")
    targets = ai_intake.safety_targets(ai)
    items = {str(i["id"]): i for i in (ai.get("proposal") or {}).get("items", [])}
    boards = [b for b in ((request or {}).get("boards") or []) if isinstance(b, Mapping)]
    placed: list[tuple[int, int, tuple[str, int, str, str]]] = []
    for placement in ai.get("placements", []) or []:
        item = items.get(str(placement.get("item_id")))
        li = placement.get("load_index")
        bi = int(placement.get("board_index", -1))
        if item is None or li is None or not 0 <= bi < len(boards):
            continue
        loads = boards[bi].get("loads") or []
        if not 0 <= int(li) < len(loads):
            continue
        source = item.get("source") or {}
        placed.append(
            (
                int(source.get("start", 0)),
                int(source.get("end", 0)),
                _row(str(boards[bi].get("name") or ""), int(li), loads[int(li)]),
            )
        )
    out = []
    for t in targets:
        overlaps = t.kind == "mention" and ai_intake.false_positive_refusal(ai, t) is not None
        rows = tuple(row for a, b, row in placed if a < t.end and t.start < b)
        item = items.get(t.target_id) if t.kind == "item" else None
        stated = ((item or {}).get("value") or {}).get("power_kw")
        if stated is None and item is not None:
            # A server-made unsupported item keeps only text: its quote may state the power.
            match = re.search(r"(\d+(?:\.\d+)?)\s*kW", str(item.get("quote") or ""), re.I)
            stated = match.group(1) if match else None
        power = _number(stated) if stated is not None else next((r[2] for r in rows if r[2]), "")
        out.append(
            Concern(
                kind=t.kind,
                target_id=t.target_id,
                proposal_id=proposal,
                level=t.level,
                code=t.code,
                label=t.label,
                start=t.start,
                end=t.end,
                raised_on=number,
                present=True,
                overlaps_life_safety_item=overlaps,
                rows=rows,
                power=power,
            )
        )
    return out


@dataclass(frozen=True)
class Stored:
    """What one revision holds, as stored: its number, request and requirements record."""

    number: int
    request: Mapping[str, Any] | None
    record: Mapping[str, Any] | None


def concerns(current: Stored, earlier: Iterable[Stored]) -> list[Concern]:
    """Every concern of ``current``: its own, then those carried from earlier revisions.

    A concern held by ``current`` is ``present`` and dated by the earliest
    revision that raised it; one only an earlier revision raised is carried.
    """
    own_ai = ai_concerns((current.record or {}).get("ai"), current.number, current.request)
    now = drawn_rows(current.request)
    own_req = request_concerns(current.request, current.number)
    first: dict[tuple[str, str, str], int] = {}
    carried: dict[tuple[str, str, str], Concern] = {}
    every_rows: dict[tuple[str, str, str], set[tuple[str, int, str, str]]] = {}
    for stored in sorted(earlier, key=lambda s: s.number):
        if stored.number >= current.number:
            continue
        for c in (
            *ai_concerns((stored.record or {}).get("ai"), stored.number, stored.request),
            *request_concerns(stored.request, stored.number),
        ):
            first.setdefault(c.key, stored.number)
            # Every revision's copy: still_in_request is held to the rows the
            # flagged load had in each one, and the latest copy is kept (M6f).
            every_rows.setdefault(c.key, set()).update(c.rows)
            carried[c.key] = c
    out: list[Concern] = []
    seen: set[tuple[str, str, str]] = set()
    for c in (*own_ai, *own_req):
        if c.key in seen:
            continue
        seen.add(c.key)
        own = _dated(c, first.get(c.key, c.raised_on), True)
        # M6f re-check: the revision's own life-safety concerns need the same
        # every-load confirmation as carried ones (the pump may be typed in
        # under another name); project information too (M6f: a project named
        # "Fire pump house" may draw the pump under another name).
        needs = c.level == "life_safety"
        rows = set(c.rows) | every_rows.get(c.key, set())
        out.append(
            Concern(
                **{
                    **own.__dict__,
                    "rows": tuple(sorted(rows)),
                    "still_in_request": c.kind != "request" and _still_drawn(rows, now),
                    "needs_confirmation": needs,
                    "candidates": candidates_for(c.power, current.request) if needs else (),
                }
            )
        )
    for key, c in carried.items():
        if key not in seen:
            seen.add(key)
            moved = _dated(c, first[key], False)
            needs = c.level == "life_safety"
            out.append(
                Concern(
                    **{
                        **moved.__dict__,
                        "rows": tuple(sorted(every_rows.get(key, set(c.rows)))),
                        "still_in_request": _still_drawn(every_rows.get(key, set(c.rows)), now),
                        "needs_confirmation": needs,
                        "candidates": candidates_for(c.power, current.request) if needs else (),
                    }
                )
            )
    return out


def _dated(c: Concern, raised_on: int, present: bool) -> Concern:
    return Concern(
        **{**c.__dict__, "raised_on": raised_on, "present": present},
    )


def refusal(concern: Concern, resolution: str) -> str | None:
    """Why ``resolution`` may not resolve ``concern``, or ``None``.

    * A board or load of the request being approved that names life-safety
      equipment has **no** resolution (``in_request``): it stays in the
      drawings as an ordinary load, so excluding its safety function would
      leave a design that draws it while saying it is not designed. It is
      removed from the request (a new revision), after which the carried
      concern is excluded from scope. A possibly-safety board or load still in
      the request may only be a false positive (``in_request``).
    * A carried board or load concern (an earlier request's load, or rows an
      earlier AI item was placed on) is not excluded while the request
      checked still draws a matching row (``still_in_request``): same board
      and same position, or same board, power and kind. Renaming the load
      is not removing it. Deliberately over-inclusive: a row moved into the
      removed load's position matches too.
    * ``false_positive``: never an AI item (``item``); never a life-safety
      request concern (``life_safety``); a life-safety AI mention only when
      no item of its proposal is life-safety equipment from an overlapping
      span (``life_safety_item_overlaps``).
    """
    in_design = (
        concern.kind == "request" and concern.present and concern.place in ("board", "load", "link")
    )
    if resolution == EXCLUDED_FROM_SCOPE:
        if in_design:
            return "in_request"
        # Renamed, not removed: the row it was raised for is still drawn.
        return "still_in_request" if concern.still_in_request else None
    if concern.kind == "item":
        return "item"
    if concern.kind == "request":
        return "life_safety" if concern.level == "life_safety" else None
    if concern.level == "life_safety" and concern.overlaps_life_safety_item:
        return "life_safety_item_overlaps"
    return None


def _entries(record: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Every recorded resolution of a record, in order, each naming its proposal."""
    if not record:
        return []
    ai = record.get("ai") or {}
    current = str((ai.get("proposal") or {}).get("id") or "")
    out: list[dict[str, Any]] = []
    for entry in record.get("safety_resolutions", []) or []:
        out.append(dict(entry))
    for entry in ai.get("resolutions", []) or []:
        out.append({**entry, "proposal_id": entry.get("proposal_id") or current})
    # Pre-M6d clarifications of this block, under their own rule (read only:
    # the route is retired).
    if ai and ai_intake.safety_concern(ai) is None:
        for entry in ai.get("clarifications", []) or []:
            if entry.get("statement") != ai_intake.NOT_LIFE_SAFETY:
                continue
            out.append(
                {
                    **entry,
                    "target_kind": "mention",
                    "target_id": entry.get("mention_id"),
                    "proposal_id": current,
                    "resolution": FALSE_POSITIVE,
                    "legacy": True,
                }
            )
    return out


def resolutions_in_force(
    found: Sequence[Concern], record: Mapping[str, Any] | None
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The first allowed resolution recorded for each concern, by key."""
    by_key = {c.key: c for c in found}
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in _entries(record):
        key = (
            str(entry.get("target_kind", "")),
            str(entry.get("target_id", "")),
            str(entry.get("proposal_id") or ""),
        )
        concern = by_key.get(key)
        if concern is None or key in out:
            continue
        if entry.get("legacy") and concern.level != "possibly":
            continue
        if entry.get("resolution") not in (EXCLUDED_FROM_SCOPE, FALSE_POSITIVE):
            continue
        if refusal(concern, str(entry["resolution"])) is not None:
            continue
        if (
            entry.get("resolution") == EXCLUDED_FROM_SCOPE
            and concern.needs_confirmation
            and confirmed_set(entry) != set(concern.candidates)
        ):
            # The request changed since the confirmation: confirm again.
            continue
        out[key] = entry
    return out


def unresolved(found: Sequence[Concern], record: Mapping[str, Any] | None) -> list[Concern]:
    """The concerns no resolution in force covers: each holds the approval.

    Every concern needs its own resolution: a false positive of a mention
    never resolves an item.
    """
    resolved = resolutions_in_force(found, record)
    return [c for c in found if c.key not in resolved]


def carried_resolutions(prior: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """What a new revision's record carries of the previous one's resolutions."""
    if not prior:
        return []
    return [
        {k: v for k, v in e.items() if k != "legacy"}
        for e in _entries(prior)
        if not e.get("legacy")
    ]


def _place_text(c: Concern) -> str:
    if c.kind == "request":
        return f" in the request ({c.where})"
    return " in the AI intake description" if c.kind == "mention" else " (AI intake item)"


def open_lines(found: Sequence[Concern], record: Mapping[str, Any] | None) -> list[str]:
    """``[OPEN]`` lines for the unresolved concerns the record's open items do not print.

    The record's own AI mentions and unsupported items print from its open
    items; request concerns and carried concerns print here.
    """
    lines = []
    for c in unresolved(found, record):
        if c.present and c.kind != "request":
            continue
        raised = "" if c.present else f" (raised on revision {c.raised_on})"
        what = (
            "names fire-pump or life-safety equipment"
            if c.level == "life_safety"
            else "may refer to life-safety equipment"
        )
        lines.append(
            f"[OPEN] Safety concern{raised}: '{c.label}'{_place_text(c)} {what}; "
            "not designed or reviewed; needs a second member's resolution before approval"
        )
    return lines


def confirmed_set(entry: Mapping[str, Any]) -> set[Candidate]:
    """The loads an exclusion's resolver confirmed are not the equipment, as candidates."""
    out: set[Candidate] = set()
    for load in entry.get("confirmed_loads") or []:
        out.add(
            (
                str(load.get("board") or ""),
                int(load.get("load_index", -1)),
                str(load.get("description") or ""),
                str(load.get("power_kw") or ""),
                str(load.get("kind") or ""),
            )
        )
    return out


def _confirmed_text(entry: Mapping[str, Any], who: str) -> str:
    loads = entry.get("confirmed_loads")
    if loads is None:
        return ""
    listed = (
        "; ".join(
            f"{x.get('board')} \u203a {x.get('description') or '?'}"
            + (f" ({x.get('power_kw')} kW)" if x.get("power_kw") else "")
            for x in loads
        )
        or "none drawn"
    )
    return f"; {who} confirmed these loads are not that equipment: {listed}"


def resolution_lines(
    found: Sequence[Concern],
    record: Mapping[str, Any] | None,
    name_of: Callable[[str | None], str] | None = None,
) -> list[str]:
    """What a review export (and the approval scope) prints for each resolution in force."""
    lines = []
    by_key = {c.key: c for c in found}
    for key, entry in resolutions_in_force(found, record).items():
        c = by_key[key]
        who = (
            name_of(entry.get("by_user_id"))
            if name_of
            else (entry.get("by_name") or "unknown account")
        )
        when = str(entry.get("at") or "")[:10]
        reason = " ".join(str(entry.get("reason", "")).split())[:500]
        raised = "" if c.present else f" (raised on revision {c.raised_on})"
        if entry.get("resolution") == EXCLUDED_FROM_SCOPE:
            lines.append(
                f"[EXCLUDED FROM SCOPE] '{c.label}'{raised} — not designed or reviewed here: "
                f"{reason} (recorded by {who} on {when}){_confirmed_text(entry, who)}"
                " — not engineer-validated"
            )
        else:
            lines.append(
                f"[FALSE POSITIVE] '{c.label}'{raised} judged not a life-safety requirement by "
                f"{who} on {when}: {reason} — not engineer-validated"
            )
    return lines
