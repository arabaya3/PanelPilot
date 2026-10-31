"""Read and write the ``model`` payload of a ``pp-design/2`` snapshot, strictly.

:func:`parse_model` is the v2 reader's whole check: it refuses, as
:class:`~app.design.model.versions.SnapshotUnreadableError`, anything that is
not exactly a v2 payload in canonical form:

* a key missing or unknown at any level, a value of the wrong type, a float
  anywhere (Decimals are strings);
* a ``design`` that is not valid v1, or not v1 in its stored form;
* an id repeated within its collection; a collection not sorted by its key
  (one design has one byte form);
* a reference that does not resolve: a board, device or cable of the v1
  design, a template, a component's terminal, a strip point, a net, a
  connection;
* a template or connection pattern embedded but unused or used but not
  embedded; a ``registry_lock`` entry (``templates`` or ``patterns``)
  missing, extra, or with a digest that is not the SHA-256 of the embedded
  definition's canonical JSON;
* a wire attribute whose source is ``assumption`` without naming an
  assumption record, or naming one that is not there;
* a terminal in two nets, a connection end outside its net, two points of one
  strip sharing ``(marking, level)``.

:func:`dump_model` is its inverse and sorts every collection, so a model
built in any order is written in its one canonical form.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, NoReturn

from pydantic import ValidationError as PydanticValidationError

from app.design import snapshot
from app.design.model import domain, entities, versions
from app.design.model.convert_v1 import from_v1
from app.design.model.serialize import to_stored_v1

#: The keys of a v2 payload, every one required.
MODEL_KEYS = frozenset(
    {
        "design",
        "templates",
        "registry_lock",
        "components",
        "strips",
        "nets",
        "connections",
        "wires",
        "assumptions",
        "patterns",
    }
)

#: A template or pattern ref: ``<scope>/<id>/v<version>``. It never holds an ``@``: refs
#: appear in finding messages and values, and API responses must hold no ``@``
#: (an email must never leak, and the privacy tests refuse any).
REF_PATTERN = re.compile(r"^(global|tenant)/([a-z0-9][a-z0-9_.-]{0,99})/v([1-9][0-9]{0,8})$")

#: The longest id, marking or label accepted.
MAX_TEXT = 200

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_POSITIVE_DECIMAL = re.compile(r"^[0-9]{1,6}(\.[0-9]{1,6})?$")


def _bad(where: str, why: str) -> NoReturn:
    raise versions.SnapshotUnreadableError(f"not a {versions.V2} design: {where}: {why}")


def _obj(value: Any, keys: Iterable[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _bad(where, "is not an object")
    wanted = set(keys)
    if set(value) != wanted:
        missing, unknown = wanted - set(value), set(value) - wanted
        _bad(where, f"missing {sorted(missing)}, unknown {sorted(unknown)}")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        _bad(where, "is not a list")
    return value


def _text(value: Any, where: str, *, limit: int = MAX_TEXT, empty: bool = False) -> str:
    if not isinstance(value, str):
        _bad(where, "is not a string")
    if (not empty and not value) or len(value) > limit:
        _bad(where, "has a length outside its bounds")
    return value


def _opt_text(value: Any, where: str, *, limit: int = MAX_TEXT) -> str | None:
    return None if value is None else _text(value, where, limit=limit)


def _int(value: Any, where: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _bad(where, f"is not an integer >= {minimum}")
    return value


def _choice(value: Any, choices: Sequence[str], where: str) -> str:
    if value not in choices:
        _bad(where, f"is not one of {list(choices)}")
    return str(value)


def _strictly_sorted[T](items: Sequence[T], key: Callable[[T], Any], where: str) -> tuple[T, ...]:
    keys = [key(item) for item in items]
    for before, after in pairwise(keys):
        if not before < after:
            _bad(where, "is not sorted by its key, or repeats one")
    return tuple(items)


def _sorted_texts(value: Any, where: str) -> tuple[str, ...]:
    texts = [_text(item, f"{where}[]") for item in _list(value, where)]
    return _strictly_sorted(texts, lambda t: t, where)


def _refuse_floats(value: Any, where: str) -> None:
    if isinstance(value, float):
        _bad(where, "holds a float; Decimals are strings")
    if isinstance(value, dict):
        for item in value.values():
            _refuse_floats(item, where)
    elif isinstance(value, list):
        for item in value:
            _refuse_floats(item, where)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

_TEMPLATE_KEYS = (
    "ref",
    "function",
    "terminals",
    "contact_groups",
    "parameters",
    "provenance",
    "review",
    "restricted_scope",
)


def parse_template(value: Any, where: str = "template") -> entities.ComponentTemplate:
    """A template definition, checked strictly.

    Args:
        value: Its JSON.
        where: Where it sits, for the error message.

    Returns:
        The template.

    Raises:
        SnapshotUnreadableError: If it is not exactly a template definition.
    """
    _refuse_floats(value, where)
    raw = _obj(value, _TEMPLATE_KEYS, where)
    ref = _ref(raw["ref"], f"{where}.ref")
    groups = _strictly_sorted(
        [
            entities.ContactGroup(
                id=_text(g["id"], f"{where}.contact_groups[].id"),
                kind=_text(g["kind"], f"{where}.contact_groups[].kind"),
            )
            for g in (
                _obj(item, ("id", "kind"), f"{where}.contact_groups[]")
                for item in _list(raw["contact_groups"], f"{where}.contact_groups")
            )
        ],
        lambda g: g.id,
        f"{where}.contact_groups",
    )
    group_ids = {g.id for g in groups}
    terminals: list[entities.TerminalDef] = []
    for item in _list(raw["terminals"], f"{where}.terminals"):
        t = _obj(item, ("id", "marking", "kind", "required", "group"), f"{where}.terminals[]")
        if t["required"] is not None and not isinstance(t["required"], bool):
            _bad(f"{where}.terminals[].required", "is not true, false or null")
        group = _opt_text(t["group"], f"{where}.terminals[].group")
        if group is not None and group not in group_ids:
            _bad(f"{where}.terminals[].group", "names no contact group of the template")
        terminals.append(
            entities.TerminalDef(
                id=_text(t["id"], f"{where}.terminals[].id"),
                marking=_text(t["marking"], f"{where}.terminals[].marking"),
                kind=_text(t["kind"], f"{where}.terminals[].kind"),
                required=t["required"],
                group=group,
            )
        )
    _strictly_sorted(terminals, lambda t: t.id, f"{where}.terminals")
    markings = [t.marking for t in terminals]
    if len(set(markings)) != len(markings):
        _bad(f"{where}.terminals", "repeats a marking")
    parameters = _strictly_sorted(
        [
            entities.ParameterDef(
                name=_text(p["name"], f"{where}.parameters[].name"),
                kind=_text(p["kind"], f"{where}.parameters[].kind"),
                unit=_opt_text(p["unit"], f"{where}.parameters[].unit"),
            )
            for p in (
                _obj(item, ("name", "kind", "unit"), f"{where}.parameters[]")
                for item in _list(raw["parameters"], f"{where}.parameters")
            )
        ],
        lambda p: p.name,
        f"{where}.parameters",
    )
    return entities.ComponentTemplate(
        ref=ref,
        function=_text(raw["function"], f"{where}.function"),
        terminals=tuple(terminals),
        contact_groups=groups,
        parameters=parameters,
        provenance=_provenance(raw["provenance"], f"{where}.provenance"),
        review=_review(raw["review"], f"{where}.review"),
        restricted_scope=_sorted_texts(raw["restricted_scope"], f"{where}.restricted_scope"),
    )


def _ref(value: Any, where: str) -> str:
    ref = _text(value, where)
    if "@" in ref or not REF_PATTERN.fullmatch(ref):
        _bad(where, "is not <global|tenant>/<id>/v<version>")
    return ref


def _provenance(value: Any, where: str) -> entities.Provenance:
    prov = _obj(value, ("source", "author", "created"), where)
    return entities.Provenance(
        source=_opt_text(prov["source"], f"{where}.source", limit=2000),
        author=_text(prov["author"], f"{where}.author"),
        created=_text(prov["created"], f"{where}.created"),
    )


def _dump_provenance(provenance: entities.Provenance) -> dict[str, Any]:
    return {
        "source": provenance.source,
        "author": provenance.author,
        "created": provenance.created,
    }


def _review(value: Any, where: str) -> entities.Review:
    rev = _obj(value, ("status", "reviewer", "reviewed_at", "standards", "scope_note"), where)
    review = entities.Review(
        status=_choice(rev["status"], entities.REVIEW_STATUSES, f"{where}.status"),
        reviewer=_opt_text(rev["reviewer"], f"{where}.reviewer"),
        reviewed_at=_opt_text(rev["reviewed_at"], f"{where}.reviewed_at"),
        standards=_sorted_texts(rev["standards"], f"{where}.standards"),
        scope_note=_opt_text(rev["scope_note"], f"{where}.scope_note", limit=2000),
    )
    decided = review.reviewer is not None and review.reviewed_at is not None
    undecided = review.reviewer is None and review.reviewed_at is None
    if (review.status == "unreviewed" and not undecided) or (
        review.status != "unreviewed" and not decided
    ):
        _bad(where, "a decision records its reviewer and date, and only a decision")
    return review


def _dump_review(review: entities.Review) -> dict[str, Any]:
    return {
        "status": review.status,
        "reviewer": review.reviewer,
        "reviewed_at": review.reviewed_at,
        "standards": sorted(review.standards),
        "scope_note": review.scope_note,
    }


def dump_template(template: entities.ComponentTemplate) -> dict[str, Any]:
    """The canonical JSON of a template definition.

    Args:
        template: The template.

    Returns:
        Its JSON, every list sorted by its key.
    """
    return {
        "ref": template.ref,
        "function": template.function,
        "terminals": [
            {
                "id": t.id,
                "marking": t.marking,
                "kind": t.kind,
                "required": t.required,
                "group": t.group,
            }
            for t in sorted(template.terminals, key=lambda t: t.id)
        ],
        "contact_groups": [
            {"id": g.id, "kind": g.kind}
            for g in sorted(template.contact_groups, key=lambda g: g.id)
        ],
        "parameters": [
            {"name": p.name, "kind": p.kind, "unit": p.unit}
            for p in sorted(template.parameters, key=lambda p: p.name)
        ],
        "provenance": _dump_provenance(template.provenance),
        "review": _dump_review(template.review),
        "restricted_scope": sorted(template.restricted_scope),
    }


def template_digest(template: entities.ComponentTemplate) -> str:
    """The digest ``registry_lock`` records: SHA-256 of the canonical template JSON.

    Args:
        template: The template.

    Returns:
        64 lower-case hex digits.
    """
    return snapshot.sha256_of(dump_template(template))


# --------------------------------------------------------------------------- #
# Connection patterns
# --------------------------------------------------------------------------- #

_PATTERN_KEYS = ("ref", "slots", "strips", "points", "nets", "provenance", "review")

#: How far a pattern net reaches: one net per binding, or one per board and signal.
NET_SCOPES = ("binding", "board")


def parse_pattern(value: Any, where: str = "pattern") -> entities.ConnectionPattern:
    """A connection pattern definition in canonical form, checked strictly.

    Canonical form: ``slots`` sorted by ``id`` (each with ``template`` and
    ``device_kind``, either ``null``), ``strips`` sorted, ``points`` sorted
    by ``id``, ``nets`` sorted by ``role``; a net's ``members`` keep their
    order (connections name them by index), and each connection holds
    ``from``, ``to``, ``via``, ``core`` and ``label``.

    Args:
        value: Its JSON.
        where: Where it sits, for the error message.

    Returns:
        The pattern.

    Raises:
        SnapshotUnreadableError: If it is not exactly a canonical pattern.
    """
    _refuse_floats(value, where)
    raw = _obj(value, _PATTERN_KEYS, where)
    ref = _ref(raw["ref"], f"{where}.ref")
    slots = _strictly_sorted(
        [
            entities.PatternSlot(
                id=_text(s["id"], f"{where}.slots[].id"),
                template=None if s["template"] is None else _ref(s["template"], f"{where}.slots[]"),
                device_kind=_opt_text(s["device_kind"], f"{where}.slots[].device_kind"),
            )
            for s in (
                _obj(item, ("id", "template", "device_kind"), f"{where}.slots[]")
                for item in _list(raw["slots"], f"{where}.slots")
            )
        ],
        lambda s: s.id,
        f"{where}.slots",
    )
    strips = _sorted_texts(raw["strips"], f"{where}.strips")
    points = _strictly_sorted(
        [
            entities.PatternPoint(
                id=_text(p["id"], f"{where}.points[].id"),
                strip=_choice(p["strip"], strips, f"{where}.points[].strip"),
                offset=_int(p["offset"], f"{where}.points[].offset"),
                level=_int(p["level"], f"{where}.points[].level"),
                kind=_text(p["kind"], f"{where}.points[].kind"),
            )
            for p in (
                _obj(item, ("id", "strip", "offset", "level", "kind"), f"{where}.points[]")
                for item in _list(raw["points"], f"{where}.points")
            )
        ],
        lambda p: p.id,
        f"{where}.points",
    )
    slot_ids = {s.id for s in slots}
    point_ids = {p.id for p in points}
    nets: list[entities.PatternNet] = []
    for item in _list(raw["nets"], f"{where}.nets"):
        n = _obj(item, ("role", "signal", "scope", "members", "connections"), f"{where}.nets[]")
        members: list[entities.PatternMember] = []
        for m in _list(n["members"], f"{where}.nets[].members"):
            if isinstance(m, dict) and "slot" in m:
                m = _obj(m, ("slot", "terminal"), f"{where}.nets[].members[]")
                members.append(
                    entities.PatternMember(
                        slot=_choice(m["slot"], sorted(slot_ids), f"{where}.nets[].members[]"),
                        terminal=_text(m["terminal"], f"{where}.nets[].members[].terminal"),
                    )
                )
            else:
                m = _obj(m, ("point", "side"), f"{where}.nets[].members[]")
                members.append(
                    entities.PatternMember(
                        point=_choice(m["point"], sorted(point_ids), f"{where}.nets[].members[]"),
                        side=_choice(m["side"], entities.SIDES, f"{where}.nets[].members[]"),
                    )
                )
        if not members:
            _bad(f"{where}.nets[].members", "is empty")
        connections: list[entities.PatternConnection] = []
        for c in _list(n["connections"], f"{where}.nets[].connections"):
            c = _obj(c, ("from", "to", "via", "core", "label"), f"{where}.nets[].connections[]")
            ends = [
                _int(c[k], f"{where}.nets[].connections[].{k}", minimum=0) for k in ("from", "to")
            ]
            if any(e >= len(members) for e in ends) or ends[0] == ends[1]:
                _bad(f"{where}.nets[].connections[]", "an end is no other member's index")
            via = _choice(c["via"], entities.VIAS, f"{where}.nets[].connections[].via")
            core = None if c["core"] is None else _int(c["core"], f"{where}.nets[].connections[]")
            if (core is None) != (via != "cable_core") or (
                c["label"] is not None and via != "cable_core"
            ):
                _bad(f"{where}.nets[].connections[]", "only a cable core connection names a core")
            if via == "internal":
                first, second = members[ends[0]], members[ends[1]]
                if first.point is None or first.point != second.point:
                    _bad(
                        f"{where}.nets[].connections[]",
                        "internal joins the two sides of one strip point only",
                    )
            connections.append(
                entities.PatternConnection(
                    start=ends[0],
                    end=ends[1],
                    via=via,
                    core=core,
                    label=_opt_text(c["label"], f"{where}.nets[].connections[].label"),
                )
            )
        nets.append(
            entities.PatternNet(
                role=_text(n["role"], f"{where}.nets[].role"),
                signal=_text(n["signal"], f"{where}.nets[].signal"),
                scope=_choice(n["scope"], NET_SCOPES, f"{where}.nets[].scope"),
                members=tuple(members),
                connections=tuple(connections),
            )
        )
    _strictly_sorted(nets, lambda n: n.role, f"{where}.nets")
    return entities.ConnectionPattern(
        ref=ref,
        slots=slots,
        strips=strips,
        points=points,
        nets=tuple(nets),
        provenance=_provenance(raw["provenance"], f"{where}.provenance"),
        review=_review(raw["review"], f"{where}.review"),
    )


def _dump_pattern_member(member: entities.PatternMember) -> dict[str, Any]:
    if member.slot is not None:
        return {"slot": member.slot, "terminal": member.terminal}
    return {"point": member.point, "side": member.side}


def dump_pattern(pattern: entities.ConnectionPattern) -> dict[str, Any]:
    """The canonical JSON of a connection pattern.

    Args:
        pattern: The pattern.

    Returns:
        Its JSON (see :func:`parse_pattern` for the canonical form).
    """
    return {
        "ref": pattern.ref,
        "slots": [
            {"id": s.id, "template": s.template, "device_kind": s.device_kind}
            for s in sorted(pattern.slots, key=lambda s: s.id)
        ],
        "strips": sorted(pattern.strips),
        "points": [
            {"id": p.id, "strip": p.strip, "offset": p.offset, "level": p.level, "kind": p.kind}
            for p in sorted(pattern.points, key=lambda p: p.id)
        ],
        "nets": [
            {
                "role": n.role,
                "signal": n.signal,
                "scope": n.scope,
                "members": [_dump_pattern_member(m) for m in n.members],
                "connections": [
                    {"from": c.start, "to": c.end, "via": c.via, "core": c.core, "label": c.label}
                    for c in n.connections
                ],
            }
            for n in sorted(pattern.nets, key=lambda n: n.role)
        ],
        "provenance": _dump_provenance(pattern.provenance),
        "review": _dump_review(pattern.review),
    }


def pattern_digest(pattern: entities.ConnectionPattern) -> str:
    """The digest ``registry_lock.patterns`` records: SHA-256 of the canonical pattern JSON.

    Args:
        pattern: The pattern.

    Returns:
        64 lower-case hex digits.
    """
    return snapshot.sha256_of(dump_pattern(pattern))


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #


def _designation(value: Any, where: str) -> domain.Designation | None:
    if value is None:
        return None
    raw = _obj(value, ("function", "location", "product"), where)
    return domain.Designation(
        function=_opt_text(raw["function"], f"{where}.function"),
        location=_opt_text(raw["location"], f"{where}.location"),
        product=_text(raw["product"], f"{where}.product"),
    )


def dump_designation(value: domain.Designation | None) -> dict[str, Any] | None:
    """The JSON of a designation, as v1 writes one.

    Args:
        value: The designation, or ``None``.

    Returns:
        ``{"function", "location", "product"}``, or ``None``.
    """
    if value is None:
        return None
    return {"function": value.function, "location": value.location, "product": value.product}


def _terminal_ref(value: Any, where: str) -> entities.TerminalRef:
    if isinstance(value, dict) and "component" in value:
        raw = _obj(value, ("component", "terminal"), where)
        return entities.TerminalRef(
            component=_text(raw["component"], f"{where}.component"),
            terminal=_text(raw["terminal"], f"{where}.terminal"),
        )
    raw = _obj(value, ("point", "side"), where)
    return entities.TerminalRef(
        point=_text(raw["point"], f"{where}.point"),
        side=_choice(raw["side"], entities.SIDES, f"{where}.side"),
    )


def ref_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    """The canonical sort key of a terminal reference's JSON (as :attr:`TerminalRef.key`).

    Args:
        value: ``{"component", "terminal"}`` or ``{"point", "side"}``.

    Returns:
        The key members of a net are sorted by.
    """
    if "component" in value:
        return ("component", str(value["component"]), str(value["terminal"]))
    return ("point", str(value["point"]), str(value["side"]))


def dump_terminal_ref(ref: entities.TerminalRef) -> dict[str, Any]:
    """The JSON of a terminal reference.

    Args:
        ref: The reference.

    Returns:
        ``{"component", "terminal"}`` or ``{"point", "side"}``.
    """
    if ref.component is not None:
        return {"component": ref.component, "terminal": ref.terminal}
    return {"point": ref.point, "side": ref.side}


def _wire_attribute(value: Any, where: str, *, decimal: bool) -> entities.WireAttribute:
    raw = _obj(value, ("value", "source", "assumption"), where)
    source = _choice(raw["source"], entities.WIRE_SOURCES, f"{where}.source")
    if (raw["value"] is None) != (source == "unset"):
        _bad(where, "a value is null exactly when its source is unset")
    if (raw["assumption"] is None) != (source != "assumption"):
        _bad(where, "names an assumption exactly when its source is assumption")
    assumption = _opt_text(raw["assumption"], f"{where}.assumption")
    if raw["value"] is None:
        return entities.UNSET
    text = _text(raw["value"], f"{where}.value")
    if not decimal:
        return entities.WireAttribute(value=text, source=source, assumption=assumption)
    try:
        number = Decimal(text)
    except InvalidOperation:
        _bad(f"{where}.value", "is not a decimal")
    if not _POSITIVE_DECIMAL.match(text) or number <= 0:
        _bad(f"{where}.value", "is not a positive plain decimal")
    return entities.WireAttribute(value=number, source=source, assumption=assumption)


def _dump_attribute(attribute: entities.WireAttribute) -> dict[str, Any]:
    value = attribute.value
    return {
        "value": None if value is None else str(value),
        "source": attribute.source,
        "assumption": attribute.assumption,
    }


def _pattern_refs(value: Any, where: str) -> tuple[str, ...]:
    return _strictly_sorted(
        [_ref(item, f"{where}[]") for item in _list(value, where)], lambda r: r, where
    )


def parse_design(value: Any) -> domain.DesignModel:
    """A v1 design as v2 holds it: valid v1, and exactly in its stored v1 form.

    Args:
        value: The bare v1 snapshot JSON.

    Returns:
        Its domain model.

    Raises:
        SnapshotUnreadableError: If it is not valid v1, or not as v1 stores it
            (so one design has one byte form under ``design``).
    """
    _refuse_floats(value, "design")
    if not isinstance(value, dict):
        _bad("design", "is not an object")
    try:
        design = from_v1(value)
    except PydanticValidationError as exc:
        _bad("design", f"not a {versions.V1} design: {exc.error_count()} error(s)")
    if to_stored_v1(design) != value:
        _bad("design", f"not in its stored {versions.V1} form")
    return design


def _lock(value: Any, kind: str, digests: Mapping[str, str]) -> tuple[entities.LockEntry, ...]:
    where = f"registry_lock.{kind}"
    lock = _strictly_sorted(
        [
            entities.LockEntry(
                ref=_text(e["ref"], f"{where}[].ref"),
                digest=_text(e["digest"], f"{where}[].digest"),
            )
            for e in (_obj(item, ("ref", "digest"), f"{where}[]") for item in _list(value, where))
        ],
        lambda e: e.ref,
        where,
    )
    if {e.ref for e in lock} != set(digests):
        _bad("registry_lock", f"does not lock exactly the embedded {kind}")
    singular = kind[:-1]
    for entry in lock:
        if not _DIGEST.match(entry.digest) or entry.digest != digests[entry.ref]:
            _bad("registry_lock", f"the digest of {entry.ref} does not match its {singular}")
    return lock


def parse_model(content: Any) -> entities.DesignModelV2:
    """A v2 payload as its domain model, every invariant checked.

    Args:
        content: The envelope's ``model``.

    Returns:
        The model.

    Raises:
        SnapshotUnreadableError: If it is not exactly a canonical v2 payload
            (see the module docstring for what is checked).
    """
    _refuse_floats(content, "model")
    raw = _obj(content, MODEL_KEYS, "model")

    design = parse_design(raw["design"])
    boards = {b.id: b for b in design.boards}

    # Templates and their lock.
    templates = _strictly_sorted(
        [parse_template(item, "templates[]") for item in _list(raw["templates"], "templates")],
        lambda t: t.ref,
        "templates",
    )
    by_ref = {t.ref: t for t in templates}
    patterns = _strictly_sorted(
        [parse_pattern(item, "patterns[]") for item in _list(raw["patterns"], "patterns")],
        lambda p: p.ref,
        "patterns",
    )
    pattern_by_ref = {p.ref: p for p in patterns}
    lock_raw = _obj(raw["registry_lock"], ("templates", "patterns"), "registry_lock")
    lock = _lock(
        lock_raw["templates"], "templates", {r: template_digest(t) for r, t in by_ref.items()}
    )
    pattern_lock = _lock(
        lock_raw["patterns"], "patterns", {r: pattern_digest(p) for r, p in pattern_by_ref.items()}
    )

    # Components.
    components: list[entities.Component] = []
    mirrored: set[tuple[str, str]] = set()
    for item in _list(raw["components"], "components"):
        c = _obj(
            item,
            (
                "id",
                "board_id",
                "template_ref",
                "designation",
                "parameters",
                "legacy_device_id",
                "pattern_refs",
            ),
            "components[]",
        )
        board_id = _text(c["board_id"], "components[].board_id")
        if board_id not in boards:
            _bad("components[].board_id", "names no board")
        template = by_ref.get(_text(c["template_ref"], "components[].template_ref"))
        if template is None:
            _bad("components[].template_ref", "names no embedded template")
        declared = {p.name for p in template.parameters}
        values = _strictly_sorted(
            [
                entities.ParameterValue(
                    name=_text(p["name"], "components[].parameters[].name"),
                    value=_text(p["value"], "components[].parameters[].value", empty=True),
                )
                for p in (
                    _obj(v, ("name", "value"), "components[].parameters[]")
                    for v in _list(c["parameters"], "components[].parameters")
                )
            ],
            lambda p: p.name,
            "components[].parameters",
        )
        if any(v.name not in declared for v in values):
            _bad("components[].parameters", "names a parameter its template does not declare")
        legacy = _opt_text(c["legacy_device_id"], "components[].legacy_device_id")
        if legacy is not None:
            if legacy not in {d.id for d in boards[board_id].devices}:
                _bad("components[].legacy_device_id", "names no device of its board")
            if (board_id, legacy) in mirrored:
                _bad("components[].legacy_device_id", "is mirrored by two components")
            mirrored.add((board_id, legacy))
        components.append(
            entities.Component(
                id=_text(c["id"], "components[].id"),
                board_id=board_id,
                template_ref=template.ref,
                designation=_designation(c["designation"], "components[].designation"),
                parameters=values,
                legacy_device_id=legacy,
                pattern_refs=_pattern_refs(c["pattern_refs"], "components[].pattern_refs"),
            )
        )
    _strictly_sorted(components, lambda c: c.id, "components")
    component_by_id = {c.id: c for c in components}

    # Strips and their points.
    strips: list[entities.TerminalStrip] = []
    point_board: dict[str, str] = {}
    for item in _list(raw["strips"], "strips"):
        s = _obj(item, ("id", "board_id", "designation", "template_ref", "points"), "strips[]")
        board_id = _text(s["board_id"], "strips[].board_id")
        if board_id not in boards:
            _bad("strips[].board_id", "names no board")
        strip_template = _opt_text(s["template_ref"], "strips[].template_ref")
        if strip_template is not None and strip_template not in by_ref:
            _bad("strips[].template_ref", "names no embedded template")
        points = [
            entities.StripPoint(
                id=_text(p["id"], "strips[].points[].id"),
                position=_int(p["position"], "strips[].points[].position"),
                level=_int(p["level"], "strips[].points[].level"),
                marking=_text(p["marking"], "strips[].points[].marking"),
                kind=_text(p["kind"], "strips[].points[].kind"),
                pattern_ref=(
                    None
                    if p["pattern_ref"] is None
                    else _ref(p["pattern_ref"], "strips[].points[].pattern_ref")
                ),
            )
            for p in (
                _obj(
                    v,
                    ("id", "position", "level", "marking", "kind", "pattern_ref"),
                    "strips[].points[]",
                )
                for v in _list(s["points"], "strips[].points")
            )
        ]
        _strictly_sorted(points, lambda p: (p.position, p.level), "strips[].points")
        marks = [(p.marking, p.level) for p in points]
        if len(set(marks)) != len(marks):
            _bad("strips[].points", "two points share a marking and level")
        for point in points:
            if point.id in point_board:
                _bad("strips[].points[].id", "is not unique")
            point_board[point.id] = board_id
        strips.append(
            entities.TerminalStrip(
                id=_text(s["id"], "strips[].id"),
                board_id=board_id,
                designation=_designation(s["designation"], "strips[].designation"),
                template_ref=strip_template,
                points=tuple(points),
            )
        )
    _strictly_sorted(strips, lambda s: s.id, "strips")

    used = {c.template_ref for c in components} | {
        s.template_ref for s in strips if s.template_ref is not None
    }
    if used != set(by_ref):
        _bad("templates", "embeds a template the design does not use")

    # Nets.
    nets: list[entities.Net] = []
    member_of: dict[tuple[str, str, str], str] = {}
    for item in _list(raw["nets"], "nets"):
        n = _obj(item, ("id", "board_id", "signal", "origin", "members", "pattern_refs"), "nets[]")
        net_id = _text(n["id"], "nets[].id")
        board_id = _text(n["board_id"], "nets[].board_id")
        if board_id not in boards:
            _bad("nets[].board_id", "names no board")
        members = [_terminal_ref(m, "nets[].members[]") for m in _list(n["members"], "nets[]")]
        if not members:
            _bad("nets[].members", "is empty")
        _strictly_sorted(members, lambda m: m.key, "nets[].members")
        for member in members:
            if member.component is not None:
                owner = component_by_id.get(member.component)
                if owner is None:
                    _bad("nets[].members[].component", "names no component")
                template = by_ref[owner.template_ref]
                if template.terminal(member.terminal or "") is None:
                    _bad("nets[].members[].terminal", "is not a terminal of its template")
                member_board = owner.board_id
            else:
                if member.point not in point_board:
                    _bad("nets[].members[].point", "names no strip point")
                member_board = point_board[member.point or ""]
            if member_board != board_id:
                _bad("nets[].members[]", "is on another board than its net")
            if member.key in member_of:
                _bad("nets[].members[]", "a terminal is a member of two nets")
            member_of[member.key] = net_id
        nets.append(
            entities.Net(
                id=net_id,
                board_id=board_id,
                signal=_text(n["signal"], "nets[].signal"),
                origin=_choice(n["origin"], entities.ORIGINS, "nets[].origin"),
                members=tuple(members),
                pattern_refs=_pattern_refs(n["pattern_refs"], "nets[].pattern_refs"),
            )
        )
    _strictly_sorted(nets, lambda n: n.id, "nets")
    net_by_id = {n.id: n for n in nets}

    # Connections.
    connections: list[entities.Connection] = []
    cores: set[tuple[str, int]] = set()
    for item in _list(raw["connections"], "connections"):
        x = _obj(
            item, ("id", "net_id", "from", "to", "via", "core", "pattern_ref"), "connections[]"
        )
        net = net_by_id.get(_text(x["net_id"], "connections[].net_id"))
        if net is None:
            _bad("connections[].net_id", "names no net")
        start = _terminal_ref(x["from"], "connections[].from")
        end = _terminal_ref(x["to"], "connections[].to")
        if start == end:
            _bad("connections[]", "joins a terminal to itself")
        if member_of.get(start.key) != net.id or member_of.get(end.key) != net.id:
            _bad("connections[]", "an end is not a member of its net")
        via = _choice(x["via"], entities.VIAS, "connections[].via")
        if via == "internal" and not (
            start.point is not None and start.point == end.point and start.side != end.side
        ):
            _bad("connections[].via", "internal joins the two sides of one strip point only")
        core: entities.CableCore | None = None
        if (x["core"] is None) != (via != "cable_core"):
            _bad("connections[].core", "is given exactly when via is cable_core")
        if x["core"] is not None:
            k = _obj(x["core"], ("cable_id", "index", "label"), "connections[].core")
            cable_id = _text(k["cable_id"], "connections[].core.cable_id")
            cable = next((c for c in boards[net.board_id].cables if c.id == cable_id), None)
            if cable is None:
                _bad("connections[].core.cable_id", "names no cable of the net's board")
            index = _int(k["index"], "connections[].core.index")
            if index > cable.cores:
                _bad("connections[].core.index", "is past the cable's cores")
            if (cable_id, index) in cores:
                _bad("connections[].core", "a core makes two connections")
            cores.add((cable_id, index))
            core = entities.CableCore(
                cable_id=cable_id,
                index=index,
                label=_opt_text(k["label"], "connections[].core.label"),
            )
        connections.append(
            entities.Connection(
                id=_text(x["id"], "connections[].id"),
                net_id=net.id,
                start=start,
                end=end,
                via=via,
                core=core,
                pattern_ref=(
                    None
                    if x["pattern_ref"] is None
                    else _ref(x["pattern_ref"], "connections[].pattern_ref")
                ),
            )
        )
    _strictly_sorted(connections, lambda x: x.id, "connections")
    connection_by_id = {x.id: x for x in connections}

    # Wires.
    wires: list[entities.Wire] = []
    wired: set[str] = set()
    for item in _list(raw["wires"], "wires"):
        w = _obj(item, ("id", "connection_id", "number", "cross_section_mm2", "colour"), "wires[]")
        connection = connection_by_id.get(_text(w["connection_id"], "wires[].connection_id"))
        if connection is None or connection.via != "wire":
            _bad("wires[].connection_id", "names no connection made by wire")
        if connection.id in wired:
            _bad("wires[].connection_id", "has two wires")
        wired.add(connection.id)
        wires.append(
            entities.Wire(
                id=_text(w["id"], "wires[].id"),
                connection_id=connection.id,
                number=_wire_attribute(w["number"], "wires[].number", decimal=False),
                cross_section_mm2=_wire_attribute(
                    w["cross_section_mm2"], "wires[].cross_section_mm2", decimal=True
                ),
                colour=_wire_attribute(w["colour"], "wires[].colour", decimal=False),
            )
        )
    _strictly_sorted(wires, lambda w: w.id, "wires")

    assumptions = _strictly_sorted(
        [
            entities.Assumption(
                id=_text(a["id"], "assumptions[].id"),
                subject=_text(a["subject"], "assumptions[].subject"),
                statement=_text(a["statement"], "assumptions[].statement", limit=2000),
            )
            for a in (
                _obj(v, ("id", "subject", "statement"), "assumptions[]")
                for v in _list(raw["assumptions"], "assumptions")
            )
        ],
        lambda a: a.id,
        "assumptions",
    )

    assumption_ids = {a.id for a in assumptions}
    for wire in wires:
        for attribute in (wire.number, wire.cross_section_mm2, wire.colour):
            if attribute.assumption is not None and attribute.assumption not in assumption_ids:
                _bad("wires[].assumption", "names no assumption record")

    pattern_used = (
        {r for c in components for r in c.pattern_refs}
        | {r for n in nets for r in n.pattern_refs}
        | {x.pattern_ref for x in connections if x.pattern_ref is not None}
        | {p.pattern_ref for s in strips for p in s.points if p.pattern_ref is not None}
    )
    if not pattern_used <= set(pattern_by_ref):
        _bad("patterns", "a recorded pattern ref names no embedded pattern")
    if pattern_used != set(pattern_by_ref):
        _bad("patterns", "embeds a pattern the design does not use")
    # A component a pattern's binding created or joined fills one of its
    # slots, on that slot's template (the composer refuses any other): so
    # each pattern it names declares a slot its template may fill. A stored
    # component whose template was swapped for another embedded one, the
    # lock left valid, is refused here rather than read as a slot it is not.
    for component in components:
        for ref in component.pattern_refs:
            slot_templates = {s.template for s in pattern_by_ref[ref].slots}
            if None not in slot_templates and component.template_ref not in slot_templates:
                _bad(
                    "components[].template_ref",
                    "is not the template of any slot of a pattern it names",
                )

    return entities.DesignModelV2(
        design=design,
        templates=templates,
        registry_lock=lock,
        components=tuple(components),
        strips=tuple(strips),
        nets=tuple(nets),
        connections=tuple(connections),
        wires=tuple(wires),
        assumptions=assumptions,
        patterns=patterns,
        pattern_lock=pattern_lock,
    )


def dump_model(model: entities.DesignModelV2) -> dict[str, Any]:
    """The canonical v2 payload of a model: every collection sorted by its key.

    Args:
        model: The design.

    Returns:
        The envelope's ``model`` value. It is valid only if :func:`parse_model`
        accepts it; the writer in :mod:`write` checks that.
    """
    return {
        "design": to_stored_v1(model.design),
        "templates": [dump_template(t) for t in sorted(model.templates, key=lambda t: t.ref)],
        "registry_lock": {
            "templates": [
                {"ref": e.ref, "digest": e.digest}
                for e in sorted(model.registry_lock, key=lambda e: e.ref)
            ],
            "patterns": [
                {"ref": e.ref, "digest": e.digest}
                for e in sorted(model.pattern_lock, key=lambda e: e.ref)
            ],
        },
        "components": [
            {
                "id": c.id,
                "board_id": c.board_id,
                "template_ref": c.template_ref,
                "designation": dump_designation(c.designation),
                "parameters": [
                    {"name": p.name, "value": p.value}
                    for p in sorted(c.parameters, key=lambda p: p.name)
                ],
                "legacy_device_id": c.legacy_device_id,
                "pattern_refs": sorted(c.pattern_refs),
            }
            for c in sorted(model.components, key=lambda c: c.id)
        ],
        "strips": [
            {
                "id": s.id,
                "board_id": s.board_id,
                "designation": dump_designation(s.designation),
                "template_ref": s.template_ref,
                "points": [
                    {
                        "id": p.id,
                        "position": p.position,
                        "level": p.level,
                        "marking": p.marking,
                        "kind": p.kind,
                        "pattern_ref": p.pattern_ref,
                    }
                    for p in sorted(s.points, key=lambda p: (p.position, p.level))
                ],
            }
            for s in sorted(model.strips, key=lambda s: s.id)
        ],
        "nets": [
            {
                "id": n.id,
                "board_id": n.board_id,
                "signal": n.signal,
                "origin": n.origin,
                "members": [dump_terminal_ref(m) for m in sorted(n.members, key=lambda m: m.key)],
                "pattern_refs": sorted(n.pattern_refs),
            }
            for n in sorted(model.nets, key=lambda n: n.id)
        ],
        "connections": [
            {
                "id": x.id,
                "net_id": x.net_id,
                "from": dump_terminal_ref(x.start),
                "to": dump_terminal_ref(x.end),
                "via": x.via,
                "core": (
                    None
                    if x.core is None
                    else {"cable_id": x.core.cable_id, "index": x.core.index, "label": x.core.label}
                ),
                "pattern_ref": x.pattern_ref,
            }
            for x in sorted(model.connections, key=lambda x: x.id)
        ],
        "wires": [
            {
                "id": w.id,
                "connection_id": w.connection_id,
                "number": _dump_attribute(w.number),
                "cross_section_mm2": _dump_attribute(w.cross_section_mm2),
                "colour": _dump_attribute(w.colour),
            }
            for w in sorted(model.wires, key=lambda w: w.id)
        ],
        "assumptions": [
            {"id": a.id, "subject": a.subject, "statement": a.statement}
            for a in sorted(model.assumptions, key=lambda a: a.id)
        ],
        "patterns": [dump_pattern(p) for p in sorted(model.patterns, key=lambda p: p.ref)],
    }
