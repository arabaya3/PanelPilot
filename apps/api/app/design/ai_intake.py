"""AI-assisted intake (M6b): the server's checks of a model's proposal, and what a save keeps.

The model proposes entries for the guided requirements step and designs
nothing (``docs/architecture/m6b-ai-intake.md``). Its output is untrusted:
this module parses it strictly, checks every item against the description it
claims to quote, and moves an item failing a check to ``server_rejected``
with a reason, never dropping it silently. Nothing here reaches storage or a
model; the provider call is ``app.ai.intake_proposer`` and the table
``app.domain.design_ai``.

The checks:

* **Spans.** Every item carries ``source = {start, end}`` into the stored
  description. The span must lie in the input, be non-empty and at most
  :data:`MAX_SPAN` characters; the stored ``quote`` is always cut from the
  stored input, never taken from the model.
* **No invented values.** Each numeric field (``power_kw``, ``power_factor``,
  ``length_m``, ``phases``) must appear as a literal number in the item's
  quote (``value_not_in_text``); a phase count may also be written as
  "single"/"three" phase. Nothing is converted (watts, horsepower): a
  figure stated in another unit is not a kW figure the text states.
* **Kinds and needs** are the M6a enums (a load is never a ``sub_board``);
  every reference (a load's board, a board's supply, a control need's load,
  a question's subject) resolves to a kept item or an existing board, else
  ``reference_unresolved``.
* **Out of scope.** Fire-pump and life-safety equipment is always turned
  into an ``unsupported`` item, whatever kind the model gave it.
* **Schema violations** (a key not listed for its kind, such as
  ``approved``, ``validated``, ``author`` or ``tenant``; a missing envelope;
  more than :data:`MAX_ITEMS`) refuse the whole output: :class:`ProposalMalformed`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.design import snapshot
from app.models.schemas.design import ControlNeed, LoadKind, ProjectDesignRequest

SCHEMA = "pp-ai-proposal/1"

#: The prompt this code was written against; stored with every proposal.
PROMPT_VERSION = "m6b-intake-2026.11.04.1"

#: The longest description sent to a provider.
MAX_INPUT_CHARS = 8000

#: The most items one output may carry.
MAX_ITEMS = 200

#: The longest string any output field may carry.
MAX_STRING = 200

#: Above this share of items failing their span check, the output is malformed.
MALFORMED_SPAN_SHARE = 0.5

#: The longest span one item may quote.
MAX_SPAN = 400

#: Fields that set a number, checked against the item's quote.
NUMERIC_FIELDS = ("power_kw", "power_factor", "length_m", "phases")

#: The load kinds the editor offers a proposal (never a typed sub-board).
PROPOSABLE_LOAD_KINDS = tuple(kind.value for kind in LoadKind if kind is not LoadKind.SUB_BOARD)

OUT_OF_SCOPE_REASON = "fire-pump or life-safety equipment is out of scope"

_LIFE_SAFETY = re.compile(
    r"fire[\s_-]*(?:pump|alarm|main|hose|fight|m[ae]n'?s?[\s_-]*lift|service|damper|curtain)|"
    r"jockey[\s_-]*pump|sprinkler|hydrant|deluge|drencher|foam[\s_-]*(?:pump|system)|"
    r"smoke[\s_-]*(?:extract|exhaust|control|vent|damper|detect|fan)|"
    r"emergency[\s_-]*(?:light|lighting|luminaire|exit)|exit[\s_-]*sign|"
    r"life[\s_-]*safety|stair(?:case|well)?[\s_-]*pressuri[sz]|evacuation|"
    # Arabic: fire, emergency, sprinklers, extinguishing, hydrant, smoke.
    "\u062d\u0631\u064a\u0642|\u0637\u0648\u0627\u0631\u0626|\u0631\u0634\u0627\u0634|"
    "\u0625\u0637\u0641\u0627\u0621|\u0627\u0637\u0641\u0627\u0621|\u0647\u064a\u062f\u0631\u0627\u0646\u062a|"
    "\u062f\u062e\u0627\u0646|"
    # Hebrew: fire (the word alone: it is inside many words), sprinkler(s),
    # emergency, smoke detector, extinguishing, hydrant, smoke.
    "(?<!\\w)\u05d0\u05e9(?!\\w)|\u05de\u05ea\u05d6|\u05e1\u05e4\u05e8\u05d9\u05e0\u05e7\u05dc\u05e8|"
    "\u05d7\u05d9\u05e8\u05d5\u05dd|\u05d2\u05dc\u05d0\u05d9 \u05e2\u05e9\u05df|\u05db\u05d9\u05d1\u05d5\u05d9|"
    "\u05d4\u05d9\u05d3\u05e8\u05e0\u05d8|\u05e2\u05e9\u05df",
    re.IGNORECASE,
)

#: Where a description's sentences end: a full stop, semicolon, question or
#: exclamation mark followed by space (not "5.5"), or a line break.
_SENTENCE_END = re.compile("(?<=[.;!?\u061b\u061f])\\s+|\\n+")

#: An identifier tag: letters then digits ("P-7", "AHU2", "FP01").
_TAG = re.compile(r"(?<![\w-])[A-Za-z]{1,6}-?\d{1,4}[A-Za-z]?(?![\w-])")

PRODUCT_REASON = "product_number"

_BRANDS = (
    r"abb|siemens|schneider|legrand|eaton|hager|mitsubishi|omron|allen[\s-]*bradley|"
    r"danfoss|telemecanique|chint|ls|ge|weg|wago|phoenix|rittal|finder|lovato|socomec"
)

#: A brand followed by a code holding letters and digits ("ABB S203").
_BRANDED_CODE = re.compile(
    rf"\b(?:{_BRANDS})\b\s+(?=[\w./-]*\d)(?=[\w./-]*[a-z])[\w./-]{{2,24}}", re.IGNORECASE
)
#: An unbranded catalogue code: letters and at least three digits in one
#: unhyphenated token ("S203", "3RT2015", "LC1D18" has three digits too).
_CODE = re.compile(r"(?<![\w-])(?=\w*[A-Za-z])(?=(?:\w*\d){3})[A-Za-z0-9]{4,20}(?![\w-])")
#: ...except a number with its unit ("5500W", "400V", "1200mm").
_NUMBER_WITH_UNIT = re.compile(
    r"^\d+(?:kw|w|kva|va|v|a|ma|ka|m|mm|mm2|hz|hp|rpm|kg|btu|lm|lux|k)$", re.IGNORECASE
)


def product_number(text: str) -> bool:
    """Whether ``text`` names a manufacturer's part or catalogue number.

    Conservative: a known brand followed by a letters-and-digits code, or an
    unhyphenated token of letters and at least three digits that is not a
    number with a unit. A tag such as "P-7", "DB-1" or "AHU2" is not one.
    """
    if _BRANDED_CODE.search(text):
        return True
    return any(not _NUMBER_WITH_UNIT.match(m.group()) for m in _CODE.finditer(text))


def _sentences(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        out.append((start, match.start()))
        start = match.end()
    out.append((start, len(text)))
    return out


def context_of(text: str, span: tuple[int, int], names: set[str]) -> str:
    """The sentences a span stands in, and every other sentence naming one of its tags.

    The model chooses the span: "Pump P-7 5.5 kW" can leave out "P-7 is the
    fire pump". A tag is an identifier in the span ("P-7") that is not a
    board's name, so a board's other loads are not drawn in.
    """
    sentences = _sentences(text)
    own = [(a, b) for a, b in sentences if a < span[1] and span[0] < b] or [span]
    tags = {
        m.group().lower()
        for m in _TAG.finditer(text[span[0] : span[1]])
        if m.group().lower() not in names
    }
    parts = [text[a:b] for a, b in own]
    for a, b in sentences:
        if (a, b) in own or not tags:
            continue
        words = {m.group().lower() for m in _TAG.finditer(text[a:b])}
        if words & tags:
            parts.append(text[a:b])
    return " ".join(parts)


def life_safety(text: str) -> bool:
    """Whether ``text`` names fire-pump or life-safety equipment (en/ar/he)."""
    return _LIFE_SAFETY.search(text) is not None


_NUMBER = re.compile(r"(?<![\d.,])\d*[.,]?\d+(?![\d])")
_ARABIC_DIGITS = str.maketrans(
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789"
)

#: The unit or cue each numeric field must be written with (F2): a positive
#: match, not a deny-list.
_AFTER = {
    # kW: "kW", "kilowatt(s)", and the Arabic and Hebrew words and abbreviations.
    "power_kw": re.compile(
        "\\s*(?:k\\s?w(?![a-z])|kilo[\\s-]?watts?\\b|"
        "\u0643\u064a\u0644\u0648\\s?\u0648\u0627\u0637|\u0643\\.\u0648|"
        '\u05e7\u05d9\u05dc\u05d5\u05d5\u05d0\u05d8|\u05e7\u05d5"\u05d8|\u05e7\u05d5\u05f4\u05d8)',
        re.IGNORECASE,
    ),
    # Metres: "m", "metre(s)", "meter(s)", and the Arabic and Hebrew words.
    "length_m": re.compile(
        "\\s*(?:m(?![a-z0-9\u00b2])|metres?\\b|meters?\\b|"
        "\u0645\u062a\u0631|\u0645(?!\\w)|\u05de\u05d8\u05e8|\u05de'(?!\\w))",
        re.IGNORECASE,
    ),
    # Phases: "3-phase", "3 ph", "3\u00d8", and the Arabic and Hebrew words.
    "phases": re.compile(
        "\\s*-?\\s*(?:phases?\\b|ph\\b|\u00f8|\u03c6|\u0444|"
        "\u0641\u0627\u0632|\u0637\u0648\u0631|\u05e4\u05d0\u05d6)",
        re.IGNORECASE,
    ),
}
#: A power factor is written after its cue: "pf 0.85", "cos φ 0.8", "power factor of 0.9".
_PF_CUE = re.compile(
    "(?:p\\.?\\s?f\\.?|cos\\s*(?:\u03c6|\u03d5|phi|\u00f8)|power[\\s-]*factor|"
    "\u0645\u0639\u0627\u0645\u0644\\s+\u0627\u0644\u0642\u062f\u0631\u0629|"
    "\u05de\u05e7\u05d3\u05dd\\s+\u05d4\u05e1\u05e4\u05e7)"
    "\\s*(?:of|is|=|:|\u2248|~)?\\s*$",
    re.IGNORECASE,
)
_PHASE_WORDS = {
    1: re.compile(r"\b(?:single|one|mono)[\s_-]*phase", re.IGNORECASE),
    3: re.compile(r"\b(?:three|tri)[\s_-]*phase", re.IGNORECASE),
}


class ProposalMalformed(Exception):  # noqa: N818 - a refusal of the output, not a bug
    """The output as a whole is unusable: not the envelope, a schema violation, bad spans en masse."""


# --------------------------------------------------------------------------- #
# Strict models of what a model may say
# --------------------------------------------------------------------------- #


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoardValue(_Strict):
    """A proposed board: its name, and the board feeding it, if the text says."""

    name: str = Field(min_length=1, max_length=40)
    fed_from: str | None = Field(default=None, max_length=40)


class LoadValue(_Strict):
    """A proposed load; every number only where its quote states it."""

    board: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=MAX_STRING)
    kind: Literal[
        "lighting",
        "socket",
        "air_conditioning",
        "water_heater",
        "motor",
        "fan",
        "kitchen",
        "lift",
        "control",
        "data",
        "spare",
        "other",
    ]
    power_kw: Decimal | None = Field(default=None, gt=0, le=10000)
    phases: Literal[1, 3] | None = None
    power_factor: Decimal | None = Field(default=None, gt=0, le=1)
    length_m: Decimal | None = Field(default=None, gt=0, le=10000)
    controlled: bool | None = None


class ControlNeedValue(_Strict):
    """One control need of a proposed load (by its item id)."""

    load: str = Field(min_length=1, max_length=40)
    need: ControlNeed


class QuestionValue(_Strict):
    """Something the description leaves missing, ambiguous or contradictory."""

    text: str = Field(min_length=1, max_length=MAX_STRING)
    about: str | None = Field(default=None, max_length=40)
    reason: Literal["missing", "ambiguous", "contradictory"]


class UnsupportedValue(_Strict):
    """Something the request cannot hold (a part number, a cable spec, life-safety equipment)."""

    text: str = Field(min_length=1, max_length=MAX_STRING)
    reason: str = Field(min_length=1, max_length=MAX_STRING)


VALUE_MODELS: dict[str, type[_Strict]] = {
    "board": BoardValue,
    "load": LoadValue,
    "control_need": ControlNeedValue,
    "question": QuestionValue,
    "unsupported": UnsupportedValue,
}

#: Kinds a user may edit (a question or an unsupported item is accepted or rejected).
EDITABLE_KINDS = ("board", "load", "control_need")

#: Kinds a save places into the request.
PLACEABLE_KINDS = ("board", "load", "control_need")


class _Span(_Strict):
    start: int
    end: int


class _RawItem(_Strict):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.\-]+$")
    kind: str = Field(min_length=1, max_length=40)
    source: _Span
    value: dict[str, Any]


class _RawOutput(_Strict):
    items: list[_RawItem] = Field(max_length=MAX_ITEMS)


class ToolBoard(_Strict):
    """Tool schema of a board item."""

    id: str
    kind: Literal["board"]
    source: _Span
    value: BoardValue


class ToolLoad(_Strict):
    """Tool schema of a load item."""

    id: str
    kind: Literal["load"]
    source: _Span
    value: LoadValue


class ToolControlNeed(_Strict):
    """Tool schema of a control need item."""

    id: str
    kind: Literal["control_need"]
    source: _Span
    value: ControlNeedValue


class ToolQuestion(_Strict):
    """Tool schema of a question item."""

    id: str
    kind: Literal["question"]
    source: _Span
    value: QuestionValue


class ToolUnsupported(_Strict):
    """Tool schema of an unsupported item."""

    id: str
    kind: Literal["unsupported"]
    source: _Span
    value: UnsupportedValue


class ToolOutput(_Strict):
    """What the forced tool call returns: the items, each quoting its span."""

    items: list[ToolBoard | ToolLoad | ToolControlNeed | ToolQuestion | ToolUnsupported] = Field(
        max_length=MAX_ITEMS
    )


# --------------------------------------------------------------------------- #
# Checking an output
# --------------------------------------------------------------------------- #


def input_sha256(text: str) -> str:
    """The SHA-256 of the description as stored (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def existing_boards(draft: Mapping[str, Any] | None) -> list[str]:
    """The board names of the editor's draft, read defensively; nothing else of it is used."""
    boards = (draft or {}).get("boards")
    if not isinstance(boards, list):
        return []
    names: list[str] = []
    for board in boards[:20]:
        name = board.get("name") if isinstance(board, dict) else None
        if isinstance(name, str) and 0 < len(name.strip()) <= 40 and name not in names:
            names.append(name)
    return names


def _numbers(text: str) -> list[tuple[Decimal, int, int]]:
    found: list[tuple[Decimal, int, int]] = []
    plain = text.translate(_ARABIC_DIGITS)
    for match in _NUMBER.finditer(plain):
        literal = match.group()
        # "1,200" is a thousands separator; "7,5" a decimal comma.
        head, _, tail = literal.partition(",")
        literal = head + tail if len(tail) == 3 and head else literal.replace(",", ".")
        try:
            found.append((Decimal(literal), match.start(), match.end()))
        except InvalidOperation:  # pragma: no cover - the pattern is a number
            continue
    return found


def literal_span(field: str, value: Any, quote: str) -> tuple[int, int] | None:
    """Where in ``quote`` the number ``value`` of ``field`` is written with its unit, or ``None``.

    A number counts only with a positive unit or cue (F2): ``power_kw``
    followed by kW, ``length_m`` by metres, ``phases`` by a phase word or
    sign, ``power_factor`` after a power-factor cue. "63 A", "16 mm2",
    "400 V", "5 MW", "10 kVA", "5.5 kW" as a length and "3 kW" as phases are
    not stated values of those fields.

    Args:
        field: The numeric field.
        value: Its proposed value.
        quote: The item's quoted text.

    Returns:
        The literal's offsets within the quote; for ``phases`` a phase word
        counts ("three-phase").
    """
    try:
        wanted = Decimal(str(value))
    except InvalidOperation:
        return None
    plain = quote.translate(_ARABIC_DIGITS)
    for number, start, end in _numbers(quote):
        if number != wanted:
            continue
        if field == "power_factor":
            if _PF_CUE.search(plain[max(0, start - 24) : start]):
                return start, end
        elif _AFTER[field].match(plain, end):
            return start, end
    if field == "phases" and wanted == wanted.to_integral_value() and int(wanted) in _PHASE_WORDS:
        match = _PHASE_WORDS[int(wanted)].search(quote)
        if match:
            return match.start(), match.end()
    return None


def _bounded(value: Any) -> dict[str, Any] | None:
    """What a rejected item proposed, kept short: strings cut, nesting dropped."""
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:12]:
        if isinstance(item, str):
            out[str(key)[:40]] = item[:MAX_STRING]
        elif isinstance(item, float):
            # The canonical hash takes no float: kept as the text it reads as.
            out[str(key)[:40]] = repr(item)[:40]
        elif isinstance(item, bool | int) or item is None:
            out[str(key)[:40]] = item
    return out


def _schema_error(exc: PydanticValidationError) -> bool:
    return any(error["type"] == "extra_forbidden" for error in exc.errors())


def _fields(exc: PydanticValidationError) -> str:
    return ", ".join(sorted({".".join(str(p) for p in e["loc"]) or "value" for e in exc.errors()}))


def check_output(
    payload: Any, text: str, boards: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Check a model's output against the description; split it into kept and refused items.

    Args:
        payload: The tool call's input, untrusted.
        text: The description, as stored.
        boards: The draft's board names, which a load or board may name.

    Returns:
        ``(items, server_rejected)``, JSON-shaped, ids ``i<n>`` by place.

    Raises:
        ProposalMalformed: Not the envelope, a key no item kind lists, more
            than :data:`MAX_ITEMS`, or most items' spans unusable.
    """
    try:
        raw = _RawOutput.model_validate(payload)
    except PydanticValidationError as exc:
        raise ProposalMalformed(
            f"the output is not the proposal tool's shape: {_fields(exc)}"
        ) from exc

    names = {name.lower() for name in boards} | {
        str(raw_item.value.get("name", "")).lower()
        for raw_item in raw.items
        if raw_item.kind == "board"
    }
    kept: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    model_ids: dict[str, str] = {}
    span_failures = 0

    def refuse(sid: str, item: _RawItem, reason: str, detail: str, quote: str | None) -> None:
        rejected.append(
            {
                "id": sid,
                "kind": item.kind if item.kind in VALUE_MODELS else None,
                "source": {"start": item.source.start, "end": item.source.end},
                "quote": quote,
                "value": _bounded(item.value),
                "reason": reason,
                "detail": detail[:MAX_STRING],
            }
        )

    for position, item in enumerate(raw.items):
        sid = f"i{position + 1}"
        model_cls = VALUE_MODELS.get(item.kind)
        if model_cls is None:
            refuse(sid, item, "invalid_value", f"unknown kind {item.kind[:40]}", None)
            continue
        try:
            value = model_cls.model_validate(item.value)
        except PydanticValidationError as exc:
            if _schema_error(exc):
                raise ProposalMalformed(
                    f"item {position + 1} sets a field its kind does not have: {_fields(exc)}"
                ) from exc
            refuse(sid, item, "invalid_value", _fields(exc), None)
            continue
        if item.id in model_ids:
            refuse(sid, item, "duplicate_id", f"id {item.id} is given twice", None)
            continue
        start, end = item.source.start, item.source.end
        if not 0 <= start < end <= len(text):
            span_failures += 1
            refuse(sid, item, "offsets_outside_input", "the span is not in the description", None)
            continue
        if end - start > MAX_SPAN:
            span_failures += 1
            refuse(sid, item, "span_too_long", f"a span holds at most {MAX_SPAN} characters", None)
            continue
        span = (start, end)
        quote = text[span[0] : span[1]]
        data = value.model_dump(mode="json", exclude_none=True)
        spans: dict[str, dict[str, int]] = {}
        missing = []
        for field in NUMERIC_FIELDS:
            if field in data:
                at = literal_span(field, data[field], quote)
                if at is None:
                    missing.append(field)
                else:
                    spans[field] = {"start": span[0] + at[0], "end": span[0] + at[1]}
        if missing:
            refuse(
                sid,
                item,
                "value_not_in_text",
                f"{', '.join(missing)} not stated in the quoted text",
                quote,
            )
            continue
        kind = item.kind
        if kind in ("board", "load"):
            own = " ".join([str(data.get("name", "")), str(data.get("description", ""))])
            if life_safety(" ".join([context_of(text, span, names), own])):
                kind, data, spans = (
                    "unsupported",
                    {"text": quote[:MAX_STRING], "reason": OUT_OF_SCOPE_REASON},
                    {},
                )
            elif product_number(" ".join([quote, own])):
                kind, data, spans = (
                    "unsupported",
                    {"text": quote[:MAX_STRING], "reason": PRODUCT_REASON},
                    {},
                )
        model_ids[item.id] = sid
        kept[sid] = {
            "id": sid,
            "kind": kind,
            "source": {"start": span[0], "end": span[1]},
            "quote": quote,
            "value": data,
            "value_spans": spans,
            "_model_id": item.id,
        }

    if raw.items and span_failures > 0 and span_failures / len(raw.items) > MALFORMED_SPAN_SHARE:
        raise ProposalMalformed(
            f"{span_failures} of {len(raw.items)} items quote text the description does not hold"
        )

    # References name the model's ids: map them to the server's, then drop,
    # until nothing changes, every item whose reference no longer resolves.
    for entry in kept.values():
        value = entry["value"]
        if entry["kind"] == "control_need":
            value["load"] = model_ids.get(value["load"], "?" + value["load"][:39])
        elif entry["kind"] == "question" and value.get("about") is not None:
            value["about"] = model_ids.get(value["about"], "?" + value["about"][:39])
    changed = True
    while changed:
        changed = False
        names = {e["value"]["name"] for e in kept.values() if e["kind"] == "board"} | set(boards)
        for sid, entry in list(kept.items()):
            problem = _unresolved(entry, kept, names)
            if problem:
                del kept[sid]
                rejected.append(
                    {
                        "id": sid,
                        "kind": entry["kind"],
                        "source": entry["source"],
                        "quote": entry["quote"],
                        "value": _bounded(entry["value"]),
                        "reason": "reference_unresolved",
                        "detail": problem,
                    }
                )
                changed = True
    board_names = [e["value"]["name"] for e in kept.values() if e["kind"] == "board"]
    for sid, entry in list(kept.items()):
        if entry["kind"] == "board" and board_names.count(entry["value"]["name"]) > 1:
            first = next(
                e
                for e in kept.values()
                if e["kind"] == "board" and e["value"]["name"] == entry["value"]["name"]
            )
            if first is not entry:
                del kept[sid]
                rejected.append(
                    {
                        "id": sid,
                        "kind": "board",
                        "source": entry["source"],
                        "quote": entry["quote"],
                        "value": _bounded(entry["value"]),
                        "reason": "duplicate_id",
                        "detail": f"board {entry['value']['name']} is proposed twice",
                    }
                )
    items = [{k: v for k, v in e.items() if k != "_model_id"} for e in kept.values()]
    rejected.sort(key=lambda r: int(r["id"][1:]))
    return items, rejected


def _unresolved(entry: Mapping[str, Any], kept: Mapping[str, Any], boards: set[str]) -> str:
    value = entry["value"]
    kind = entry["kind"]
    if kind == "board":
        if value.get("fed_from") is not None:
            if value["fed_from"] == value["name"]:
                return "a board cannot feed itself"
            if value["fed_from"] not in boards:
                return f"fed_from {value['fed_from']} is no proposed or existing board"
    elif kind == "load":
        if value["board"] not in boards:
            return f"board {value['board']} is no proposed or existing board"
    elif kind == "control_need":
        target = kept.get(value["load"])
        if target is None or target["kind"] != "load":
            return "the control need names no proposed load"
    elif kind == "question" and value.get("about") is not None and value["about"] not in kept:
        return "the question is about no proposed item"
    return ""


def proposal_sha256(
    *,
    input_text: str,
    provider: str,
    model: str,
    prompt_version: str,
    items: Sequence[Mapping[str, Any]],
    server_rejected: Sequence[Mapping[str, Any]],
) -> str:
    """The canonical hash of a proposal's immutable part."""
    return snapshot.sha256_of(
        {
            "schema": SCHEMA,
            "input_text": input_text,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "items": list(items),
            "server_rejected": list(server_rejected),
        }
    )


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


def check_edit(kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """An edited value checked against its kind's fields, JSON-shaped.

    Raises:
        ValueError: The kind takes no edit, or the value does not fit it.
    """
    if kind not in EDITABLE_KINDS:
        raise ValueError(f"a {kind} item is accepted or rejected, not edited")
    try:
        checked = VALUE_MODELS[kind].model_validate(dict(value))
    except PydanticValidationError as exc:
        raise ValueError(f"the edited value does not fit a {kind}: {_fields(exc)}") from exc
    return checked.model_dump(mode="json", exclude_none=True)


def latest_decisions(decisions: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Each item's latest decision (decisions are append-only)."""
    latest: dict[str, Mapping[str, Any]] = {}
    for decision in decisions:
        latest[str(decision["item_id"])] = decision
    return latest


def decided_value(
    item: Mapping[str, Any], decision: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """What a decision confirmed: the item's value (accept), the user's (edit), else ``None``."""
    if decision is None or decision["decision"] == "reject":
        return None
    if decision["decision"] == "edit":
        return dict(decision.get("value") or {})
    return dict(item["value"])


def state_of(decision: Mapping[str, Any] | None) -> str:
    """``proposed``, ``accepted``, ``edited`` or ``rejected``."""
    if decision is None:
        return "proposed"
    return {"accept": "accepted", "edit": "edited", "reject": "rejected"}[str(decision["decision"])]


# --------------------------------------------------------------------------- #
# Applying at save
# --------------------------------------------------------------------------- #


class PlacementError(Exception):
    """A placement a save cannot apply; ``code`` and ``params`` as the API refuses it."""

    def __init__(self, message: str, code: str, params: Mapping[str, Any]) -> None:
        """Record the refusal."""
        super().__init__(message)
        self.code = code
        self.params = dict(params)


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def place(
    request: ProjectDesignRequest,
    control: Mapping[tuple[int, int], Sequence[str]],
    items: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    placements: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Check each placement against the request; record what changed in the editor.

    Args:
        request: The request as saved.
        control: The control needs given, by ``(board_index, load_index)``.
        items: The proposal's kept items.
        decisions: Its decisions.
        placements: Where the save says each confirmed item went.

    Returns:
        The placements as recorded: indices, and ``changed_in_editor``.

    Raises:
        PlacementError: ``ai_placement_invalid`` (no such item or row, a kind that is not
            placed, or indices that do not fit its kind), ``ai_placement_twice``,
            ``ai_item_not_confirmed``.
    """
    by_id = {str(item["id"]): item for item in items}
    latest = latest_decisions(decisions)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for placement in placements:
        item_id = str(placement["item_id"])
        where = {"item_id": item_id}
        item = by_id.get(item_id)
        if item is None:
            raise PlacementError(
                f"the proposal has no item {item_id}", "ai_placement_invalid", where
            )
        if item_id in seen:
            raise PlacementError(f"item {item_id} is placed twice", "ai_placement_twice", where)
        seen.add(item_id)
        kind = item["kind"]
        if kind not in PLACEABLE_KINDS:
            raise PlacementError(
                f"a {kind} item is not placed in the request", "ai_placement_invalid", where
            )
        confirmed = decided_value(item, latest.get(item_id))
        if confirmed is None:
            raise PlacementError(
                f"item {item_id} is not accepted or edited", "ai_item_not_confirmed", where
            )
        b, li = int(placement["board_index"]), placement.get("load_index")
        if (kind == "board") != (li is None):
            raise PlacementError(
                f"a {kind} item is placed {'on a board' if kind == 'board' else 'on a load'}",
                "ai_placement_invalid",
                where,
            )
        if b >= len(request.boards) or (li is not None and li >= len(request.boards[b].loads)):
            raise PlacementError(
                f"item {item_id} is placed where the request has no row",
                "ai_placement_invalid",
                {**where, "board_index": b, "load_index": "" if li is None else li},
            )
        board = request.boards[b]
        changed: list[str] = []
        if kind == "board":
            if confirmed.get("name") != board.name:
                changed.append("name")
            if "fed_from" in confirmed and confirmed.get("fed_from") != board.fed_from:
                changed.append("fed_from")
        elif kind == "load":
            assert li is not None
            row = board.loads[li]
            row_values = {
                "board": board.name,
                "description": row.description,
                "kind": row.load.value,
                "power_kw": row.power_kw,
                "phases": row.phases,
                "power_factor": row.power_factor,
                "length_m": row.length_m,
                "controlled": row.controlled,
            }
            for field, proposed in confirmed.items():
                current = row_values.get(field)
                if isinstance(proposed, bool) or isinstance(current, bool):
                    differs = bool(proposed) != bool(current)
                elif field in NUMERIC_FIELDS:
                    differs = not _same(proposed, current)
                else:
                    differs = str(proposed) != str(current)
                if differs:
                    changed.append(field)
        else:
            assert li is not None
            if confirmed.get("need") not in control.get((b, li), ()):
                changed.append("need")
        out.append(
            {
                "item_id": item_id,
                "board_index": b,
                "load_index": li,
                "changed_in_editor": changed,
            }
        )
    return out


def with_refs(
    block: Mapping[str, Any],
    boards: Sequence[Mapping[str, Any]],
    loads: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The block's placements with the record's board and load refs, stable across revisions."""
    board_refs = [str(b["ref"]) for b in boards]
    by_board: dict[str, list[str]] = {}
    for load in loads:
        by_board.setdefault(str(load["board_ref"]), []).append(str(load["ref"]))
    placed = []
    for placement in block.get("placements", []):
        b = int(placement["board_index"])
        board_ref = board_refs[b] if b < len(board_refs) else None
        li = placement.get("load_index")
        rows = by_board.get(board_ref or "", [])
        load_ref = rows[li] if li is not None and li < len(rows) else None
        placed.append({**placement, "board_ref": board_ref, "load_ref": load_ref})
    return {**block, "placements": placed}


def open_items(ai: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The open items an applied proposal leaves: undecided items, unrejected questions and gaps."""
    if not ai:
        return []
    latest = latest_decisions(ai.get("decisions", []))
    out: list[dict[str, Any]] = []
    for item in (ai.get("proposal") or {}).get("items", []):
        decision = latest.get(str(item["id"]))
        rejected = decision is not None and decision["decision"] == "reject"
        code = None
        if item["kind"] == "question" and not rejected:
            code = "ai_question"
        elif item["kind"] == "unsupported" and not rejected:
            code = "ai_unsupported"
        elif item["kind"] in PLACEABLE_KINDS and decision is None:
            code = "ai_item_unreviewed"
        if code is not None:
            out.append({"code": code, "subject": {"item_id": str(item["id"])}})
    return out


def _short(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def open_item_line(ai: Mapping[str, Any] | None, item: Mapping[str, Any]) -> str | None:
    """The ``[OPEN]`` line a review export prints for one AI open item, or ``None``."""
    if not ai:
        return None
    wanted = (item.get("subject") or {}).get("item_id")
    items = (ai.get("proposal") or {}).get("items", [])
    found = next((i for i in items if str(i["id"]) == wanted), None)
    if found is None:
        return None
    value = found.get("value") or {}
    if item["code"] == "ai_question":
        return f"[OPEN] AI intake question, not answered: {_short(value.get('text', ''))}"
    if item["code"] == "ai_unsupported":
        return (
            f"[OPEN] Not covered by this design (from AI intake): {_short(value.get('text', ''))}"
            f" — {_short(value.get('reason', ''), 80)}"
        )
    if item["code"] == "ai_item_unreviewed":
        return f"[OPEN] AI intake item not reviewed ({found['kind']}): “{_short(found['quote'])}”"
    return None
