"""Read and write the ``model`` payload of a ``pp-design/3`` snapshot, strictly (M7a).

A v3 payload is a v2 payload plus one top-level key, ``links``: the
review-only record of each server-owned feeder's link between the board that
supplies it and the board it feeds. Everything but ``links`` is read by
:func:`codec_v2.parse_model` exactly as a v2 payload is, so nets stay
board-local and every v2 rule (``board_mismatch`` included) is unchanged.

``links`` is strictly sorted by id and canonical, like every v2 collection.
Each link must be the one :func:`derived_feeders` derives for a feeder of the
design (one per feeder, none extra), its id the digest of its two boards, its
ends' terminals real terminals of the end's own board, and every attribute
either ``user`` (with a value of the right type and bounds) or ``unset``
(``null``). A designer value never appears: no attribute has a ``data``
source.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from app.design.model import codec_v2, entities, ids, versions

#: The keys of a v3 payload, every one required: the v2 keys and ``links``.
MODEL_KEYS = codec_v2.MODEL_KEYS | {"links"}

_LINK_KEYS = ("id", "origin", "feeder", "ends", "carrier", "attributes", "provenance")
_END_KEYS = ("role", "board", "terminal", "label", "resolution")

#: The longest user text a link holds (label, material, insulation, number, colour).
MAX_LINK_TEXT = 200

#: Bounds of a link's numeric attributes, as the request bounds them.
MAX_CORES = 100
MAX_SECTION_MM2 = Decimal(1000)
MAX_LENGTH_M = Decimal(10000)


def _bad(where: str, why: str) -> NoReturn:
    raise versions.SnapshotUnreadableError(f"not a {versions.V3} design: {where}: {why}")


def _obj(value: Any, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _bad(where, "is not an object")
    if set(value) != set(keys):
        _bad(where, f"missing {sorted(set(keys) - set(value))}, unknown {sorted(set(value) - set(keys))}")
    return value


def _text(value: Any, where: str, limit: int = MAX_LINK_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _bad(where, "is not a non-empty string within its bounds")
    return value


def link_id(supply_board: str, fed_board: str) -> str:
    """A link's stable id: a digest of its supply board and fed board."""
    return ids.derive("l", supply_board, fed_board)


def requirement_ref(fed_board_ref: str) -> str:
    """The requirements-record ref of the feeder a link records."""
    return f"{fed_board_ref}.feeder"


def derived_feeders(model: entities.DesignModelV2) -> list[entities.LinkFeeder]:
    """Every server-owned feeder of the design, as a link records it, sorted by link id.

    A feeder is a board with ``fed_from`` naming a board of the design whose
    circuits hold one feeding it (``feeds``). Boards are named by id.
    """
    by_name = {board.name: board for board in model.design.boards}
    out: list[entities.LinkFeeder] = []
    for board in model.design.boards:
        if board.fed_from is None or board.fed_from not in by_name:
            continue
        supply = by_name[board.fed_from]
        circuit = next((c for c in supply.circuits if c.feeds == board.name), None)
        if circuit is None:
            continue
        out.append(
            entities.LinkFeeder(supply_board=supply.id, fed_board=board.id, circuit_id=circuit.id)
        )
    return sorted(out, key=lambda f: link_id(f.supply_board, f.fed_board))


def feeder_cable(model: entities.DesignModelV2, feeder: entities.LinkFeeder) -> Any:
    """The feeder circuit's cable in the design (``domain.Cable``), or ``None``."""
    board = next((b for b in model.design.boards if b.id == feeder.supply_board), None)
    if board is None:
        return None
    circuit = next((c for c in board.circuits if c.id == feeder.circuit_id), None)
    if circuit is None or circuit.cable_id is None:
        return None
    return next((c for c in board.cables if c.id == circuit.cable_id), None)


def terminal_board(model: entities.DesignModelV2, ref: entities.TerminalRef) -> str | None:
    """The board a terminal reference is on, or ``None`` if it names no modelled terminal."""
    if ref.component is not None:
        component = next((c for c in model.components if c.id == ref.component), None)
        if component is None:
            return None
        template = model.template(component.template_ref)
        if template is None or template.terminal(ref.terminal or "") is None:
            return None
        return component.board_id
    for strip in model.strips:
        for point in strip.points:
            if point.id == ref.point:
                return strip.board_id if ref.side in entities.SIDES else None
    return None


def _decimal(value: Any, where: str, *, positive: bool, limit: Decimal) -> Decimal:
    if not isinstance(value, str):
        _bad(where, "is not a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        _bad(where, "is not a decimal")
    if not number.is_finite() or canonical_decimal(number) != value:
        _bad(where, "is not a canonical plain decimal")
    if number < 0 or (positive and number == 0) or number > limit:
        _bad(where, "is outside its bounds")
    return number


def canonical_decimal(value: Decimal) -> str:
    """A decimal as a link stores it: plain, no exponent, no trailing zeros."""
    text = format(value.normalize(), "f")
    return text if text != "-0" else "0"


def _attribute(name: str, value: Any, where: str) -> entities.LinkAttribute:
    raw = _obj(value, ("value", "source"), where)
    source = raw["source"]
    if source not in entities.LINK_SOURCES:
        _bad(f"{where}.source", f"is not one of {list(entities.LINK_SOURCES)}")
    if (raw["value"] is None) != (source == "unset"):
        _bad(where, "a value is null exactly when its source is unset")
    if raw["value"] is None:
        return entities.LINK_UNSET
    v = raw["value"]
    parsed: str | int | Decimal
    if name == "cores":
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_CORES:
            _bad(f"{where}.value", f"is not an integer from 1 to {MAX_CORES}")
        parsed = v
    elif name == "cross_section_mm2":
        parsed = _decimal(v, f"{where}.value", positive=True, limit=MAX_SECTION_MM2)
    elif name == "length_m":
        parsed = _decimal(v, f"{where}.value", positive=False, limit=MAX_LENGTH_M)
    else:
        parsed = _text(v, f"{where}.value")
    return entities.LinkAttribute(value=parsed, source="user")


def dump_attribute(name: str, attribute: entities.LinkAttribute) -> dict[str, Any]:
    """The JSON of a link attribute (decimals as canonical strings, cores as an int)."""
    value = attribute.value
    if isinstance(value, Decimal):
        value = canonical_decimal(value)
    return {"value": value, "source": attribute.source}


def parse_links(value: Any, model: entities.DesignModelV2) -> tuple[entities.InterBoardLink, ...]:
    """Read a v3 payload's ``links`` against its v2 model, strictly.

    Raises:
        SnapshotUnreadableError: For any link that is not the derived one of
            a feeder of the design, any feeder without its link, an end's
            terminal not on its board, or a value of the wrong type or bounds.
    """
    if not isinstance(value, list):
        _bad("links", "is not a list")
    expected = {link_id(f.supply_board, f.fed_board): f for f in derived_feeders(model)}
    links: list[entities.InterBoardLink] = []
    for item in value:
        raw = _obj(item, _LINK_KEYS, "links[]")
        lid = raw["id"]
        feeder = expected.get(lid) if isinstance(lid, str) else None
        if feeder is None:
            _bad("links[].id", "is not the link of a feeder of the design")
        if raw["origin"] not in entities.LINK_ORIGINS:
            _bad("links[].origin", f"is not one of {list(entities.LINK_ORIGINS)}")
        f = _obj(raw["feeder"], ("supply_board", "fed_board", "circuit_id"), "links[].feeder")
        if (f["supply_board"], f["fed_board"], f["circuit_id"]) != (
            feeder.supply_board,
            feeder.fed_board,
            feeder.circuit_id,
        ):
            _bad("links[].feeder", "is not the feeder its id names")
        ends_raw = raw["ends"]
        if not isinstance(ends_raw, list) or len(ends_raw) != 2:
            _bad("links[].ends", "is not a list of two ends")
        ends: list[entities.LinkEnd] = []
        for role, board, end_value in zip(
            entities.LINK_ROLES, (feeder.supply_board, feeder.fed_board), ends_raw, strict=True
        ):
            e = _obj(end_value, _END_KEYS, "links[].ends[]")
            if e["role"] != role or e["board"] != board:
                _bad("links[].ends[]", "is not the from end on the supply board, then the to end")
            terminal = None
            if e["terminal"] is not None:
                terminal = codec_v2._terminal_ref(e["terminal"], "links[].ends[].terminal")
                if terminal_board(model, terminal) != board:
                    _bad("links[].ends[].terminal", "is not a modelled terminal of its end's board")
            label = None if e["label"] is None else _text(e["label"], "links[].ends[].label")
            end = entities.LinkEnd(role=role, board=board, terminal=terminal, label=label)
            if e["resolution"] != end.resolution:
                _bad("links[].ends[].resolution", "is resolved exactly when a terminal is set")
            ends.append(end)
        c = _obj(raw["carrier"], ("kind", "cable_id"), "links[].carrier")
        cable = feeder_cable(model, feeder)
        want = (
            {"kind": "cable", "cable_id": cable.id}
            if cable is not None
            else {"kind": "unknown", "cable_id": None}
        )
        if c != want:
            _bad("links[].carrier", "is not the feeder circuit's cable")
        a = _obj(raw["attributes"], entities.LINK_ATTRIBUTES, "links[].attributes")
        attributes = tuple(
            (name, _attribute(name, a[name], f"links[].attributes.{name}"))
            for name in entities.LINK_ATTRIBUTES
        )
        p = _obj(
            raw["provenance"],
            ("requirement_ref", "entered_by_user_id", "entered_at"),
            "links[].provenance",
        )
        ref = _text(p["requirement_ref"], "links[].provenance.requirement_ref")
        if not ref.endswith(".feeder"):
            _bad("links[].provenance.requirement_ref", "is not a feeder's requirement ref")
        # A trial's save may record when values were entered but no account.
        entered_at = None if p["entered_at"] is None else _text(p["entered_at"], "entered_at", 64)
        if p["entered_by_user_id"] is not None and entered_at is None:
            _bad("links[].provenance", "names who entered values but not when")
        links.append(
            entities.InterBoardLink(
                id=lid,
                origin=raw["origin"],
                feeder=feeder,
                ends=(ends[0], ends[1]),
                carrier=entities.LinkCarrier(kind=want["kind"], cable_id=want["cable_id"]),
                attributes=attributes,
                provenance=entities.LinkProvenance(
                    requirement_ref=ref,
                    entered_by_user_id=(
                        None
                        if p["entered_by_user_id"] is None
                        else _text(p["entered_by_user_id"], "entered_by_user_id", 64)
                    ),
                    entered_at=entered_at,
                ),
            )
        )
    ordered = [link.id for link in links]
    if ordered != sorted(set(ordered)):
        _bad("links", "is not sorted by id, or repeats one")
    if set(ordered) != set(expected):
        _bad("links", "does not hold exactly one link per feeder of the design")
    return tuple(links)


def dump_link(link: entities.InterBoardLink) -> dict[str, Any]:
    """The canonical JSON of one link."""
    return {
        "id": link.id,
        "origin": link.origin,
        "feeder": {
            "supply_board": link.feeder.supply_board,
            "fed_board": link.feeder.fed_board,
            "circuit_id": link.feeder.circuit_id,
        },
        "ends": [
            {
                "role": end.role,
                "board": end.board,
                "terminal": (
                    None if end.terminal is None else codec_v2.dump_terminal_ref(end.terminal)
                ),
                "label": end.label,
                "resolution": end.resolution,
            }
            for end in link.ends
        ],
        "carrier": {"kind": link.carrier.kind, "cable_id": link.carrier.cable_id},
        "attributes": {name: dump_attribute(name, value) for name, value in link.attributes},
        "provenance": {
            "requirement_ref": link.provenance.requirement_ref,
            "entered_by_user_id": link.provenance.entered_by_user_id,
            "entered_at": link.provenance.entered_at,
        },
    }


def dump_links(links: tuple[entities.InterBoardLink, ...] | list[entities.InterBoardLink]) -> list[dict[str, Any]]:
    """The canonical ``links`` collection, sorted by id."""
    return [dump_link(link) for link in sorted(links, key=lambda x: x.id)]


def parse_model(
    content: Any,
) -> tuple[entities.DesignModelV2, tuple[entities.InterBoardLink, ...]]:
    """Read a v3 payload: its v2 model and its links.

    Raises:
        SnapshotUnreadableError: If it is not a valid canonical v3 payload.
    """
    if not isinstance(content, Mapping):
        _bad("model", "is not an object")
    if set(content) != MODEL_KEYS:
        _bad(
            "model",
            f"missing {sorted(MODEL_KEYS - set(content))}, unknown {sorted(set(content) - MODEL_KEYS)}",
        )
    v2_payload = {key: value for key, value in content.items() if key != "links"}
    try:
        model = codec_v2.parse_model(v2_payload)
        codec_v2._refuse_floats(content["links"], "links")
    except versions.SnapshotUnreadableError as exc:
        raise versions.SnapshotUnreadableError(
            str(exc).replace(f"not a {versions.V2} design", f"not a {versions.V3} design", 1)
        ) from exc
    links = parse_links(content["links"], model)
    if dump_links(links) != content["links"]:
        _bad("links", "is not in canonical form")
    return model, links
