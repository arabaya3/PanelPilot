"""Compose a ``pp-design/2`` payload from a v1 design and a configuration, all as data.

A **configuration** is pure data (JSON-shaped):

``templates``
    Template definitions (see :func:`codec_v2.parse_template`), embedded in
    the result when used.
``patterns``
    Connection patterns. Each has a ``ref``; ``slots`` (components it joins:
    ``id``, ``template`` ref, optional ``device_kind`` for picking a v1
    device of a bound circuit); ``strips`` (strip roles); ``points`` (strip
    points it allocates: ``id``, ``strip`` role, ``offset`` ≥ 1 within the
    binding, ``level`` ≥ 1, ``kind``); and ``nets``: per role a ``signal``,
    a ``scope`` (``binding``: one net per binding; ``board``: one net per
    board and signal, shared by every binding), ``members`` (``{slot,
    terminal}`` or ``{point, side}``) and ``connections`` (``from``/``to``
    member indexes, ``via``, and for a cable core its ``core`` index).
``strips``
    The terminal strips to create: ``id``, ``board``, ``designation``,
    ``template`` (a template ref or ``null``).
``components``
    Components that mirror no v1 device: ``id``, ``board``, ``template``,
    ``designation``.
``bindings``
    Which v1 board/circuit/devices use which pattern: ``id``, ``pattern``,
    ``board``, ``circuit`` (or ``null``), ``slots`` (slot id to
    ``{"device": v1 id}`` or ``{"component": configuration component id}``),
    ``strips`` (strip role to configuration strip id), ``markings`` (pattern
    point id to the printed marking), ``cable`` (a v1 cable id or ``null``),
    ``wires`` (``"<net role>/<connection index>"`` to given ``number``,
    ``cross_section_mm2`` and ``colour`` values, recorded with source
    ``"data"``).

The composer is generic: it never branches on a template, pattern or
configuration id. Each v1 device bound to a slot becomes a component on the
slot's template; every other v1 device keeps its legacy template, as the
migration gives it. Strip points are allocated per strip in binding id order
(then by ``offset`` and ``level``), so positions do not depend on list order.

It **refuses rather than guesses**: every gap or contradiction raises
:class:`CompositionRefusedError` with a stable ``code``. A required terminal
left unconnected is *not* a refusal: a draft may be incomplete, so it is the
structural finding ``unconnected_required_terminal`` (:mod:`findings_v2`).
No wire number, cross-section or colour is ever computed or defaulted.
"""

from __future__ import annotations

import copy
import operator
from collections.abc import Mapping
from typing import Any, NoReturn

from app.design.model import codec_v2, entities, ids, migrate_v2, versions

_CONFIG_KEYS = ("templates", "patterns", "strips", "components", "bindings")
_OPTIONAL_CONFIG_KEYS = ("name", "_comment")
_PATTERN_KEYS = ("ref", "slots", "strips", "points", "nets")
_BINDING_KEYS = (
    "id",
    "pattern",
    "board",
    "circuit",
    "slots",
    "strips",
    "markings",
    "cable",
    "wires",
)
_WIRE_FIELDS = ("number", "cross_section_mm2", "colour")


class CompositionRefusedError(ValueError):
    """A configuration cannot be applied without guessing.

    Attributes:
        code: A stable identifier (``unknown_template``, ``ambiguous_binding`` ...).
        params: The ids it is about, as text.
    """

    def __init__(self, code: str, message: str, **params: str) -> None:
        """Record the refusal.

        Args:
            code: Its stable code.
            message: In English.
            **params: The ids it names.
        """
        super().__init__(f"{code}: {message}")
        self.code = code
        self.params: dict[str, str] = dict(params)


def _refuse(code: str, message: str, **params: str) -> NoReturn:
    raise CompositionRefusedError(code, message, **params)


def _obj(value: Any, keys: tuple[str, ...], where: str, optional: tuple[str, ...] = ()) -> Any:
    if not isinstance(value, Mapping):
        _refuse("configuration_invalid", f"{where} is not an object", where=where)
    present = set(value)
    if not set(keys) <= present or present - set(keys) - set(optional):
        _refuse(
            "configuration_invalid",
            f"{where} must hold exactly {sorted(keys)}",
            where=where,
        )
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        _refuse("configuration_invalid", f"{where} is not a list", where=where)
    return value


def _unique(items: list[Any], key: str, where: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        name = item[key]
        if not isinstance(name, str) or name in out:
            _refuse("configuration_invalid", f"{where}: {key} missing or repeated", where=where)
        out[name] = item
    return out


def _ordered(raw: Any) -> Any:
    """A configured template with its unordered lists sorted, as a snapshot embeds it.

    A configuration is authored input, so the order of its lists carries no
    meaning; the embedded copy is canonical. Anything malformed is left for
    :func:`codec_v2.parse_template` to refuse.
    """
    if not isinstance(raw, Mapping):
        return raw
    out = dict(raw)
    try:
        for name, key in (("terminals", "id"), ("contact_groups", "id"), ("parameters", "name")):
            if isinstance(out.get(name), list):
                out[name] = sorted(out[name], key=operator.itemgetter(key))
        if isinstance(out.get("restricted_scope"), list):
            out["restricted_scope"] = sorted(out["restricted_scope"])
        review = out.get("review")
        if isinstance(review, Mapping) and isinstance(review.get("standards"), list):
            out["review"] = {**review, "standards": sorted(review["standards"])}
    except (KeyError, TypeError):
        return raw
    return out


class _Composer:
    def __init__(self, design_v1: dict[str, Any], configuration: Mapping[str, Any]) -> None:
        self.design_json = copy.deepcopy(design_v1)
        self.design = codec_v2.parse_design(self.design_json)
        self.boards = {b.id: b for b in self.design.boards}
        config = _obj(configuration, _CONFIG_KEYS, "configuration", _OPTIONAL_CONFIG_KEYS)

        self.templates: dict[str, entities.ComponentTemplate] = {}
        for item in _list(config["templates"], "templates"):
            try:
                template = codec_v2.parse_template(_ordered(item), "configuration.templates[]")
            except versions.SnapshotUnreadableError as exc:
                _refuse("template_invalid", str(exc))
            if template.ref in self.templates:
                _refuse("configuration_invalid", "a template ref is repeated", ref=template.ref)
            legacy = {t["ref"]: t for t in migrate_v2.LEGACY_TEMPLATES.values()}
            if template.ref in legacy and codec_v2.dump_template(template) != legacy[template.ref]:
                _refuse(
                    "template_conflict",
                    "a configuration redefines a frozen legacy template",
                    ref=template.ref,
                )
            self.templates[template.ref] = template
        self.patterns = _unique(
            [_obj(p, _PATTERN_KEYS, "patterns[]") for p in _list(config["patterns"], "patterns")],
            "ref",
            "patterns",
        )
        self.strip_defs = _unique(
            [
                _obj(s, ("id", "board", "designation", "template"), "strips[]")
                for s in _list(config["strips"], "strips")
            ],
            "id",
            "strips",
        )
        self.component_defs = _unique(
            [
                _obj(c, ("id", "board", "template", "designation"), "components[]")
                for c in _list(config["components"], "components")
            ],
            "id",
            "components",
        )
        self.bindings = _unique(
            [_obj(b, _BINDING_KEYS, "bindings[]") for b in _list(config["bindings"], "bindings")],
            "id",
            "bindings",
        )

        # Output, keyed by id.
        self.components: dict[str, dict[str, Any]] = {}
        self.bound_devices: dict[tuple[str, str], str] = {}
        self.strips: dict[str, dict[str, Any]] = {}
        self.next_position: dict[str, int] = {}
        self.nets: dict[str, dict[str, Any]] = {}
        self.member_net: dict[tuple[str, str, str], str] = {}
        self.connections: dict[str, dict[str, Any]] = {}
        self.wires: dict[str, dict[str, Any]] = {}
        self.cores: set[tuple[str, int]] = set()

    # -- lookups ------------------------------------------------------------ #

    def template(self, ref: Any, **where: str) -> entities.ComponentTemplate:
        template = self.templates.get(ref) if isinstance(ref, str) else None
        if template is None:
            _refuse("unknown_template", "names a template the configuration does not hold", **where)
        if template.restricted_scope:
            _refuse(
                "restricted_scope",
                "the template is restricted to uses this composer may not design",
                ref=template.ref,
                scope=",".join(template.restricted_scope),
            )
        return template

    def board(self, board_id: Any, **where: str) -> Any:
        board = self.boards.get(board_id) if isinstance(board_id, str) else None
        if board is None:
            _refuse("unknown_reference", "names no board of the design", **where)
        return board

    # -- building ----------------------------------------------------------- #

    def run(self) -> dict[str, Any]:
        for strip_id in sorted(self.strip_defs):
            self.add_strip(strip_id)
        for binding_id in sorted(self.bindings):
            self.apply(self.bindings[binding_id])
        skip = set(self.bound_devices)
        for legacy in migrate_v2.legacy_components(self.design_json, skip):
            self.components[legacy["id"]] = legacy
        return self.payload()

    def add_strip(self, strip_id: str) -> None:
        raw = self.strip_defs[strip_id]
        self.board(raw["board"], strip=strip_id)
        ref = raw["template"]
        if ref is not None:
            self.template(ref, strip=strip_id)
        sid = ids.derive("s", strip_id)
        self.strips[strip_id] = {
            "id": sid,
            "board_id": raw["board"],
            "designation": copy.deepcopy(raw["designation"]),
            "template_ref": ref,
            "points": [],
        }
        self.next_position[strip_id] = 1

    def slot_component(
        self, binding: Mapping[str, Any], slot: Mapping[str, Any], circuit: Any
    ) -> tuple[str, entities.ComponentTemplate]:
        bid, slot_id = binding["id"], slot["id"]
        given = binding["slots"].get(slot_id)
        board = self.boards[binding["board"]]
        if given is None:
            kind = slot.get("device_kind")
            if circuit is None or kind is None:
                _refuse(
                    "missing_binding",
                    "the binding does not say which device fills the slot",
                    binding=bid,
                    slot=slot_id,
                )
            devices = {d.id: d for d in board.devices}
            candidates = sorted(
                d for d in circuit.device_ids if d in devices and devices[d].kind == kind
            )
            if not candidates:
                _refuse(
                    "missing_binding",
                    "no device of the circuit can fill the slot",
                    binding=bid,
                    slot=slot_id,
                )
            if len(candidates) > 1:
                _refuse(
                    "ambiguous_binding",
                    "more than one device of the circuit could fill the slot",
                    binding=bid,
                    slot=slot_id,
                    candidates=",".join(candidates),
                )
            given = {"device": candidates[0]}
        given = _obj(given, (), f"bindings[{bid}].slots.{slot_id}", ("device", "component"))
        if len(given) != 1:
            _refuse(
                "configuration_invalid", "a slot names one device or one component", slot=slot_id
            )
        if "device" in given:
            template = self.template(slot.get("template"), binding=bid, slot=slot_id)
            device_id = given["device"]
            device = next((d for d in board.devices if d.id == device_id), None)
            if device is None:
                _refuse(
                    "unknown_reference",
                    "names no device of the binding's board",
                    binding=bid,
                    device=str(device_id),
                )
            key = (board.id, device.id)
            cid = migrate_v2.component_id(board.id, device.id)
            if key in self.bound_devices and self.bound_devices[key] != template.ref:
                _refuse(
                    "conflicting_binding",
                    "one device is bound to two templates",
                    device=device.id,
                )
            self.bound_devices[key] = template.ref
            self.components[cid] = {
                "id": cid,
                "board_id": board.id,
                "template_ref": template.ref,
                "designation": codec_v2.dump_designation(device.designation),
                "parameters": [],
                "legacy_device_id": device.id,
            }
            return cid, template
        name = given["component"]
        raw = self.component_defs.get(name)
        if raw is None:
            _refuse("unknown_reference", "names no configuration component", component=str(name))
        if raw["board"] != board.id:
            _refuse("board_mismatch", "the component is on another board", component=name)
        template = self.template(raw["template"], component=name)
        if slot.get("template") is not None and slot["template"] != template.ref:
            _refuse(
                "conflicting_binding",
                "the component's template is not the slot's",
                component=name,
                slot=slot_id,
            )
        cid = ids.derive("k", name)
        self.components[cid] = {
            "id": cid,
            "board_id": board.id,
            "template_ref": template.ref,
            "designation": copy.deepcopy(raw["designation"]),
            "parameters": [],
            "legacy_device_id": None,
        }
        return cid, template

    def apply(self, binding: Mapping[str, Any]) -> None:
        bid = binding["id"]
        pattern = self.patterns.get(binding["pattern"])
        if pattern is None:
            _refuse(
                "unknown_pattern", "names a pattern the configuration does not hold", binding=bid
            )
        board = self.board(binding["board"], binding=bid)
        circuit = None
        if binding["circuit"] is not None:
            circuit = next((c for c in board.circuits if c.id == binding["circuit"]), None)
            if circuit is None:
                _refuse("unknown_reference", "names no circuit of the board", binding=bid)
        for mapping in ("slots", "strips", "markings", "wires"):
            if not isinstance(binding[mapping], Mapping):
                _refuse("configuration_invalid", f"bindings[].{mapping} is not an object")

        slots = _unique(_list(pattern["slots"], "pattern.slots"), "id", "pattern.slots")
        unknown = set(binding["slots"]) - set(slots)
        if unknown:
            _refuse("configuration_invalid", "binds a slot the pattern lacks", binding=bid)
        filled = {
            slot_id: self.slot_component(binding, slots[slot_id], circuit)
            for slot_id in sorted(slots)
        }

        # Strip points, allocated after what earlier bindings took.
        roles = _list(pattern["strips"], "pattern.strips")
        if set(binding["strips"]) != set(roles):
            _refuse("missing_binding", "the binding must name a strip for each role", binding=bid)
        for role in roles:
            strip_id = binding["strips"][role]
            if strip_id not in self.strips:
                _refuse("unknown_reference", "names no configuration strip", strip=str(strip_id))
            if self.strips[strip_id]["board_id"] != board.id:
                _refuse("board_mismatch", "the strip is on another board", strip=strip_id)
        points: dict[str, str] = {}
        bases = {
            binding["strips"][role]: self.next_position[binding["strips"][role]] for role in roles
        }
        point_defs = _unique(_list(pattern["points"], "pattern.points"), "id", "pattern.points")
        for point_id, raw in sorted(point_defs.items()):
            raw = _obj(raw, ("id", "strip", "offset", "level", "kind"), "pattern.points[]")
            for name in ("offset", "level"):
                number = raw[name]
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    _refuse(
                        "pattern_invalid", f"a point's {name} is an integer >= 1", point=point_id
                    )
            if raw["strip"] not in roles:
                _refuse("pattern_invalid", "a point names no strip role", point=point_id)
            marking = binding["markings"].get(point_id)
            if not isinstance(marking, str) or not marking:
                _refuse(
                    "missing_binding",
                    "the binding gives no marking for a strip point",
                    binding=bid,
                    point=point_id,
                )
            strip_id = binding["strips"][raw["strip"]]
            strip = self.strips[strip_id]
            position = bases[strip_id] + raw["offset"] - 1
            if any((p["marking"], p["level"]) == (marking, raw["level"]) for p in strip["points"]):
                _refuse("duplicate_marking", "two points share a marking", strip=strip_id)
            pid = ids.derive("p", strip_id, bid, point_id)
            strip["points"].append(
                {
                    "id": pid,
                    "position": position,
                    "level": raw["level"],
                    "marking": marking,
                    "kind": raw["kind"],
                }
            )
            self.next_position[strip_id] = max(self.next_position[strip_id], position + 1)
            points[point_id] = pid
        extra = set(binding["markings"]) - set(points)
        if extra:
            _refuse("configuration_invalid", "marks a point the pattern lacks", binding=bid)

        # Nets and connections.
        for net_def in _list(pattern["nets"], "pattern.nets"):
            net_def = _obj(
                net_def, ("role", "signal", "scope", "members", "connections"), "pattern.nets[]"
            )
            role = net_def["role"]
            members: list[dict[str, Any]] = []
            for raw in _list(net_def["members"], "pattern.nets[].members"):
                if "slot" in raw:
                    raw = _obj(raw, ("slot", "terminal"), "pattern.nets[].members[]")
                    if raw["slot"] not in filled:
                        _refuse("pattern_invalid", "a member names no slot", role=role)
                    cid, template = filled[raw["slot"]]
                    if template.terminal(raw["terminal"]) is None:
                        _refuse(
                            "terminal_not_on_template",
                            "the pattern joins a terminal its template does not have",
                            template=template.ref,
                            terminal=str(raw["terminal"]),
                        )
                    members.append({"component": cid, "terminal": raw["terminal"]})
                else:
                    raw = _obj(raw, ("point", "side"), "pattern.nets[].members[]")
                    if raw["point"] not in points or raw["side"] not in entities.SIDES:
                        _refuse("pattern_invalid", "a member names no point or side", role=role)
                    members.append({"point": points[raw["point"]], "side": raw["side"]})
            if net_def["scope"] == "binding":
                nid = ids.derive("n", bid, role)
            elif net_def["scope"] == "board":
                nid = ids.derive("n", board.id, net_def["signal"])
            else:
                _refuse("pattern_invalid", "a net's scope is binding or board", role=role)
            net = self.nets.setdefault(
                nid,
                {
                    "id": nid,
                    "board_id": board.id,
                    "signal": net_def["signal"],
                    "origin": "derived",
                    "members": [],
                },
            )
            for member in members:
                key = codec_v2.ref_key(member)
                owner = self.member_net.get(key)
                if owner is not None and owner != nid:
                    _refuse(
                        "contradictory_nets",
                        "one terminal would be a member of two nets",
                        binding=bid,
                        role=role,
                    )
                if owner is None:
                    self.member_net[key] = nid
                    net["members"].append(member)
            for index, raw in enumerate(
                _list(net_def["connections"], "pattern.nets[].connections")
            ):
                self.connect(binding, role, index, raw, members, nid, board)
        given_wires = set(binding["wires"])
        unknown_wires = given_wires - {
            w["_key"] for w in self.wires.values() if w["_binding"] == bid
        }
        if unknown_wires:
            _refuse(
                "unknown_wire",
                "gives wire data for a connection that is not a wire",
                binding=bid,
                wires=",".join(sorted(unknown_wires)),
            )

    def connect(
        self,
        binding: Mapping[str, Any],
        role: str,
        index: int,
        raw: Any,
        members: list[dict[str, Any]],
        nid: str,
        board: Any,
    ) -> None:
        bid = binding["id"]
        raw = _obj(raw, ("from", "to", "via"), "pattern.nets[].connections[]", ("core", "label"))
        ends = []
        for end in (raw["from"], raw["to"]):
            if not isinstance(end, int) or isinstance(end, bool) or not 0 <= end < len(members):
                _refuse("pattern_invalid", "a connection end is no member index", role=role)
            ends.append(members[end])
        via = raw["via"]
        if via not in entities.VIAS:
            _refuse("pattern_invalid", "a connection's via is unknown", role=role)
        core = None
        if via == "cable_core":
            cable_id = binding["cable"]
            if cable_id is None:
                _refuse(
                    "missing_binding", "the binding names no cable for a cable core", binding=bid
                )
            cable = next((c for c in board.cables if c.id == cable_id), None)
            if cable is None:
                _refuse("unknown_reference", "names no cable of the board", cable=str(cable_id))
            index_ = raw.get("core")
            if not isinstance(index_, int) or isinstance(index_, bool) or index_ < 1:
                _refuse("pattern_invalid", "a cable core connection names its core", role=role)
            if index_ > cable.cores:
                _refuse(
                    "core_out_of_range",
                    "the pattern uses a core the cable does not have",
                    cable=cable.id,
                    core=str(index_),
                )
            if (cable.id, index_) in self.cores:
                _refuse("core_reused", "one cable core would make two connections", cable=cable.id)
            self.cores.add((cable.id, index_))
            core = {"cable_id": cable.id, "index": index_, "label": raw.get("label")}
        elif "core" in raw or "label" in raw:
            _refuse("pattern_invalid", "only a cable core connection names a core", role=role)
        xid = ids.derive("x", bid, role, str(index))
        self.connections[xid] = {
            "id": xid,
            "net_id": nid,
            "from": ends[0],
            "to": ends[1],
            "via": via,
            "core": core,
        }
        if via != "wire":
            return
        key = f"{role}/{index}"
        given = binding["wires"].get(key, {})
        given = _obj(given, (), f"bindings[{bid}].wires.{key}", _WIRE_FIELDS)
        wid = ids.derive("w", bid, role, str(index))
        wire: dict[str, Any] = {"id": wid, "connection_id": xid, "_key": key, "_binding": bid}
        for name in _WIRE_FIELDS:
            value = given.get(name)
            if value is not None and not isinstance(value, str):
                _refuse("configuration_invalid", "wire data is given as text", field=name)
            wire[name] = {"value": value, "source": "unset" if value is None else "data"}
        self.wires[wid] = wire

    def payload(self) -> dict[str, Any]:
        components = [self.components[k] for k in sorted(self.components)]
        strips = []
        for strip in sorted(self.strips.values(), key=lambda s: s["id"]):
            strip["points"].sort(key=lambda p: (p["position"], p["level"]))
            strips.append(strip)
        used = sorted(
            {c["template_ref"] for c in components}
            | {s["template_ref"] for s in strips if s["template_ref"] is not None}
        )
        legacy = {t["ref"]: t for t in migrate_v2.LEGACY_TEMPLATES.values()}
        templates = [
            codec_v2.dump_template(self.templates[ref]) if ref in self.templates else legacy[ref]
            for ref in used
        ]
        nets = []
        for net in sorted(self.nets.values(), key=lambda n: n["id"]):
            net["members"].sort(key=codec_v2.ref_key)
            nets.append(net)
        wires = [
            {k: v for k, v in w.items() if not k.startswith("_")}
            for w in sorted(self.wires.values(), key=lambda w: w["id"])
        ]
        payload = {
            "design": self.design_json,
            "templates": copy.deepcopy(templates),
            "registry_lock": migrate_v2.lock_of(templates),
            "components": components,
            "strips": strips,
            "nets": nets,
            "connections": [self.connections[k] for k in sorted(self.connections)],
            "wires": wires,
            "assumptions": [],
        }
        try:
            codec_v2.parse_model(payload)
        except versions.SnapshotUnreadableError as exc:
            _refuse("composition_invalid", str(exc))
        return payload


def compose(design_v1: dict[str, Any], configuration: Mapping[str, Any]) -> dict[str, Any]:
    """The v2 payload of a designed v1 snapshot under a configuration.

    Args:
        design_v1: The bare v1 snapshot (``DesignProject`` JSON as stored). Not changed.
        configuration: Templates, patterns, strips, components and bindings,
            as data (see the module docstring).

    Returns:
        The canonical v2 ``model`` payload; :func:`codec_v2.parse_model` accepts it.

    Raises:
        CompositionRefusedError: If information is missing, ambiguous,
            contradictory or not supported (see its ``code``).
        SnapshotUnreadableError: If ``design_v1`` is not a stored v1 design.
    """
    return _Composer(design_v1, configuration).run()
