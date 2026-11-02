"""Adversarial topology and geometry tests of routed connection lines (M5), from the PDF alone.

Every check reads the *rendered* PDF with ``pdfplumber`` and compares it with
the saved ``pp-design/2`` model (and the schedules derived from that model by
their public functions). Nothing reads the renderer's private constants, its
router or its layout objects: a renderer that draws the same drawing passes.

Cases: every committed v2 configuration, plus stress models built here as
configuration data and composed through ``write.snapshot_v2_from_v1`` (so
``codec_v2`` checks them): a dense crossing grid, one terminal with seven
connections, a long chain, two nets with the same signal, unrealised nets,
jumpers and internal links on strips, many boards, and a model larger than
one sheet.

The PDF contract these tests assume is set in one place, the section
"The routed-drawing contract" below; adjust it there if the renderer's
documented contract differs. Its reading heuristics and their limits are
documented beside each reader.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import io
import itertools
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from reportlab.lib.units import mm

from app.design import connection_drawing_pdf, marking, schedule_pdf, snapshot
from app.design.model import (
    compose,
    connection_drawing,
    entities,
    findings_v2,
    schedules,
    write,
)
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.test_drawing_geometry import (
    CONFIG_CASES,
    EPS,
    PRIMARY_MIN_PT,
    SECONDARY_MIN_PT,
    TRUNCATION_MARKER,
    BlockRead,
    Box,
    P,
    Page,
    Run,
    Seg,
    _ink,
    _read_page,
    _rect,
    _symbol,
    _v1,
    has_note,
    is_secondary,
    meet,
    read_pdf,
    squash,
    through_box,
)
from app.tests.design.test_drawing_geometry import build as geometry_build

# =========================================================================== #
# The routed-drawing contract: the one place it is set
# =========================================================================== #
#
# * A terminal point is a filled round dot smaller than TERMINAL_DOT_MAX.
# * Each saved Connection is ONE stroked path. Its two ends lie on its two
#   terminal dots, or on one dot and one continuation marker (other sheet).
#   Routed lines have one stroke width, distinct from every symbol stroke.
# * A junction dot is a filled round dot at least JUNCTION_RATIO times the
#   terminal-point diameter, drawn centred on the terminal point, only at
#   terminals with two or more connections.
# * Crossings of different connections are orthogonal, with a gap or a hop,
#   and no dot.
# * A continuation marker is an arrow or flag (a small closed non-round
#   shape) plus the text CONTINUATION, both within CONTINUATION_REACH of the
#   line's end.
# * Each line has a label LINE_LABEL ("w-..." wire id or "x-..." connection
#   short id) within LABEL_REACH of the line.
# * Block headers contain "(component)" or "(strip)" (the geometry reader's
#   block detection).
# * An unroutable connection keeps a stub at each end, a text
#   cross-reference "to <label> (...)" and an open item UNROUTED_ITEM that
#   names the connection's x-/w- id and gives a reason.
# * Every page carries REVIEW_MARKS and the snapshot hash; the cover/legend
#   matches SCHEMATIC_NOTE.
# * Dotted and dash-dot stubs are each at least STUB_MIN long and have
#   different dash arrays.

TERMINAL_DOT_MAX = 3 * mm
#: The largest filled dot read at all (junction dots may exceed TERMINAL_DOT_MAX).
DOT_READ_MAX = 6 * mm
JUNCTION_RATIO = 1.2
CONTINUATION_REACH = 10 * mm
LABEL_REACH = 6 * mm
STUB_MIN = 8 * mm
#: Text minimums, as parameters: primary / secondary (grey) text, in points.
PRIMARY_PT = PRIMARY_MIN_PT
SECONDARY_PT = SECONDARY_MIN_PT
#: A gapped crossing: two collinear fragments of one line with at most this gap.
GAP_MAX = 3 * mm
#: A hop: a non-axis piece of a line no longer than this, where another line crosses.
HOP_MAX = 4 * mm
#: The largest arrow / flag shape of a continuation marker.
MARKER_MAX = 8 * mm
#: No dot may lie within this of a crossing.
CROSSING_DOT_CLEAR = 1 * mm

CONTINUATION = re.compile(
    r"to (?P<board>[^\s/()]+)/(?P<label>.+?) \(sheet (?P<sheet>\d+), zone (?P<zone>\d+[A-Z])\)"
)
LINE_LABEL = re.compile(r"(?<![0-9A-Za-z])([wx]-[0-9a-f]{4,24})")
UNROUTED_ITEM = re.compile(r"unrouted:")
REVIEW_MARKS = ("REVIEW ONLY", "NOT FOR EXECUTION")
SCHEMATIC_NOTE = re.compile(
    r"schematic.{0,200}not.{0,60}physical|not.{0,60}physical.{0,200}schematic",
    re.IGNORECASE | re.DOTALL,
)
_HEADER = re.compile(r"\((component|strip)\)")
#: How far a terminal's marking may be printed from its point, and off its row.
MARKING_REACH = 10 * mm
MARKING_ROW = 3 * mm

# =========================================================================== #
# Stress models, as configuration data
# =========================================================================== #

_PROVENANCE = {
    "source": "synthetic test data",
    "author": "test suite",
    "created": "2026-11-01",
}
_REVIEW: dict[str, Any] = {
    "status": "unreviewed",
    "reviewer": None,
    "reviewed_at": None,
    "standards": [],
    "scope_note": None,
}
_COMMENT = "SYNTHETIC TEST DATA for routed topology; no value here is an engineering value."

#: A net member: ("c", component key, terminal index) or ("p", strip key, point index, side).
Member = tuple[Any, ...]


@dataclass
class NetSpec:
    board: str
    signal: str
    members: list[Member]
    connections: list[tuple[int, int, str]]


@dataclass
class Spec:
    comps: list[tuple[str, str, int]] = field(default_factory=list)  # key, board, terminals
    strips: list[tuple[str, str, int]] = field(default_factory=list)  # key, board, points
    nets: list[NetSpec] = field(default_factory=list)
    boards: list[str] = field(default_factory=list)  # extra boards to add to the v1 snapshot
    names: dict[str, str] = field(default_factory=dict)  # component key -> designation


def _right_symbol(count: int) -> dict[str, Any]:
    anchors = [{"terminal": f"t{n:03d}", "x": 6, "y": 1 + 2 * n} for n in range(count)]
    return _symbol(6, 2 * count, [_rect(1, 0, 4, 2 * count)], anchors)


def _configuration(spec: Spec) -> dict[str, Any]:
    templates, patterns, strips, components, bindings = [], [], [], [], []
    boards = sorted({b for _k, b, _n in spec.comps} | {b for _k, b, _n in spec.strips})
    for key, board, count in spec.comps:
        templates.append(
            {
                "ref": f"tenant/synth-route-{key}/v1",
                "function": "relay",
                "terminals": [
                    {
                        "id": f"t{n:03d}",
                        "marking": f"{n + 1}",
                        "kind": "signal",
                        "required": True,
                        "group": None,
                    }
                    for n in range(count)
                ],
                "contact_groups": [],
                "parameters": [],
                "provenance": _PROVENANCE,
                "review": _REVIEW,
                "restricted_scope": [],
                "symbol": _right_symbol(count),
            }
        )
        components.append(
            {
                "id": f"c{key}",
                "board": board,
                "template": f"tenant/synth-route-{key}/v1",
                "designation": {
                    "function": None,
                    "location": None,
                    "product": spec.names.get(key, f"K{key}"),
                },
            }
        )
    for key, board, _count in spec.strips:
        strips.append(
            {
                "id": f"X{key}",
                "board": board,
                "designation": {
                    "function": None,
                    "location": None,
                    "product": f"X{key}",
                },
                "template": None,
            }
        )
    for board in boards:
        pref = f"tenant/synth-route-p-{board.lower()}/v1"
        comps = [k for k, b, _n in spec.comps if b == board]
        strip_keys = [(k, n) for k, b, n in spec.strips if b == board]
        nets, wires = [], {}
        for index, net in enumerate(n for n in spec.nets if n.board == board):
            role = f"r{index:03d}"
            members: list[dict[str, Any]] = []
            for m in net.members:
                if m[0] == "c":
                    members.append({"slot": f"s{m[1]}", "terminal": f"t{m[2]:03d}"})
                else:
                    members.append({"point": f"{m[1]}p{m[2]:03d}", "side": m[3]})
            connections = []
            for c_index, (a, b, via) in enumerate(net.connections):
                connections.append({"from": a, "to": b, "via": via})
                if via == "wire":
                    wires[f"{role}/{c_index}"] = {
                        "number": {
                            "value": f"SYN-W{board}-{index:03d}-{c_index}",
                            "source": "user",
                        }
                    }
            nets.append(
                {
                    "role": role,
                    "signal": net.signal,
                    "scope": "binding",
                    "members": members,
                    "connections": connections,
                }
            )
        points = [
            {
                "id": f"{k}p{n:03d}",
                "strip": f"x{k}",
                "offset": n + 1,
                "level": 1,
                "kind": "signal",
            }
            for k, count in strip_keys
            for n in range(count)
        ]
        patterns.append(
            {
                "ref": pref,
                "slots": [{"id": f"s{k}", "template": f"tenant/synth-route-{k}/v1"} for k in comps],
                "strips": [f"x{k}" for k, _n in strip_keys],
                "points": points,
                "nets": nets,
                "provenance": _PROVENANCE,
                "review": _REVIEW,
            }
        )
        bindings.append(
            {
                "id": f"bind-{board.lower()}",
                "pattern": pref,
                "board": board,
                "circuit": None,
                "slots": {f"s{k}": {"component": f"c{k}"} for k in comps},
                "strips": {f"x{k}": f"X{k}" for k, _n in strip_keys},
                "markings": {p["id"]: str(p["offset"]) for p in points},
                "cable": None,
                "wires": wires,
            }
        )
    return {
        "name": "synthetic routed topology stress",
        "_comment": _COMMENT,
        "templates": sorted(templates, key=lambda t: t["ref"]),
        "patterns": sorted(patterns, key=lambda p: p["ref"]),
        "strips": strips,
        "components": components,
        "bindings": bindings,
    }


def _v1_with(boards: Sequence[str]) -> dict[str, Any]:
    """The base v1 snapshot with extra boards, each with one legacy incomer."""
    v1 = _v1()
    template = v1["boards"][0]
    device = next(d for d in template["devices"] if d["upstream_id"] is not None)
    for board_id in boards:
        incomer = {
            **copy.deepcopy(device),
            "id": f"{board_id.lower()}-incomer",
            "upstream_id": None,
            "designation": {"function": board_id, "location": None, "product": "Q1"},
        }
        v1["boards"].append(
            {
                **copy.deepcopy(template),
                "id": board_id,
                "name": board_id,
                "cables": [],
                "circuits": [],
                "devices": [incomer],
                "incomer_ids": [incomer["id"]],
                "notes": [],
                "fed_from": None,
            }
        )
    return v1


B = "DB-V"


def _pair(board: str, signal: str, a: Member, b: Member, via: str = "wire") -> NetSpec:
    return NetSpec(board, signal, [a, b], [(0, 1, via)])


def spec_dense_grid() -> Spec:
    """Eight components, each wired to every other one: many unavoidable crossings."""
    n = 8
    spec = Spec(comps=[(f"g{i}", B, n - 1) for i in range(n)])
    for i, j in itertools.combinations(range(n), 2):
        spec.nets.append(
            _pair(B, f"SYN-G{i}{j}", ("c", f"g{i}", j - 1), ("c", f"g{j}", i)),
        )
    return spec


def spec_hub() -> Spec:
    """One terminal with seven connections (a star), plus a second hub on a strip point."""
    spec = Spec(comps=[("h", B, 2)] + [(f"l{k}", B, 2) for k in range(7)], strips=[("hs", B, 2)])
    members: list[Member] = [("c", "h", 0)] + [("c", f"l{k}", 0) for k in range(7)]
    spec.nets.append(NetSpec(B, "SYN-HUB", members, [(0, k, "wire") for k in range(1, 8)]))
    point: list[Member] = [("p", "hs", 0, "inner")] + [("c", f"l{k}", 1) for k in range(6)]
    spec.nets.append(NetSpec(B, "SYN-HUB2", point, [(0, k, "wire") for k in range(1, 7)]))
    return spec


def spec_long_chain() -> Spec:
    """Fifty components in a chain, each joined to the next: it spans several sheets."""
    n = 50
    spec = Spec(comps=[(f"ch{i:02d}", B, 2) for i in range(n)])
    for i in range(n - 1):
        spec.nets.append(
            _pair(B, f"SYN-CH{i:02d}", ("c", f"ch{i:02d}", 1), ("c", f"ch{i + 1:02d}", 0))
        )
    return spec


def spec_same_signal() -> Spec:
    """Two nets with the same signal name on one board, and one on another: never merged."""
    spec = Spec(
        comps=[(f"ss{i}", B, 2) for i in range(4)] + [("so0", "DB-SO", 1), ("so1", "DB-SO", 1)]
    )
    spec.boards = ["DB-SO"]
    spec.nets.append(_pair(B, "SYN-SAME", ("c", "ss0", 0), ("c", "ss1", 0)))
    spec.nets.append(_pair(B, "SYN-SAME", ("c", "ss2", 0), ("c", "ss3", 0)))
    spec.nets.append(_pair("DB-SO", "SYN-SAME", ("c", "so0", 0), ("c", "so1", 0)))
    # Interleave the second terminals so a naive router would pass near the other net.
    spec.nets.append(_pair(B, "SYN-OTHER", ("c", "ss0", 1), ("c", "ss3", 1)))
    return spec


def spec_unrealised() -> Spec:
    """A three-member net with one connection, and a four-member net in two islands."""
    spec = Spec(comps=[(f"u{i}", B, 1) for i in range(7)])
    spec.nets.append(
        NetSpec(B, "SYN-UNR3", [("c", f"u{i}", 0) for i in range(3)], [(0, 1, "wire")])
    )
    spec.nets.append(
        NetSpec(
            B,
            "SYN-UNR4",
            [("c", f"u{i}", 0) for i in range(3, 7)],
            [(0, 1, "wire"), (2, 3, "wire")],
        )
    )
    return spec


def spec_strip_links() -> Spec:
    """A strip whose points' sides are joined by internal links and adjacent points by jumpers."""
    spec = Spec(comps=[("sl", B, 6)], strips=[("sl", B, 6)])
    for k in (0, 2, 4):
        members: list[Member] = [
            ("c", "sl", k),
            ("c", "sl", k + 1),
            ("p", "sl", k, "inner"),
            ("p", "sl", k, "outer"),
            ("p", "sl", k + 1, "outer"),
            ("p", "sl", k + 1, "inner"),
        ]
        connections = [
            (0, 2, "wire"),
            (2, 3, "internal"),
            (3, 4, "jumper"),
            (4, 5, "internal"),
            (1, 5, "wire"),
        ]
        spec.nets.append(NetSpec(B, f"SYN-SL{k}", members, connections))
    return spec


def spec_many_boards() -> Spec:
    """Ten boards, each with a three-component triangle of wires."""
    boards = [f"DB-M{n:02d}" for n in range(10)]
    spec = Spec(boards=boards)
    for board in boards:
        keys = [f"{board[-3:].lower()}{i}" for i in range(3)]
        spec.comps += [(k, board, 2) for k in keys]
        spec.nets.append(_pair(board, f"SYN-{board}-A", ("c", keys[0], 0), ("c", keys[1], 0)))
        spec.nets.append(_pair(board, f"SYN-{board}-B", ("c", keys[1], 1), ("c", keys[2], 0)))
        spec.nets.append(_pair(board, f"SYN-{board}-C", ("c", keys[2], 1), ("c", keys[0], 1)))
    return spec


def spec_oversize() -> Spec:
    """Forty components of six terminals, 120 wires between far-apart blocks: more than a sheet."""
    n = 40
    spec = Spec(comps=[(f"o{i:02d}", B, 6) for i in range(n)])
    for i in range(n):
        for k in range(3):
            j = (i + 7 * (k + 1)) % n
            spec.nets.append(
                _pair(
                    B,
                    f"SYN-O{i:02d}{k}",
                    ("c", f"o{i:02d}", k),
                    ("c", f"o{j:02d}", k + 3),
                )
            )
    return spec


def spec_split_symbol() -> Spec:
    """One 48-pin component, each pin wired to a strip point.

    Its block splits into parts, so pins on a later part keep a stub (the
    dash-dot style) instead of a lead.
    """
    n = 48
    spec = Spec(comps=[("big", B, n)], strips=[("big", B, n)])
    for k in range(n):
        spec.nets.append(_pair(B, f"SYN-BIG{k:02d}", ("c", "big", k), ("p", "big", k, "inner")))
    return spec


def spec_long_designations() -> Spec:
    """Thirty components with 70-character designations, chained and with long jumps.

    Wrapped titles and markings, and continuation flags naming long labels across sheets.
    """
    n = 30
    keys = [f"ld{i:02d}" for i in range(n)]
    spec = Spec(comps=[(k, B, 3) for k in keys])
    spec.names = {
        k: f"KLONG-SYNTHETIC-DESIGNATION-{i:02d}-WITH-NO-SPACES-0123456789-ABCDEFGHIJKLMNOPQRS"
        for i, k in enumerate(keys)
    }
    for i in range(n - 1):
        spec.nets.append(_pair(B, f"SYN-LD{i:02d}", ("c", keys[i], 1), ("c", keys[i + 1], 0)))
    # Long jumps too, so continuations are needed whatever the placement.
    for i in range(0, n - 15, 5):
        spec.nets.append(_pair(B, f"SYN-LDJ{i:02d}", ("c", keys[i], 2), ("c", keys[i + 15], 2)))
    return spec


def spec_both_sides() -> Spec:
    """One terminal with six connections: three as their start, three as their end.

    Its peers are components and strip points, so lines leave it towards both
    sides of the sheet.
    """
    spec = Spec(
        comps=[("bs", B, 2)] + [(f"bp{k}", B, 1) for k in range(3)],
        strips=[("bs", B, 3)],
    )
    members: list[Member] = [("c", "bs", 0)]
    members += [("c", f"bp{k}", 0) for k in range(3)]
    members += [("p", "bs", k, "inner") for k in range(3)]
    connections = [(0, k, "wire") for k in (1, 2, 3)] + [(k, 0, "wire") for k in (4, 5, 6)]
    spec.nets.append(NetSpec(B, "SYN-BOTH", members, connections))
    spec.nets.append(_pair(B, "SYN-BOTH2", ("c", "bs", 1), ("p", "bs", 0, "outer")))
    return spec


def spec_same_designation() -> Spec:
    """Components designated alike ("K1", "K2") on three boards, each wired the same way."""
    boards = ["DB-SD1", "DB-SD2", "DB-SD3"]
    spec = Spec(boards=boards)
    for n, board in enumerate(boards):
        a, b = f"sd{n}a", f"sd{n}b"
        spec.comps += [(a, board, 2), (b, board, 2)]
        spec.names[a], spec.names[b] = "K1", "K2"
        spec.nets.append(_pair(board, "SYN-SD", ("c", a, 0), ("c", b, 0)))
        spec.nets.append(_pair(board, "SYN-SD-X", ("c", a, 1), ("c", b, 1)))
    return spec


def spec_jumper_every_point() -> Spec:
    """A ten-point strip with a jumper between every pair of neighbouring points.

    One net: each point's sides are joined internally, the outer sides by jumpers along
    the whole strip, and one wire feeds the first point from a component.
    """
    n = 10
    spec = Spec(comps=[("jf", B, 1)], strips=[("jp", B, n)])
    members: list[Member] = [("c", "jf", 0)]
    members += [("p", "jp", k, side) for k in range(n) for side in ("inner", "outer")]
    connections: list[tuple[int, int, str]] = [(0, 1, "wire")]
    for k in range(n):
        connections.append((1 + 2 * k, 2 + 2 * k, "internal"))
        if k + 1 < n:
            connections.append((2 + 2 * k, 2 + 2 * (k + 1), "jumper"))
    spec.nets.append(NetSpec(B, "SYN-JMP", members, connections))
    return spec


def spec_all_off_sheet() -> Spec:
    """A component whose connections go to other sheets.

    A 40-link chain spans sheets; "KFAR" (terminal 1 twice: a junction) is wired to
    chain members far apart. On the cross-reference sheets its peers are on other
    sheets; the routed sheets regroup blocks by connection (a layout choice the
    test cannot force), so there its lines may be drawn whole.
    """
    n = 40
    spec = Spec(comps=[(f"oc{i:02d}", B, 3) for i in range(n)] + [("far", B, 3)])
    spec.names["far"] = "KFAR"
    for i in range(n - 1):
        spec.nets.append(
            _pair(B, f"SYN-OC{i:02d}", ("c", f"oc{i:02d}", 1), ("c", f"oc{i + 1:02d}", 0))
        )
    spec.nets.append(
        NetSpec(
            B,
            "SYN-FAR0",
            [("c", "far", 0), ("c", "oc00", 2), ("c", "oc39", 2)],
            [(0, 1, "wire"), (0, 2, "wire")],
        )
    )
    spec.nets.append(_pair(B, "SYN-FAR1", ("c", "far", 1), ("c", "oc20", 2)))
    spec.nets.append(_pair(B, "SYN-FAR2", ("c", "far", 2), ("c", "oc30", 2)))
    return spec


STRESS: dict[str, Callable[[], Spec]] = {
    "dense_grid": spec_dense_grid,
    "hub": spec_hub,
    "long_chain": spec_long_chain,
    "same_signal": spec_same_signal,
    "unrealised": spec_unrealised,
    "strip_links": spec_strip_links,
    "many_boards": spec_many_boards,
    "oversize": spec_oversize,
    "split_symbol": spec_split_symbol,
    "long_designations": spec_long_designations,
    "both_sides": spec_both_sides,
    "same_designation": spec_same_designation,
    "jumper_every_point": spec_jumper_every_point,
    "all_off_sheet": spec_all_off_sheet,
}

STRESS_CASES = [f"route:{name}" for name in STRESS]
#: The geometry module's long-names sample (geometry_cases/long_names_sample.json).
SAMPLE_CASES = ["sample:long_names"]
ALL_CASES = [*CONFIG_CASES, *SAMPLE_CASES, *STRESS_CASES]


@functools.cache
def build(case: str) -> tuple[entities.DesignModelV2, str]:
    kind, _, name = case.partition(":")
    if kind != "route":
        return geometry_build(case)
    spec = STRESS[name]()
    stored = write.snapshot_v2_from_v1(_v1_with(spec.boards), _configuration(spec))
    model = read_snapshot(stored).v2
    assert model is not None
    return model, snapshot.sha256_of(stored)


_MARK = marking.ExportMark(label=marking.DRAFT_LABEL, identity="Synthetic, revision 01")


def render_fresh(case: str) -> bytes:
    """Render a case through the public functions, uncached."""
    model, digest = build(case)
    header = connection_drawing_pdf.DrawingHeader(
        project=f"Synthetic {case}",
        revision="01",
        design_sha256=digest,
        schema="pp-design/2",
        rules_version="rules-under-test",
        renderer_version="renderer-under-test",
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(model)
    ]
    return connection_drawing_pdf.render_connection_drawing(
        connection_drawing.drawing_set(model), header, _MARK, structural
    )


@functools.cache
def render(case: str) -> bytes:
    return render_fresh(case)


def test_the_stress_models_are_what_they_claim() -> None:
    model, _ = build("route:hub")
    per_key: dict[tuple[str, str, str], int] = defaultdict(int)
    for x in model.connections:
        per_key[x.start.key] += 1
        per_key[x.end.key] += 1
    assert max(per_key.values()) >= 6
    model, _ = build("route:same_signal")
    signals = [n.signal for n in model.nets]
    assert signals.count("SYN-SAME") == 3
    model, _ = build("route:unrealised")
    assert len(unrealised_nets(model)) == 2
    model, _ = build("route:strip_links")
    vias = {x.via for x in model.connections}
    assert {"jumper", "internal", "wire"} <= vias
    model, _ = build("route:many_boards")
    assert len({c.board_id for c in model.components}) >= 10
    model, _ = build("route:dense_grid")
    assert len(model.connections) == 28
    for case in ("route:long_chain", "route:oversize"):
        assert len([p for p in doc(case).pages if p.page.is_drawing]) >= 2, case


# =========================================================================== #
# The model, as the drawing must show it
# =========================================================================== #

Key = tuple[str, str, str]


class ModelIndex:
    """Lookups over one model: owners, printed markings, labels, connections and ids."""

    def __init__(self, model: entities.DesignModelV2) -> None:
        """Index ``model``."""
        self.model = model
        self.connections = {x.id: x for x in model.connections}
        self.wire_of = {w.connection_id: w.id for w in model.wires}
        self.at: dict[Key, list[str]] = defaultdict(list)
        for x in model.connections:
            self.at[x.start.key].append(x.id)
            self.at[x.end.key].append(x.id)
        # (owner id) -> name, board, {squashed printed marking: key}
        self.owners: list[tuple[str, str, str, dict[str, Key]]] = []
        self.label: dict[Key, str] = {}
        self.board: dict[Key, str] = {}
        self.side: dict[Key, str] = {}  # a strip point side's side; components have none
        for c in model.components:
            template = model.template(c.template_ref)
            name = c.designation.product if c.designation else c.id
            printed: dict[str, Key] = {}
            for t in template.terminals if template is not None else ():
                key = entities.TerminalRef(component=c.id, terminal=t.id).key
                printed[squash(t.marking)] = key
                self.label[key] = f"{name}:{t.marking}"
                self.board[key] = c.board_id
            self.owners.append((c.id, name, c.board_id, printed))
        for s in model.strips:
            name = s.designation.product if s.designation else s.id
            printed = {}
            for p in s.points:
                for side in entities.SIDES:
                    key = entities.TerminalRef(point=p.id, side=side).key
                    printed[squash(f"{p.marking} {side} L{p.level}")] = key
                    self.label[key] = f"{name}:{p.marking}"
                    self.board[key] = s.board_id
                    self.side[key] = side
            self.owners.append((s.id, name, s.board_id, printed))
        self.ids = sorted(set(self.connections) | set(self.wire_of.values()))

    def resolve(self, token: str) -> str | None:
        """The connection a printed ``w-``/``x-`` token names: a unique id prefix."""
        hits = [i for i in self.ids if i.startswith(token)]
        if len(hits) != 1:
            return None
        hit = hits[0]
        if hit in self.connections:
            return hit
        return next(c for c, w in self.wire_of.items() if w == hit)

    def ends(self, cid: str) -> tuple[Key, Key]:
        x = self.connections[cid]
        return x.start.key, x.end.key

    def owner_of_header(self, header: str) -> tuple[str, dict[str, Key]] | None:
        text = squash(header)
        hits = [
            o
            for o in self.owners
            if text.startswith(squash(o[1]) + "(")
            or text.startswith(squash(f"{o[2]}/{o[1]}") + "(")
        ]
        if len(hits) > 1:
            hits = [o for o in hits if _names_board(header, o[2])]
        if len(hits) != 1:
            return None
        return hits[0][1], hits[0][3]


def _find(parent: dict[Key, Key], k: Key) -> Key:
    parent.setdefault(k, k)
    while parent[k] != k:
        k = parent[k]
    return k


def _names_board(header: str, board: str) -> bool:
    """Whether a header names ``board`` as a whole token (not "MDB" inside "SMDB-1")."""
    return re.search(rf"(?<![\w-]){re.escape(board)}(?![\w-])", header) is not None


def unrealised_nets(
    model: entities.DesignModelV2,
) -> list[tuple[entities.Net, list[set[Key]]]]:
    """Nets whose own connections leave members in more than one island, with the islands."""
    out = []
    by_net: dict[str, list[entities.Connection]] = defaultdict(list)
    for x in model.connections:
        by_net[x.net_id].append(x)
    for net in model.nets:
        parent = {m.key: m.key for m in net.members}

        for x in by_net[net.id]:
            if x.start.key in parent and x.end.key in parent:
                parent[_find(parent, x.start.key)] = _find(parent, x.end.key)
        islands: dict[Key, set[Key]] = defaultdict(set)
        for k in parent:
            islands[_find(parent, k)].add(k)
        if len(islands) > 1:
            out.append((net, list(islands.values())))
    return out


# =========================================================================== #
# The PDF, read back: paths, dots, markers, lines
# =========================================================================== #


@dataclass(frozen=True)
class Dot:
    c: P
    r: float


@dataclass
class DrawnPath:
    """One stroked open path (a pdfplumber line/curve subpath), Béziers flattened."""

    pts: list[P]
    width: float
    dash: tuple[tuple[float, ...], float] | None
    obj: int
    hops: list[int] = field(default_factory=list)  # indexes of segments from Bézier pieces
    gaps: list[int] = field(default_factory=list)  # indexes of segments that bridge a gap

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in itertools.pairwise(self.pts))


@dataclass
class End:
    p: P
    dot: Dot | None = None
    key: Key | None = None
    marker: re.Match[str] | None = None


@dataclass
class Line:
    """A routed line as read: one path (or gap-joined fragments) of the routed width."""

    page: int
    path: DrawnPath
    ends: tuple[End, End]
    labels: list[tuple[str, float]] = field(default_factory=list)  # (connection id, distance)
    conn: str | None = None

    @property
    def kind(self) -> str:
        dots = sum(e.dot is not None for e in self.ends)
        marks = sum(e.dot is None and e.marker is not None for e in self.ends)
        if dots == 2:
            return "drawn"
        if dots == 1 and marks == 1:
            return "continued"
        if dots == 1:
            return "stub"
        return "orphan"


@dataclass
class RawPage:
    page: Page
    paths: list[DrawnPath]
    dots: list[Dot]
    markers: list[Box]  # small closed non-round shapes: arrow / flag candidates
    marker_shapes: list[list[P]] = field(default_factory=list)  # their outlines
    dashes: list[tuple[tuple[float, ...], float]] = field(default_factory=list)  # (array, width)
    lines: list[Line] = field(default_factory=list)
    stubs: list[Line] = field(default_factory=list)
    symbol_boxes: list[Box] = field(default_factory=list)


@dataclass
class Doc:
    pages: list[RawPage]
    route_width: float | None
    symbol_widths: set[float]

    @property
    def lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]


def _dash_of(obj: dict[str, Any]) -> tuple[tuple[float, ...], float] | None:
    dash = obj.get("dash")
    if not dash or not dash[0]:
        return None
    return tuple(float(v) for v in dash[0]), float(dash[1])


def _bezier(p0: P, p1: P, p2: P, p3: P, steps: int = 8) -> list[P]:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            (
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            )
        )
    return out


def _flatten(obj: dict[str, Any]) -> tuple[list[P], list[int]]:
    path = obj.get("path")
    if not path:
        return [(float(x), float(y)) for x, y in obj["pts"]], []
    pts: list[P] = []
    hops: list[int] = []
    for op, *args in path:
        if op in ("m", "l"):
            pts.append((float(args[0][0]), float(args[0][1])))
        elif op == "c" and pts:
            a, b, c = ((float(x), float(y)) for x, y in args)
            start = len(pts) - 1
            pts += _bezier(pts[-1], a, b, c)
            hops += list(range(start, len(pts) - 1))
        elif op == "h" and pts:
            pts.append(pts[0])
    return pts, hops


def _round(pts: Sequence[P], box: Box) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return (
        len(pts) >= 3
        and math.dist(pts[0], pts[-1]) < 0.1
        and abs(w - h) < 0.5
        and all(abs(math.dist(p, centre) - w / 2) < 0.12 * w + 0.05 for p in pts)
    )


def _raw_page(page: Page, plumber: Any) -> RawPage:
    paths: list[DrawnPath] = []
    dots: list[Dot] = []
    markers: list[Box] = []
    shapes: list[list[P]] = []
    for n, obj in enumerate([*plumber.lines, *plumber.curves]):
        pts, hops = _flatten(obj)
        if len(pts) < 2:
            continue
        box = (obj["x0"], obj["top"], obj["x1"], obj["bottom"])
        w, h = box[2] - box[0], box[3] - box[1]
        closed = math.dist(pts[0], pts[-1]) < 0.1 and len(pts) >= 3
        if closed and _round(pts, box):
            if obj.get("fill") and w < DOT_READ_MAX:
                dots.append(Dot(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), w / 2))
            continue
        if closed or obj.get("fill"):
            if max(w, h) <= MARKER_MAX:
                markers.append(box)
                shapes.append(pts if closed else [*pts, pts[0]])
            continue
        if not obj.get("stroke", True):
            continue
        paths.append(
            DrawnPath(pts, round(float(obj.get("linewidth") or 0), 2), _dash_of(obj), n, hops)
        )
    for rect in plumber.rects:
        box = (rect["x0"], rect["top"], rect["x1"], rect["bottom"])
        if max(box[2] - box[0], box[3] - box[1]) <= MARKER_MAX and rect.get("fill"):
            markers.append(box)
            x0, t, x1, b = box
            shapes.append([(x0, t), (x1, t), (x1, b), (x0, b), (x0, t)])
    return RawPage(page, paths, dots, markers, shapes)


def _symbol_extent(page: Page) -> tuple[list[Box], set[float]]:
    """Each block's symbol box, and the widths of its strokes (from the geometry reader)."""
    boxes, widths = [], set()
    for block in page.blocks:
        widths |= {s.width for s in block.strokes if isinstance(s, Seg)}
        if block.strokes:
            pts: list[P] = []
            for s in block.strokes:
                if isinstance(s, Seg):
                    pts += [s.a, s.b]
                else:
                    pts += [(s.c[0] - s.r, s.c[1] - s.r), (s.c[0] + s.r, s.c[1] + s.r)]
            xs, ys = zip(*pts, strict=True)
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes, widths


def _dot_at(raw: RawPage, p: P) -> Dot | None:
    near = [d for d in raw.dots if math.dist(d.c, p) <= d.r + EPS]
    return min(near, key=lambda d: math.dist(d.c, p)) if near else None


def _block_at(page: Page, p: P) -> Any:
    owners = [
        b
        for b in page.blocks
        if b.header.x0 - 3 * mm <= p[0] < b.right and b.header.top < p[1] < b.bottom
    ]
    return max(owners, key=lambda b: b.header.x0) if owners else None


def _stacked(upper: Run, lower: Run) -> bool:
    return (
        (abs(upper.x0 - lower.x0) < 0.6 or abs(upper.x1 - lower.x1) < 0.6)  # left / right aligned
        and abs(upper.size - lower.size) < 0.01
        and -0.1 <= lower.top - upper.bottom < 1.3 * upper.size
    )


def _stacks(runs: Sequence[Run], depth: int = 3) -> list[tuple[str, list[Run]]]:
    """Each run, and each chain of runs stacked at one left edge, with its joined text.

    Chains of up to ``depth`` runs printed one under the next at one left edge (a wrapped value), with its joined text.
    """
    ordered = sorted(runs, key=lambda r: r.top)
    out: list[tuple[str, list[Run]]] = []
    for run in ordered:
        chain = [run]
        out.append((run.text, list(chain)))
        while len(chain) < depth:
            below = [o for o in ordered if o is not chain[-1] and _stacked(chain[-1], o)]
            if len(below) != 1:
                break
            chain.append(below[0])
            out.append((" ".join(r.text for r in chain), list(chain)))
    return out


def header_text(page: Page, block: BlockRead) -> str:
    """A block title wrapped over several lines, re-joined up to its "(component)" line."""
    lines = [block.header]
    while len(lines) < 5:
        above = [r for r in page.runs if r is not lines[0] and _stacked(r, lines[0])]
        if not above:
            break
        lines.insert(0, max(above, key=lambda r: r.top))
    return "".join(r.text for r in lines[:-1]) + (" " if len(lines) > 1 else "") + lines[-1].text


def terminal_at(page: Page, p: P, index: ModelIndex) -> Key | None:
    """The terminal a dot is: its block's owner, and the marking printed on its row."""
    block = _block_at(page, p)
    if block is None:
        return None
    owner = index.owner_of_header(header_text(page, block))
    if owner is None:
        return None
    _name, printed = owner
    found: list[tuple[float, Key]] = []
    inside = [
        r
        for r in page.runs
        if block.header.bottom < r.yc < block.bottom
        and r.x0 >= block.header.x0 - 3 * mm
        and r.x1 <= block.right
    ]
    for text, stack in _stacks(inside):
        if not any(abs(r.yc - p[1]) < MARKING_ROW for r in stack):
            continue
        gap = min(max(p[0] - r.x1, r.x0 - p[0], 0.0) for r in stack)
        if gap > MARKING_REACH:
            continue
        printed_as = squash(text)
        printed_as = printed_as.removesuffix("(cont.)")  # a continued block's row
        key = printed.get(printed_as)
        if key is not None:
            off = min(abs(r.yc - p[1]) for r in stack)
            found.append((gap + off + 0.01 * len(stack), key))
    if not found:
        return None
    return min(found)[1]


def _dist_to_path(box: Box, pts: Sequence[P]) -> float:
    best = math.inf
    for a, b in itertools.pairwise(pts):
        for q in _samples(a, b):
            dx = max(box[0] - q[0], 0.0, q[0] - box[2])
            dy = max(box[1] - q[1], 0.0, q[1] - box[3])
            best = min(best, math.hypot(dx, dy))
    return best


def _samples(a: P, b: P, step: float = 1.0) -> list[P]:
    n = max(1, int(math.dist(a, b) / step))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def continued_texts(page: Page) -> list[tuple[str, Any]]:
    """Wrapped texts re-joined, without merging stacked references.

    A run continues the one above it only when it shares its left edge, sits on the next line, does not start a new "to ..." reference, and the
    line above does not end a reference with ")".
    """
    out: list[tuple[str, Any]] = []
    pending = sorted(page.runs, key=lambda r: (round(r.x0), r.top))
    used: set[int] = set()
    for n, run in enumerate(pending):
        if n in used:
            continue
        text, last = run.text, run
        for m in range(n + 1, len(pending)):
            other = pending[m]
            if m in used or abs(other.x0 - run.x0) > 0.6:
                continue
            if other.top - last.bottom >= 1.2 * last.size:
                break
            if other.top < last.bottom - 0.1:
                continue
            if other.text.startswith("to ") or last.text.rstrip().endswith(")"):
                break
            text += " " + other.text
            used.add(m)
            last = other
        out.append((text, run))
    return out


def _continuations(page: Page) -> list[tuple[re.Match[str], Box]]:
    out = []
    for run in page.runs:
        for m in CONTINUATION.finditer(run.text):
            out.append((m, (run.x0, run.top, run.x1, run.bottom)))
    for text, run in continued_texts(page):
        if text == run.text:
            continue
        for m in CONTINUATION.finditer(text):
            out.append((m, (run.x0, run.top, run.x1, run.bottom + 2 * run.size)))
    return out


def _marker_at(raw: RawPage, p: P, conts: list[tuple[re.Match[str], Box]]) -> re.Match[str] | None:
    box = (p[0], p[1], p[0], p[1])
    arrow = any(_box_gap(m, box) <= CONTINUATION_REACH for m in raw.markers)
    if not arrow:
        return None
    near = [(_box_gap(b, box), m) for m, b in conts if _box_gap(b, box) <= CONTINUATION_REACH]
    return min(near, key=lambda t: t[0])[1] if near else None


def _box_gap(a: Box, b: Box) -> float:
    dx = max(a[0] - b[2], 0.0, b[0] - a[2])
    dy = max(a[1] - b[3], 0.0, b[1] - a[3])
    return math.hypot(dx, dy)


def _join_gaps(paths: list[DrawnPath]) -> list[DrawnPath]:
    """Join fragments of one line split by a crossing gap: same width/dash, collinear, gap small."""
    pool = list(paths)
    changed = True
    while changed:
        changed = False
        for i, j in itertools.permutations(range(len(pool)), 2):
            a, b = pool[i], pool[j]
            if a.width != b.width or a.dash != b.dash:
                continue
            gap = math.dist(a.pts[-1], b.pts[0])
            if not EPS < gap <= GAP_MAX:
                continue
            da = (a.pts[-1][0] - a.pts[-2][0], a.pts[-1][1] - a.pts[-2][1])
            db = (b.pts[1][0] - b.pts[0][0], b.pts[1][1] - b.pts[0][1])
            dg = (b.pts[0][0] - a.pts[-1][0], b.pts[0][1] - a.pts[-1][1])
            if abs(da[0] * dg[1] - da[1] * dg[0]) > 0.05 * gap * math.hypot(*da):
                continue
            if abs(db[0] * dg[1] - db[1] * dg[0]) > 0.05 * gap * math.hypot(*db):
                continue
            offset = len(a.pts)
            merged = DrawnPath(
                a.pts + b.pts,
                a.width,
                a.dash,
                a.obj,
                a.hops + [h + offset for h in b.hops],
                a.gaps + [offset - 1] + [g + offset for g in b.gaps],
            )
            pool = [p for n, p in enumerate(pool) if n not in (i, j)] + [merged]
            changed = True
            break
    return pool


def read_doc(content: bytes, index: ModelIndex) -> Doc:
    """Read every page; find the routed width and the lines, ends, labels."""
    raws: list[RawPage] = []
    # One parse per page: the geometry reader and the path reader share pdfplumber's
    # parsed objects (the same result as read_pdf() and a second open, parsed once).
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for n, plumber in enumerate(pdf.pages, start=1):
            page = _read_page(n, plumber)
            raw = _raw_page(page, plumber)
            raw.paths = _join_gaps(raw.paths)
            raw.dashes = [
                (dash[0], round(float(obj.get("linewidth") or 0), 2))
                for obj in [*plumber.lines, *plumber.curves, *plumber.rects]
                if obj.get("stroke", True) and (dash := _dash_of(obj)) is not None
            ]
            raws.append(raw)
    symbol_widths: set[float] = set()
    for raw in raws:
        if raw.page.is_drawing:
            raw.symbol_boxes, widths = _symbol_extent(raw.page)
            symbol_widths |= widths
    # The routed width: the most common width of open paths joining two dots.
    counts: dict[float, int] = defaultdict(int)
    for raw in raws:
        if not raw.page.is_drawing:
            continue
        for path in raw.paths:
            both = _dot_at(raw, path.pts[0]) and _dot_at(raw, path.pts[-1])
            if both and math.dist(path.pts[0], path.pts[-1]) > EPS:
                counts[path.width] += 1
    route_width = max(sorted(counts), key=lambda w: counts[w]) if counts else None
    for n, raw in enumerate(raws, start=1):
        if not raw.page.is_drawing:
            continue
        conts = _continuations(raw.page)
        # Lead ends, per page: where the geometry reader's leads meet their symbol.
        lead_anchors = {
            (round(lead.anchor[0]), round(lead.anchor[1]))
            for block in raw.page.blocks
            for lead in block.leads
        }
        for path in raw.paths:
            ends = []
            for p in (path.pts[0], path.pts[-1]):
                end = End(p, dot=_dot_at(raw, p))
                if end.dot is not None:
                    end.key = terminal_at(raw.page, end.dot.c, index)
                else:
                    end.marker = _marker_at(raw, p, conts)
                ends.append(end)
            line = Line(n, path, (ends[0], ends[1]))
            if not any(e.dot for e in ends):
                if path.width == route_width and route_width is not None:
                    raw.lines.append(line)  # an orphan: reported, never skipped
                continue
            if _is_lead(raw, line, route_width, lead_anchors):
                continue  # a symbol lead, not a connection line
            if line.kind == "stub":
                raw.stubs.append(line)
            if path.width != route_width:
                continue
            if line.kind == "stub":
                continue
            for run in raw.page.runs:
                for m in LINE_LABEL.finditer(run.text):
                    d = _dist_to_path((run.x0, run.top, run.x1, run.bottom), path.pts)
                    cid = index.resolve(m.group(1))
                    if d <= LABEL_REACH and cid is not None:
                        line.labels.append((cid, d))
            raw.lines.append(line)
        _assign(raw.lines, index)
    return Doc(raws, route_width, symbol_widths)


def _is_lead(
    raw: RawPage, line: Line, route_width: float | None, anchors: set[tuple[int, int]]
) -> bool:
    """Whether a one-dot path is a symbol lead rather than a stub or line.

    Never the routed width, never a dotted / dash-dot stub style; a one-dot path whose far end meets a symbol (a lead anchor or a symbol box edge), or
    that heads left into the symbol drawn solid.
    """
    path = line.path
    if path.width == route_width or line.kind != "stub":
        return False
    if path.dash is not None and _dash_kind(path.dash, path.width) in (
        "dotted",
        "dash-dot",
    ):
        return False
    far = next(e for e in line.ends if e.dot is None).p
    if (round(far[0]), round(far[1])) in anchors:
        return True
    if any(_box_gap(b, (far[0], far[1], far[0], far[1])) <= EPS for b in raw.symbol_boxes):
        return True
    first = path.pts[1] if line.ends[0].dot else path.pts[-2]
    dot = next(e.dot for e in line.ends if e.dot is not None)
    return first[0] < dot.c[0] - EPS and path.dash is None


def _assign(lines: list[Line], index: ModelIndex) -> None:
    """Each line's connection: one its ends allow, named by a label near it (nearest first)."""
    for line in lines:
        cands = _candidates(line, index)
        named = sorted((d, c) for c, d in line.labels if c in cands)
        line.conn = named[0][1] if named else None


def _candidates(line: Line, index: ModelIndex) -> set[str]:
    keyed = [e.key for e in line.ends if e.key is not None]
    if line.kind == "drawn":
        if len(keyed) != 2:
            return set()
        return {
            c
            for c in index.at[keyed[0]]
            if set(index.ends(c)) == set(keyed)
            or (keyed[0] == keyed[1] and index.ends(c) == (keyed[0], keyed[0]))
        }
    if line.kind == "continued" and keyed:
        marker = next(e.marker for e in line.ends if e.marker is not None)
        assert marker is not None
        out = set()
        for c in index.at[keyed[0]]:
            a, b = index.ends(c)
            other = b if a == keyed[0] else a
            if squash(f"{index.board[other]}/{index.label[other]}") == squash(
                f"{marker['board']}/{marker['label']}"
            ):
                out.add(c)
        return out
    return set()


@functools.cache
def model_index(case: str) -> ModelIndex:
    return ModelIndex(build(case)[0])


@functools.cache
def doc(case: str) -> Doc:
    return read_doc(render(case), model_index(case))


def _drawing_texts(case: str) -> list[str]:
    return [p.page.text for p in doc(case).pages]


def unrouted_items(case: str) -> dict[str, str]:
    """``unrouted:`` open items: connection id -> the reason text after the id."""
    index = model_index(case)
    out: dict[str, str] = {}
    for raw in doc(case).pages:
        if raw.page.is_drawing:
            continue
        text = raw.page.text
        for m in UNROUTED_ITEM.finditer(text):
            rest = text[m.end() :]
            stop = re.search(r"\n[a-z_]+:", rest)
            entry = rest[: stop.start()] if stop else rest
            for token in LINE_LABEL.finditer(entry):
                cid = index.resolve(token.group(1))
                if cid is not None:
                    out[cid] = (entry[: token.start()] + entry[token.end() :]).strip()
    return out


def _fmt(p: P) -> str:
    return f"({p[0]:.1f},{p[1]:.1f})"


def _report(problems: list[str]) -> str:
    return f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def _need_lines(d: Doc, problems: list[str]) -> None:
    if d.route_width is None:
        problems.append("no routed line found: no stroked path joins two terminal dots")


# =========================================================================== #
# 1. Bijection: drawn lines <-> model connections
# =========================================================================== #


def bijection_problems(case: str) -> list[str]:
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    _need_lines(d, problems)
    if d.route_width is not None and d.route_width in d.symbol_widths:
        problems.append(f"routed stroke width {d.route_width} is also a symbol stroke width")
    drawn: dict[str, list[Line]] = defaultdict(list)
    continued: dict[str, list[Line]] = defaultdict(list)
    for line in d.lines:
        where = f"sheet {line.page} line {_fmt(line.ends[0].p)}-{_fmt(line.ends[1].p)}"
        if line.kind == "orphan":
            problems.append(f"{where}: neither end on a terminal dot")
            continue
        for e in line.ends:
            if e.dot is not None and e.key is None:
                problems.append(f"{where}: end dot at {_fmt(e.p)} is no identifiable terminal")
        if line.conn is None:
            keys = [index.label.get(e.key, "?") for e in line.ends if e.key]
            labels = sorted({c for c, _d in line.labels})
            problems.append(
                f"{where}: {line.kind} line between {keys} matches no model connection with a "
                f"label within {LABEL_REACH / mm:.0f} mm (labels near: {labels[:4]}); "
                "an invented or merged connection, or an unlabelled line"
            )
            continue
        (drawn if line.kind == "drawn" else continued)[line.conn].append(line)
    unrouted = unrouted_items(case)
    for cid, x in sorted(index.connections.items()):
        name = f"{cid} ({index.label[x.start.key]} - {index.label[x.end.key]})"
        n_drawn, n_cont, listed = len(drawn[cid]), len(continued[cid]), cid in unrouted
        ok = (n_drawn, n_cont, listed) in ((1, 0, False), (0, 2, False), (0, 0, True))
        if not ok:
            problems.append(
                f"connection {name}: drawn {n_drawn}x, continued ends {n_cont}, "
                f"unrouted item {'yes' if listed else 'no'}; expected exactly one of: one line, "
                "two continued ends, or an unrouted item"
            )
        if n_cont == 2:
            ends_on = {e.key for ln in continued[cid] for e in ln.ends if e.key}
            if ends_on != set(index.ends(cid)):
                problems.append(f"connection {name}: its continued ends sit on {ends_on}")
        if listed:
            if len(unrouted[cid]) < 3:
                problems.append(f"connection {name}: unrouted item gives no reason")
            for key in index.ends(cid):
                if not any(
                    s.ends[0].key == key or s.ends[1].key == key
                    for raw in d.pages
                    for s in raw.stubs
                ):
                    problems.append(f"unrouted {name}: no stub at {index.label[key]}")
            texts = [squash(t) for t in _drawing_texts(case)]
            for key in index.ends(cid):
                names = (f"to {index.label[key]}", f"to {index.board[key]}/{index.label[key]}")
                if not any(squash(n) in t for n in names for t in texts):
                    problems.append(f"unrouted {name}: no cross-reference names {index.label[key]}")
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_drawn_lines_and_model_connections_are_a_bijection(case: str) -> None:
    problems = bijection_problems(case)
    assert not problems, _report(problems)


# =========================================================================== #
# 2. Junctions exactly at terminals with two or more connections
# =========================================================================== #


def junction_problems(case: str) -> list[str]:
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    _need_lines(d, problems)
    radii = [dot.r for raw in d.pages if raw.page.is_drawing for dot in raw.dots]
    if not radii:
        return [*problems, "no dots at all"]
    r_t = min(radii)
    for raw in d.pages:
        if not raw.page.is_drawing:
            continue
        clusters: list[list[Dot]] = []
        for dot in sorted(raw.dots, key=lambda q: q.c):
            for cl in clusters:
                if math.dist(cl[0].c, dot.c) < 0.5:
                    cl.append(dot)
                    break
            else:
                clusters.append([dot])
        for cl in clusters:
            r = max(q.r for q in cl)
            terminal = [q for q in cl if 2 * q.r < TERMINAL_DOT_MAX]
            junction = r >= JUNCTION_RATIO * r_t - 0.01
            key = terminal_at(raw.page, cl[0].c, index)
            where = f"sheet {raw.page.number} dot {_fmt(cl[0].c)}"
            if key is None:
                if junction:
                    problems.append(f"{where}: junction dot at no terminal point")
                continue
            if not terminal and not junction:
                problems.append(f"{where}: terminal point drawn larger than {TERMINAL_DOT_MAX}")
            need = len(index.at[key]) >= 2
            if junction != need:
                problems.append(
                    f"{where} {index.label[key]}: {len(index.at[key])} connections, junction dot "
                    f"{'drawn' if junction else 'missing'}"
                )
    # Lines meet only at terminal points: no line end on another line's interior,
    # no line passing through a dot that is not its own end.
    for raw in d.pages:
        for a in raw.lines:
            own = {e.dot.c for e in a.ends if e.dot}
            for dot in raw.dots:
                if dot.c in own:
                    continue
                for p, q in itertools.pairwise(a.path.pts):
                    box = (
                        dot.c[0] - dot.r,
                        dot.c[1] - dot.r,
                        dot.c[0] + dot.r,
                        dot.c[1] + dot.r,
                    )
                    if through_box(p, q, box) > EPS:
                        problems.append(
                            f"sheet {raw.page.number}: line {a.conn} passes through the dot at "
                            f"{_fmt(dot.c)}"
                        )
                        break
            for b in raw.lines:
                if a is b:
                    continue
                for e in a.ends:
                    if e.dot is not None:
                        continue
                    if any(meet(p, q, e.p, e.p) for p, q in itertools.pairwise(b.path.pts)):
                        problems.append(
                            f"sheet {raw.page.number}: line {a.conn} ends on line {b.conn} at "
                            f"{_fmt(e.p)}, not at a terminal"
                        )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_junction_dots_are_exactly_at_terminals_with_two_or_more_connections(
    case: str,
) -> None:
    problems = junction_problems(case)
    assert not problems, _report(problems)


# =========================================================================== #
# 3. Crossings are proper; lines avoid symbols and text
# =========================================================================== #


def _axis(a: P, b: P) -> str | None:
    if abs(a[1] - b[1]) < 0.05:
        return "h"
    if abs(a[0] - b[0]) < 0.05:
        return "v"
    return None


#: More than meet()'s own tolerance (0.05): segments whose boxes are this far apart share no point.
_APART = 0.1


def _apart(p1: P, p2: P, q1: P, q2: P) -> bool:
    """Whether two segments' bounding boxes are further apart than meet() could join."""
    return (
        min(p1[0], p2[0]) > max(q1[0], q2[0]) + _APART
        or min(q1[0], q2[0]) > max(p1[0], p2[0]) + _APART
        or min(p1[1], p2[1]) > max(q1[1], q2[1]) + _APART
        or min(q1[1], q2[1]) > max(p1[1], p2[1]) + _APART
    )


def crossing_problems(lines: Sequence[Line], dots: Sequence[Dot], sheet: int) -> list[str]:
    """Any shared point of two lines of different connections must be a proper crossing."""
    problems: list[str] = []
    for a, b in itertools.combinations(lines, 2):
        if a.conn is not None and a.conn == b.conn:
            continue
        shared_dots = {e.dot.c for e in a.ends if e.dot} & {e.dot.c for e in b.ends if e.dot}
        for i, (p1, p2) in enumerate(itertools.pairwise(a.path.pts)):
            for j, (q1, q2) in enumerate(itertools.pairwise(b.path.pts)):
                if _apart(p1, p2, q1, q2):
                    continue
                hits = meet(p1, p2, q1, q2)
                if not hits:
                    continue
                at = hits[0]
                if any(math.dist(at, c) < CROSSING_DOT_CLEAR + 1.5 for c in shared_dots):
                    if len(hits) == 2 and math.dist(*hits) > 2 * mm:
                        problems.append(
                            f"sheet {sheet}: lines {a.conn} and {b.conn} overlap from their "
                            f"shared terminal at {_fmt(hits[0])}-{_fmt(hits[1])}"
                        )
                    continue  # they meet at their common terminal: a junction
                name = f"sheet {sheet}: lines {a.conn} and {b.conn} at {_fmt(at)}"
                if len(hits) == 2 and math.dist(*hits) > EPS:
                    problems.append(f"{name}: collinear overlap to {_fmt(hits[1])}")
                    continue
                ax, bx = _axis(p1, p2), _axis(q1, q2)
                hop = (i in a.path.hops and bx is not None) or (j in b.path.hops and ax is not None)
                if not hop and not (ax and bx and ax != bx):
                    problems.append(f"{name}: not an orthogonal crossing ({ax}, {bx})")
                ends = [a.path.pts[0], a.path.pts[-1], b.path.pts[0], b.path.pts[-1]]
                if any(math.dist(at, e) < 0.5 * mm for e in ends):
                    problems.append(f"{name}: a line ends on the other (touch, not crossing)")
                    continue
                corners = [p1, p2] if i not in a.path.hops else []
                corners += [q1, q2] if j not in b.path.hops else []
                if not hop and any(math.dist(at, c) < EPS for c in corners):
                    problems.append(f"{name}: they touch at a bend")
                if any(math.dist(at, d.c) < CROSSING_DOT_CLEAR + d.r for d in dots):
                    problems.append(f"{name}: a dot is drawn on the crossing")
    return problems


def avoidance_problems(raw: RawPage) -> list[str]:
    problems = []
    inks = [(ch, _ink(ch)) for ch in raw.page.chars]
    for line in raw.lines:
        own = [e.dot.c for e in line.ends if e.dot]
        for p, q in itertools.pairwise(line.path.pts):
            # A segment can only run through a box its bounding box meets: skipping the
            # others changes no result, only the cost.
            sx0, sx1 = min(p[0], q[0]), max(p[0], q[0])
            sy0, sy1 = min(p[1], q[1]), max(p[1], q[1])
            for box in raw.symbol_boxes:
                inner = (box[0] + EPS, box[1] + EPS, box[2] - EPS, box[3] - EPS)
                if through_box(p, q, inner) > EPS:
                    problems.append(
                        f"sheet {raw.page.number}: line {line.conn} runs through a symbol body "
                        f"{_fmt(box[:2])}"
                    )
            for ch, ink in inks:
                if ink[0] >= sx1 or ink[2] <= sx0 or ink[1] >= sy1 or ink[3] <= sy0:
                    continue  # through_box() is 0: the box's open interior is out of reach
                if through_box(p, q, ink) > 0.1 and not any(
                    _box_gap(ink, (c[0], c[1], c[0], c[1])) < 0.5 for c in own
                ):
                    problems.append(
                        f"sheet {raw.page.number}: line {line.conn} crosses text {ch['text']!r} at "
                        f"({ch['x0']:.1f},{ch['top']:.1f})"
                    )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_crossings_are_orthogonal_and_lines_avoid_symbols_and_text(case: str) -> None:
    d = doc(case)
    problems: list[str] = []
    _need_lines(d, problems)
    for raw in d.pages:
        problems += crossing_problems(raw.lines, raw.dots, raw.page.number)
        problems += avoidance_problems(raw)
    assert not problems, _report(problems)


# =========================================================================== #
# 4. Unrealised nets stay unrealised
# =========================================================================== #


@pytest.mark.parametrize("case", ALL_CASES)
def test_unrealised_net_members_get_no_line_and_keep_their_note(case: str) -> None:
    model, index, d = build(case)[0], model_index(case), doc(case)
    problems: list[str] = []
    _need_lines(d, problems)
    # The drawn graph (by line ends) must not join what the model does not join.
    parent: dict[Key, Key] = {}

    def find(k: Key) -> Key:
        return _find(parent, k)

    for line in d.lines:
        keys = [e.key for e in line.ends if e.key is not None]
        if line.kind == "drawn" and len(keys) == 2:
            parent[find(keys[0])] = find(keys[1])
    hay = [squash(t) for raw in d.pages for t in (raw.page.text, *raw.page.paragraphs)]
    for net, islands in unrealised_nets(model):
        for one, other in itertools.combinations(islands, 2):
            for k1, k2 in itertools.product(one, other):
                if find(k1) == find(k2):
                    problems.append(
                        f"net {net.signal}: {index.label[k1]} and {index.label[k2]} are joined by "
                        "drawn lines but by no connection of the model"
                    )
        note = squash(f"UNREALISED: net {net.signal}")
        if not any(note in h for h in hay):
            problems.append(f"net {net.signal} ({net.id}): its UNREALISED note is gone")
    # Same signal, different nets: never joined.
    net_of = {m.key: n.id for n in model.nets for m in n.members}
    for line in d.lines:
        keys = [e.key for e in line.ends if e.key is not None]
        if len(keys) == 2 and net_of.get(keys[0]) != net_of.get(keys[1]):
            problems.append(
                f"sheet {line.page}: a line joins {index.label[keys[0]]} and "
                f"{index.label[keys[1]]} of different nets"
            )
    assert not problems, _report(problems)


# =========================================================================== #
# 5. Cross-sheet continuations pair up
# =========================================================================== #


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_continuation_pairs_with_its_other_end(case: str) -> None:
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    _need_lines(d, problems)
    conts = [ln for ln in d.lines if ln.kind == "continued"]
    pages = {raw.page.number: raw for raw in d.pages}
    # Never vacuous: every flag-like text must be among the continuations paired here.
    flagged = sum(len(flag_texts(raw.page)) for raw in d.pages if raw.page.is_drawing)
    if flagged > len(conts):
        problems.append(
            f"{flagged} continuation-like texts on the sheets, only {len(conts)} read as "
            "continuations: the pairing below would be vacuous for the rest"
        )
    for line in conts:
        here = next(e for e in line.ends if e.dot is not None)
        marker = next(e.marker for e in line.ends if e.marker is not None)
        assert marker is not None
        where = f"sheet {line.page} {marker[0]!r}"
        target_sheet, zone = int(marker["sheet"]), marker["zone"]
        target = pages.get(target_sheet)
        if target is None or not target.page.is_drawing:
            problems.append(f"{where}: names no drawing sheet")
            continue
        back = [
            o
            for o in target.lines
            if o.kind == "continued"
            and o.conn is not None
            and o.conn == line.conn
            and o is not line
        ]
        if not back:
            problems.append(
                f"{where}: no continuation of the same connection on sheet {target_sheet}"
            )
            continue
        other = back[0]
        there = next(e for e in other.ends if e.dot is not None)
        got = target.page.zone_of(*there.p)
        if got != zone:
            problems.append(
                f"{where}: the other end is drawn in zone {got} on sheet {target_sheet}"
            )
        if here.key is not None and line.conn is not None:
            a, b = index.ends(line.conn)
            if {here.key, there.key} != {a, b}:
                problems.append(f"{where}: the two ends are not the connection's two terminals")
        back_marker = next(e.marker for e in other.ends if e.marker is not None)
        assert back_marker is not None
        mine = pages[line.page].page.zone_of(*here.p)
        if int(back_marker["sheet"]) != line.page or back_marker["zone"] != mine:
            problems.append(
                f"{where}: the other end's marker {back_marker[0]!r} does not point back to "
                f"sheet {line.page}, zone {mine}"
            )
    assert not problems, _report(problems)


_FLAG_START = re.compile(r"^to [^\s/()]+/")
_SHEET_ZONE = re.compile(r"\((?:sheet \d+|this sheet), zone \d+[A-Z]\)")
#: How close to the inner frame a flag-like text sits.
FLAG_EDGE = 20 * mm


def flag_texts(page: Page) -> list[tuple[str, Any]]:
    """Board-qualified "to <board>/..." texts near the frame edge.

    Wrapped lines are re-joined (a following line continues it unless it starts a new "to ...", and only until a ")").
    """
    if page.inner is None or not page.is_routed:
        # Flags are drawn on routed sheets only: a cross-reference sheet's "to <board>/..."
        # texts (board-qualified far ends) are references, read by their own tests.
        return []
    ix0, it, ix1, ib = page.inner
    out = []
    joined = {id(run): text for text, run in continued_texts(page)}
    for run in page.runs:
        if not _FLAG_START.match(run.text):
            continue
        near = (
            run.x0 - ix0 < FLAG_EDGE
            or ix1 - run.x1 < FLAG_EDGE
            or run.top - it < FLAG_EDGE
            or ib - run.bottom < FLAG_EDGE
        )
        if near:
            out.append((joined.get(id(run), run.text), run))
    return out


def flag_problems(case: str) -> list[str]:
    problems: list[str] = []
    everything = "\n".join(raw.page.text for raw in doc(case).pages)
    for raw in doc(case).pages:
        if not raw.page.is_drawing:
            continue
        for text, run in flag_texts(raw.page):
            where = f"sheet {raw.page.number} at ({run.x0:.0f},{run.top:.0f})"
            if CONTINUATION.match(text):
                continue
            cut = TRUNCATION_MARKER.search(text)
            if cut is not None and _SHEET_ZONE.search(text[cut.end() :]):
                if not has_note(everything, cut.group(1)):
                    problems.append(f"{where}: flag marker {cut.group(1)} has no full text")
                continue
            problems.append(
                f"{where}: incomplete continuation flag {text[:120]!r}: neither a whole "
                "'to <board>/<label> (sheet n, zone XY)' nor a resolvable …[n] marker with its "
                "(sheet n, zone XY) intact"
            )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_continuation_flag_is_complete(case: str) -> None:
    problems = flag_problems(case)
    assert not problems, _report(problems)


@pytest.mark.parametrize("case", ALL_CASES)
def test_continuations_are_read_wherever_a_connection_leaves_its_sheet(
    case: str,
) -> None:
    """Non-vacuity: a connection that leaves its sheet has readable flags.

    Every connection not drawn as one line (and not unrouted) must have two readable continued ends, and every flag-like text must be read as a continuation.
    """
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    drawn = {ln.conn for ln in d.lines if ln.kind == "drawn" and ln.conn}
    unrouted = unrouted_items(case)
    ends: dict[str, int] = defaultdict(int)
    for ln in d.lines:
        if ln.kind == "continued" and ln.conn:
            ends[ln.conn] += 1
    for cid in sorted(index.connections):
        if cid in drawn or cid in unrouted:
            continue
        if ends[cid] != 2:
            x = index.connections[cid]
            problems.append(
                f"connection {cid} ({index.label[x.start.key]} - {index.label[x.end.key]}) is "
                f"neither drawn nor unrouted, and {ends[cid]} of its 2 ends carry a readable "
                "continuation flag"
            )
    for raw in d.pages:
        if not raw.page.is_drawing:
            continue
        flags = len(flag_texts(raw.page))
        read = sum(1 for ln in raw.lines if ln.kind == "continued" and ln.conn)
        if flags > read:
            problems.append(
                f"sheet {raw.page.number}: {flags} continuation-like texts, {read} read as "
                "identified continuations"
            )
    assert not problems, _report(problems)


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_model_connection_is_accounted_for(case: str) -> None:
    """Bijection completeness, loudly: every model connection is accounted for.

    Drawn + continued + unrouted covers every connection, and no routed-width path is left unidentified.
    """
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    _need_lines(d, problems)
    unidentified = [ln for ln in d.lines if ln.conn is None]
    for ln in unidentified:
        problems.append(
            f"sheet {ln.page}: {ln.kind} line {_fmt(ln.ends[0].p)}-{_fmt(ln.ends[1].p)} "
            "cannot be identified with a model connection"
        )
    accounted = {ln.conn for ln in d.lines if ln.conn} | set(unrouted_items(case))
    missing = sorted(set(index.connections) - accounted)
    if missing:
        problems.append(
            f"{len(missing)} of {len(index.connections)} model connections are neither drawn, "
            f"continued nor unrouted: {missing[:8]}"
        )
    assert not problems, _report(problems)


# =========================================================================== #
# 6. Schedules and drawing agree
# =========================================================================== #


@pytest.mark.parametrize("case", ALL_CASES)
def test_drawn_continued_and_unrouted_equal_the_schedules(case: str) -> None:
    model, index, d = build(case)[0], model_index(case), doc(case)
    problems: list[str] = []
    _need_lines(d, problems)
    wire_rows = schedules.wire_schedule(model)
    terminal_rows = schedules.terminal_schedule(model)
    from_wires = {r.connection_id for r in wire_rows}
    # Links without a wire record (jumper / internal / ...), as the terminal schedule lists them.
    pairs: dict[frozenset[str], int] = defaultdict(int)
    for row in terminal_rows:
        name = row.designation.product if row.designation else row.owner_id
        for peer in row.connected_to:
            pairs[frozenset((f"{name}:{row.marking}", peer))] += 1
    scheduled = set(from_wires)
    for cid, x in index.connections.items():
        if cid in from_wires:
            continue
        pair = frozenset((index.label[x.start.key], index.label[x.end.key]))
        if pairs.get(pair, 0) >= 1:
            scheduled.add(cid)
    shown = {ln.conn for ln in d.lines if ln.conn is not None} | set(unrouted_items(case))
    if shown != scheduled:
        problems.append(
            f"drawn+continued+unrouted minus schedules: {sorted(shown - scheduled)[:6]}; "
            f"schedules minus drawing: {sorted(scheduled - shown)[:6]}"
        )
    wire_of = {r.connection_id: r.wire_id for r in wire_rows}
    for line in d.lines:
        if line.conn is None:
            continue
        tokens = {
            m.group(1)
            for run in d.pages[line.page - 1].page.runs
            for m in LINE_LABEL.finditer(run.text)
            if _dist_to_path((run.x0, run.top, run.x1, run.bottom), line.path.pts) <= LABEL_REACH
        }
        want = wire_of.get(line.conn)
        if want is not None and not any(want.startswith(t) and t.startswith("w-") for t in tokens):
            problems.append(
                f"sheet {line.page}: line of {line.conn} is not labelled with wire {want}"
            )
        if want is None and not any(line.conn.startswith(t) for t in tokens):
            problems.append(f"sheet {line.page}: line of {line.conn} (no wire) lacks its x- label")
    assert not problems, _report(problems)


# =========================================================================== #
# 7. Disclaimer and review marks
# =========================================================================== #


@pytest.mark.parametrize("case", ALL_CASES)
def test_cover_says_routing_is_schematic_and_every_page_is_marked(case: str) -> None:
    _model, digest = build(case)
    pages = [raw.page for raw in doc(case).pages]
    assert SCHEMATIC_NOTE.search(
        " ".join(pages[0].text.split())
    ), "the cover/legend does not state that line paths are schematic, not physical"
    for page in pages:
        text = squash(page.text)
        for mark in REVIEW_MARKS:
            assert squash(mark) in text, (page.number, mark)
        assert digest in text, page.number


# =========================================================================== #
# 8. Stub styles
# =========================================================================== #


def _dash_kind(dash: tuple[tuple[float, ...], float], width: float) -> str:
    on = dash[0][0::2]
    short = max(1.5 * max(width, 0.1), 0.6)
    if all(v <= short for v in on):
        return "dotted"
    if len(dash[0]) >= 4 and any(v <= short for v in on) and any(v > short for v in on):
        return "dash-dot"
    return "dashed"


def stub_styles(case: str) -> dict[str, set[tuple[float, ...]]]:
    out: dict[str, set[tuple[float, ...]]] = defaultdict(set)
    for raw in doc(case).pages:
        for stub in raw.stubs:
            if stub.path.dash is not None:
                out[_dash_kind(stub.path.dash, stub.path.width)].add(stub.path.dash[0])
    return out


@pytest.mark.parametrize("case", ALL_CASES)
def test_dotted_and_dash_dot_stubs_are_long_enough_and_distinct(case: str) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        for stub in raw.stubs:
            if stub.path.dash is None:
                continue
            kind = _dash_kind(stub.path.dash, stub.path.width)
            if kind in ("dotted", "dash-dot") and stub.path.length < STUB_MIN - EPS:
                problems.append(
                    f"sheet {raw.page.number}: {kind} stub at {_fmt(stub.path.pts[0])} is "
                    f"{stub.path.length / mm:.1f} mm long"
                )
    styles = stub_styles(case)
    if styles["dotted"] & styles["dash-dot"]:
        problems.append("a dotted and a dash-dot stub share a dash array")
    assert not problems, _report(problems)


def test_both_stub_styles_occur_and_differ() -> None:
    seen: dict[str, set[tuple[float, ...]]] = defaultdict(set)
    for case in ALL_CASES:
        for kind, arrays in stub_styles(case).items():
            seen[kind] |= arrays
    assert seen["dotted"], "no dotted stub read from any case's PDF dash arrays"
    assert seen["dash-dot"], "no dash-dot stub read from any case's PDF dash arrays"
    assert not seen["dotted"] & seen["dash-dot"]


# =========================================================================== #
# 9. Readability
# =========================================================================== #


@pytest.mark.parametrize("case", ALL_CASES)
@pytest.mark.parametrize(("primary", "secondary"), [(PRIMARY_PT, SECONDARY_PT)])
def test_text_is_readable_and_no_label_overlaps_another_line(
    case: str, primary: float, secondary: float
) -> None:
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    _need_lines(d, problems)
    for raw in d.pages:
        for c in raw.page.chars:
            minimum = secondary if is_secondary(c) else primary
            if c["size"] < minimum - 1e-6:
                problems.append(f"sheet {raw.page.number}: {c['text']!r} at {c['size']:.2f} pt")
        for run in raw.page.runs:
            tokens = [index.resolve(m.group(1)) for m in LINE_LABEL.finditer(run.text)]
            is_cont = CONTINUATION.search(run.text) is not None
            if not tokens and not is_cont:
                continue
            owns = {t for t in tokens if t is not None}
            box = (
                run.x0,
                run.top + 0.25 * run.size,
                run.x1,
                run.bottom - 0.15 * run.size,
            )
            for line in raw.lines:
                if line.conn in owns:
                    continue  # its own line or leader
                if any(through_box(p, q, box) > 0.1 for p, q in itertools.pairwise(line.path.pts)):
                    problems.append(
                        f"sheet {raw.page.number}: label {run.text[:40]!r} overlaps line {line.conn}"
                    )
    assert not problems, _report(problems)


# =========================================================================== #
# 10. Determinism
# =========================================================================== #

DETERMINISM_CASES = [
    "config:drawing_control_relay",
    "route:dense_grid",
    "route:strip_links",
]


@pytest.mark.parametrize("case", DETERMINISM_CASES)
def test_two_renders_are_byte_identical(case: str) -> None:
    assert render_fresh(case) == render_fresh(case)


def test_renders_are_identical_across_hash_seeds() -> None:
    script = (
        "import hashlib, sys\n"
        "from app.tests.design.test_routed_topology import render_fresh\n"
        "for case in sys.argv[1:]:\n"
        "    print(case, hashlib.sha256(render_fresh(case)).hexdigest())\n"
    )
    api = Path(__file__).resolve().parents[3]
    outputs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", script, *DETERMINISM_CASES],
            cwd=api,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        outputs.append(done.stdout)
    here = "".join(
        f"{c} {hashlib.sha256(render_fresh(c)).hexdigest()}\n" for c in DETERMINISM_CASES
    )
    assert outputs[0] == outputs[1] == outputs[2] == here


# =========================================================================== #
# The readers themselves, on hand-drawn lines
# =========================================================================== #


def _line(points: Iterable[P], conn: str, hops: list[int] | None = None) -> Line:
    pts = list(points)
    path = DrawnPath(pts, 1.0, None, 0, hops or [])
    return Line(1, path, (End(pts[0]), End(pts[-1])), conn=conn)


def test_the_crossing_checker_tells_proper_crossings_from_bad_ones() -> None:
    h = _line([(0, 50), (100, 50)], "x-a")
    assert not crossing_problems([h, _line([(50, 0), (50, 100)], "x-b")], [], 1)
    diagonal = crossing_problems([h, _line([(0, 0), (100, 100)], "x-b")], [], 1)
    assert any("not an orthogonal" in p for p in diagonal), diagonal
    overlap = crossing_problems([h, _line([(20, 50), (60, 50), (60, 90)], "x-b")], [], 1)
    assert any("collinear overlap" in p for p in overlap), overlap
    touch = crossing_problems([h, _line([(50, 0), (50, 50)], "x-b")], [], 1)
    assert any("ends on the other" in p for p in touch), touch
    dotted = crossing_problems([h, _line([(50, 0), (50, 100)], "x-b")], [Dot((50, 50), 1)], 1)
    assert any("dot is drawn" in p for p in dotted), dotted
    hop = _line([(0, 50), (47, 50), (48, 47), (52, 47), (53, 50), (100, 50)], "x-a", [1, 2, 3])
    assert not crossing_problems([hop, _line([(50, 0), (50, 100)], "x-b")], [], 1)


def test_the_path_reader_flattens_curves_and_joins_gaps() -> None:
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    canvas = Canvas(buf, pagesize=(200, 200))
    canvas.setLineWidth(0.7)
    p = canvas.beginPath()
    p.moveTo(10, 100)
    p.lineTo(50, 100)
    p.curveTo(52, 104, 56, 104, 58, 100)
    p.lineTo(90, 100)
    canvas.drawPath(p, stroke=1, fill=0)
    canvas.line(100, 100, 120, 100)
    canvas.line(122, 100, 150, 100)
    canvas.circle(10, 100, 1.0, stroke=0, fill=1)
    canvas.save()
    with pdfplumber.open(io.BytesIO(buf.getvalue())) as pdf:
        page = read_pdf(buf.getvalue())[0]
        raw = _raw_page(page, pdf.pages[0])
    assert len(raw.dots) == 1
    assert raw.dots[0].r == pytest.approx(1.0, abs=0.05)
    curved = next(q for q in raw.paths if q.pts[0][0] == pytest.approx(10))
    assert curved.hops
    assert curved.pts[-1][0] == pytest.approx(90)
    joined = _join_gaps([q for q in raw.paths if q.pts[0][0] >= 100])
    assert len(joined) == 1
    assert joined[0].length == pytest.approx(50, abs=0.1)  # 20 + gap 2 + 28


def test_label_tokens_resolve_by_unique_prefix() -> None:
    index = model_index("route:hub")
    cid = sorted(index.connections)[0]
    assert index.resolve(cid[:10]) == cid
    wire = index.wire_of[cid]
    assert index.resolve(wire) == cid
    assert index.resolve("x-") is None


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_dot_is_identified_even_under_wrapped_titles_and_markings(
    case: str,
) -> None:
    """Every dot on a drawing sheet resolves to one model terminal.

    Header and marking are read however many lines they wrap over; an
    unidentifiable dot fails loudly.
    """
    d, index = doc(case), model_index(case)
    problems = [
        f"sheet {raw.page.number}: dot {_fmt(dot.c)} is no identifiable terminal"
        for raw in d.pages
        if raw.page.is_drawing
        for dot in raw.dots
        if terminal_at(raw.page, dot.c, index) is None
    ]
    assert not problems, _report(problems)


def test_wrapped_titles_are_rejoined() -> None:
    build("sample:long_names")
    names = {o[1] for o in model_index("sample:long_names").owners}
    longest = max(names, key=len)
    headers = [
        header_text(raw.page, b)
        for raw in doc("sample:long_names").pages
        if raw.page.is_drawing
        for b in raw.page.blocks
    ]
    assert any(squash(longest) in squash(h) for h in headers), headers[:5]
    assert any(len(h) > 60 and h.count(" ") >= 1 for h in headers)


# =========================================================================== #
# 11. Print-scale legibility, continuation endpoints, schedule PDFs, regressions
# =========================================================================== #
#
# The thresholds below are parameters of the review contract, not renderer
# constants. A3 sheets are often printed on A4 "fit to page": every length then
# shrinks by A4_FIT (297/420 = 0.707).

#: A3 -> A4 fit-to-page scale.
A4_FIT = 297 / 420
#: The shortest dash gap (and dash) still visible on paper, after A4_FIT.
PRINT_GAP_MIN = 0.4 * mm
#: Two dash patterns of different meaning must differ by at least this, after A4_FIT.
PATTERN_DIFF_MIN = 0.4 * mm
#: A crossing hop's rise off its own line, or each side of a crossing gap, after A4_FIT.
CROSSING_VISIBLE_MIN = 0.4 * mm
#: The clearance between any text ink and an arrowhead / flag shape, at A3 100 %.
ARROW_TEXT_CLEAR = 0.2 * mm
#: The largest piece of an arrowhead drawn as stroked lines at a stub end.
ARROW_PIECE_MAX = 2.5 * mm
#: The least pitch of adjacent terminal rows in one block, at A3 100 %.
ROW_PITCH_MIN = 3.5 * mm

_SIDE_SUFFIX = re.compile(r"^, (?P<side>inner|outer) side")


def _seg_box_gap(a: P, b: P, box: Box) -> float:
    """The distance from a segment to a box (0 when it touches or enters it)."""
    if through_box(a, b, box) > 0:
        return 0.0
    best = math.inf
    for q in _samples(a, b, 0.1):
        dx = max(box[0] - q[0], 0.0, q[0] - box[2])
        dy = max(box[1] - q[1], 0.0, q[1] - box[3])
        best = min(best, math.hypot(dx, dy))
    return best


def _inside(p: P, poly: Sequence[P]) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in itertools.pairwise(poly):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _shape_box_gap(poly: Sequence[P], box: Box) -> float:
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    if _inside(centre, poly):
        return 0.0
    return min(_seg_box_gap(a, b, box) for a, b in itertools.pairwise(poly))


# --- 11a. Crossings are visibly a hop or a gap ----------------------------- #


def crossing_visibility_problems(lines: Sequence[Line], sheet: int) -> list[str]:
    """Every crossing of different connections has a visible hop or a visible gap.

    A hop: the crossing lies on a Bézier piece of one line, at least
    CROSSING_VISIBLE_MIN / A4_FIT off that line's own axis. A gap: the crossing
    lies in a bridged gap of one line, with at least CROSSING_VISIBLE_MIN / A4_FIT
    of open paper on each side of the other line. Two lines meeting plainly fail.
    """
    need = CROSSING_VISIBLE_MIN / A4_FIT
    problems: list[str] = []
    for a, b in itertools.combinations(lines, 2):
        if a.conn is not None and a.conn == b.conn:
            continue
        shared = {e.dot.c for e in a.ends if e.dot} & {e.dot.c for e in b.ends if e.dot}
        hits: list[tuple[P, Line, int, Line, int]] = []
        for i, (p1, p2) in enumerate(itertools.pairwise(a.path.pts)):
            for j, (q1, q2) in enumerate(itertools.pairwise(b.path.pts)):
                if _apart(p1, p2, q1, q2):
                    continue
                got = meet(p1, p2, q1, q2)
                if len(got) == 1:
                    hits.append((got[0], a, i, b, j))
        for at, la, i, lb, j in hits:
            if any(math.dist(at, c) < CROSSING_DOT_CLEAR + 1.5 for c in shared):
                continue
            ends = [la.path.pts[0], la.path.pts[-1], lb.path.pts[0], lb.path.pts[-1]]
            if any(math.dist(at, e) < 0.5 * mm for e in ends):
                continue  # a touch: the crossing test reports it
            ok = False
            for line, k in ((la, i), (lb, j)):
                if k in line.path.hops:
                    ok = ok or _hop_rise(line.path, k, at) >= need - 0.01
                if k in line.path.gaps:
                    g1, g2 = line.path.pts[k], line.path.pts[k + 1]
                    ok = ok or min(math.dist(at, g1), math.dist(at, g2)) >= need - 0.01
            if not ok:
                problems.append(
                    f"sheet {sheet}: lines {la.conn} and {lb.conn} cross at {_fmt(at)} with no "
                    f"hop or gap visible at {CROSSING_VISIBLE_MIN / mm:.1f} mm after A4 fit"
                )
    return problems


def _hop_rise(path: DrawnPath, k: int, at: P) -> float:
    """How far a point on hop piece ``k`` is off the straight line the hop interrupts."""
    first = k
    while first - 1 in path.hops:
        first -= 1
    last = k
    while last + 1 in path.hops:
        last += 1
    a, b = path.pts[first], path.pts[last + 1]
    length = math.dist(a, b)
    if length < 1e-9:
        return 0.0
    return abs((b[0] - a[0]) * (at[1] - a[1]) - (b[1] - a[1]) * (at[0] - a[0])) / length


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_crossing_shows_a_visible_hop_or_gap(case: str) -> None:
    d = doc(case)
    problems: list[str] = []
    _need_lines(d, problems)
    for raw in d.pages:
        problems += crossing_visibility_problems(raw.lines, raw.page.number)
    assert not problems, _report(problems)


def test_the_visibility_checker_tells_hops_and_gaps_from_plain_crossings() -> None:
    v = _line([(50, 0), (50, 100)], "x-b")
    plain = _line([(0, 50), (100, 50)], "x-a")
    assert crossing_visibility_problems([plain, v], 1)
    hop = _line([(0, 50), (47, 50), (48, 47), (52, 47), (53, 50), (100, 50)], "x-a", [1, 2, 3])
    assert not crossing_visibility_problems([hop, v], 1)
    flat = _line(
        [(0, 50), (47, 50), (48, 49.8), (52, 49.8), (53, 50), (100, 50)],
        "x-a",
        [1, 2, 3],
    )
    assert crossing_visibility_problems([flat, v], 1)
    gap = _line([(0, 50), (48, 50), (52, 50), (100, 50)], "x-a")
    gap.path.gaps = [1]
    assert not crossing_visibility_problems([gap, v], 1)
    narrow = _line([(0, 50), (49.6, 50), (50.4, 50), (100, 50)], "x-a")
    narrow.path.gaps = [1]
    assert crossing_visibility_problems([narrow, v], 1)


# --- 11b. Line patterns at A4 fit-to-page ---------------------------------- #


@functools.cache
def dash_patterns(case: str) -> dict[str, set[tuple[float, ...]]]:
    """Every dash array stroked on a drawing sheet, by meaning.

    Meanings: "link" (a dashed routed line), "dotted" and "dash-dot" stubs, and
    "outline" (any other dashed stroke: placeholders, frames).
    """
    d = doc(case)
    out: dict[str, set[tuple[float, ...]]] = defaultdict(set)
    for raw in d.pages:
        if not raw.page.is_drawing:
            continue
        for array, width in raw.dashes:
            kind = _dash_kind((array, 0.0), width)
            if width == d.route_width and kind == "dashed":
                kind = "link"
            elif kind == "dashed":
                kind = "outline"
            out[kind].add(array)
    return out


def _pattern_differs(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    if len(a) != len(b):
        return True
    diff = max(abs(x - y) for x, y in zip(a, b, strict=True))
    return bool(diff * A4_FIT >= PATTERN_DIFF_MIN)


def dash_problems(patterns: dict[str, set[tuple[float, ...]]]) -> list[str]:
    problems = []
    for kind, arrays in sorted(patterns.items()):
        for array in sorted(arrays):
            gaps = array[1::2] if len(array) > 1 else array
            short = min(gaps) * A4_FIT
            if short < PRINT_GAP_MIN - 1e-6:
                problems.append(
                    f"{kind} dash {array}: a gap of {short / mm:.2f} mm at A4 fit, under "
                    f"{PRINT_GAP_MIN / mm:.1f} mm"
                )
    kinds = sorted(patterns)
    for k1, k2 in itertools.combinations(kinds, 2):
        for a, b in itertools.product(sorted(patterns[k1]), sorted(patterns[k2])):
            if not _pattern_differs(a, b):
                problems.append(f"{k1} {a} and {k2} {b} are indistinguishable at A4 fit")
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_line_patterns_stay_visible_and_distinct_at_a4_fit(case: str) -> None:
    problems = dash_problems(dash_patterns(case))
    assert not problems, _report(problems)


def test_the_dash_checker_rejects_tiny_gaps_and_lookalike_patterns() -> None:
    assert dash_problems({"dotted": {(0.6, 0.8)}})
    assert dash_problems({"link": {(5.0, 2.0)}, "outline": {(5.2, 2.1)}})
    assert not dash_problems({"link": {(5.0, 2.0)}, "outline": {(2.0, 2.0)}})


def test_every_line_meaning_occurs_somewhere() -> None:
    """Non-vacuity: the patterns checked above include each meaning at least once."""
    seen: set[str] = set()
    for case in ALL_CASES:
        seen |= set(dash_patterns(case))
    assert {"link", "dotted", "dash-dot"} <= seen, seen


# --- 11c. No text on an arrowhead or marker -------------------------------- #


def arrowheads(raw: RawPage) -> list[list[P]]:
    """Flag and arrow shapes: filled marker outlines, and stroked arrow pieces at stub ends."""
    shapes = list(raw.marker_shapes)
    tips = [e.p for s in raw.stubs for e in s.ends if e.dot is None]
    for path in raw.paths:
        if path.length > ARROW_PIECE_MAX:
            continue
        if any(math.dist(path.pts[0], t) < EPS or math.dist(path.pts[-1], t) < EPS for t in tips):
            shapes.append(path.pts)
    return shapes


def arrow_text_problems(raw: RawPage) -> list[str]:
    problems = []
    for shape in arrowheads(raw):
        xs, ys = zip(*shape, strict=True)
        near = (min(xs) - 2 * mm, min(ys) - 2 * mm, max(xs) + 2 * mm, max(ys) + 2 * mm)
        for ch in raw.page.chars:
            if not ch["text"].strip():
                continue
            ink = _ink(ch)
            if _box_gap(ink, near) > 0:
                continue
            gap = _shape_box_gap(shape, ink)
            if gap < ARROW_TEXT_CLEAR:
                problems.append(
                    f"sheet {raw.page.number}: text {ch['text']!r} at ({ch['x0']:.1f},"
                    f"{ch['top']:.1f}) is {gap / mm:.2f} mm from an arrowhead/marker at "
                    f"{_fmt(shape[0])}"
                )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_text_touches_an_arrowhead_or_marker(case: str) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        if raw.page.is_drawing:
            problems += arrow_text_problems(raw)
    assert not problems, _report(problems)


def test_arrowheads_are_found() -> None:
    """Non-vacuity: some case has marker shapes and stroked stub arrows to check."""
    flags = arrows = 0
    for case in ALL_CASES:
        for raw in doc(case).pages:
            if raw.page.is_drawing:
                flags += len(raw.marker_shapes)
                arrows += len(arrowheads(raw)) - len(raw.marker_shapes)
    assert flags, (flags, arrows)
    assert arrows, (flags, arrows)


# --- 11d. Terminal row pitch ----------------------------------------------- #


def row_pitch_problems(raw: RawPage, minimum: float) -> list[str]:
    """Adjacent terminal points of one block, in one column, at least ``minimum`` apart."""
    columns: dict[tuple[int, int], list[float]] = defaultdict(list)
    for dot in raw.dots:
        block = _block_at(raw.page, dot.c)
        if block is None:
            continue
        ys = columns[(id(block), round(dot.c[0] * 2))]
        if not any(abs(y - dot.c[1]) < 0.5 for y in ys):
            ys.append(dot.c[1])
    problems = []
    for (_b, x2), ys in columns.items():
        ys.sort()
        for y1, y2 in itertools.pairwise(ys):
            if y2 - y1 < minimum - 0.01:
                problems.append(
                    f"sheet {raw.page.number}: terminal rows at x={x2 / 2:.1f}, y={y1:.1f} and "
                    f"{y2:.1f} are {(y2 - y1) / mm:.2f} mm apart, under {minimum / mm:.1f} mm"
                )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
@pytest.mark.parametrize("minimum", [ROW_PITCH_MIN])
def test_adjacent_terminal_rows_keep_their_pitch(case: str, minimum: float) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        if raw.page.is_drawing:
            problems += row_pitch_problems(raw, minimum)
    assert not problems, _report(problems)


# --- 11e. Continuations name the right far terminal ------------------------ #


def far_terminal(marker: re.Match[str], index: ModelIndex) -> list[Key]:
    """The model terminals a continuation's "to <board>/<name>:<marking>[, <side> side]" names."""
    after = _SIDE_SUFFIX.match(marker.string[marker.end() :])
    side = after.group("side") if after else None
    want = squash(f"{marker['board']}/{marker['label']}")
    return [
        k
        for k, label in index.label.items()
        if squash(f"{index.board[k]}/{label}") == want and index.side.get(k) == side
    ]


@pytest.mark.parametrize("case", ALL_CASES)
def test_each_continuation_names_its_far_board_component_and_terminal(
    case: str,
) -> None:
    """Each continued end's flag resolves to exactly one terminal: the connection's far end.

    Board, component (or strip) and marking, and a strip point's side, all count:
    alike designations on other boards and the two sides of a point must not be
    confused. Each connection's two continued ends name each other.
    """
    d, index = doc(case), model_index(case)
    problems: list[str] = []
    by_conn: dict[str, list[Line]] = defaultdict(list)
    for line in d.lines:
        if line.kind != "continued":
            continue
        here = next(e for e in line.ends if e.dot is not None)
        marker = next(e.marker for e in line.ends if e.marker is not None)
        assert marker is not None
        where = f"sheet {line.page} {marker[0]!r}"
        named = far_terminal(marker, index)
        if len(named) != 1:
            problems.append(f"{where}: names {len(named)} model terminals, not one")
            continue
        if line.conn is None or here.key is None:
            problems.append(f"{where}: its line or its own terminal is not identified")
            continue
        a, b = index.ends(line.conn)
        far = b if here.key == a else a
        if here.key not in (a, b) or named[0] != far:
            problems.append(
                f"{where}: drawn at {index.label.get(here.key, '?')}, names "
                f"{index.board[named[0]]}/{index.label[named[0]]}; the connection's far end is "
                f"{index.board[far]}/{index.label[far]}"
            )
        by_conn[line.conn].append(line)
    for cid, lines in sorted(by_conn.items()):
        keys = {e.key for ln in lines for e in ln.ends if e.dot is not None}
        if len(lines) != 2 or keys != set(index.ends(cid)):
            problems.append(f"connection {cid}: continued ends {len(lines)}, at {keys}")
    assert not problems, _report(problems)


# --- 11f. Drawing and schedule PDFs agree ---------------------------------- #


@functools.cache
def schedule_pdfs(case: str) -> tuple[bytes, bytes]:
    """The terminal and wire schedule PDFs of the case's model, through the public functions."""
    model, digest = build(case)
    header = schedule_pdf.ScheduleHeader(
        f"Synthetic {case}",
        "01",
        digest,
        "pp-design/2",
        "rules-under-test",
        "renderer-under-test",
    )
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(model)
    ]
    terminal = schedule_pdf.render_terminal_schedule(
        schedules.terminal_schedule(model), header, _MARK, structural
    )
    wire = schedule_pdf.render_wire_schedule(
        schedules.wire_schedule(model), header, _MARK, structural
    )
    return terminal, wire


@functools.cache
def schedule_tables(case: str) -> tuple[list[list[str]], list[list[str]], str, str]:
    """Each schedule PDF's table rows (cells squashed) and its whole text."""
    out: list[Any] = []
    texts: list[str] = []
    for content in schedule_pdfs(case):
        rows: list[list[str]] = []
        text = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text.append(page.extract_text() or "")
                for table in page.extract_tables():
                    rows += [[squash(c or "") for c in row] for row in table]
        out.append(rows)
        texts.append("\n".join(text))
    return out[0], out[1], texts[0], texts[1]


def schedule_pdf_problems(case: str) -> list[str]:
    d, index = doc(case), model_index(case)
    _model, digest = build(case)
    terminal_rows, wire_rows, terminal_text, wire_text = schedule_tables(case)
    problems: list[str] = []
    for name, text in (("terminal", terminal_text), ("wire", wire_text)):
        if digest not in squash(text):
            problems.append(f"the {name} schedule PDF does not print the design hash {digest}")
    if not all(digest in squash(raw.page.text) for raw in d.pages):
        problems.append("a drawing page does not print the design hash")
    # Wire schedule rows: board, wire, from, to.
    wires = {r[1]: r for r in wire_rows if len(r) >= 4 and r[1].startswith("w-")}
    peers: dict[tuple[str, str], str] = {}
    for r in terminal_rows:
        if len(r) >= 7 and r[0] and not r[0].startswith("Board"):
            peers[(r[0], r[1].split("(")[0] + ":" + r[2])] = r[6]
    for line in d.lines:
        if line.conn is None:
            continue
        keys = [e.key for e in line.ends if e.key is not None]
        marker = next((e.marker for e in line.ends if e.marker is not None), None)
        if marker is not None:
            named = far_terminal(marker, index)
            keys += named[:1]
        where = f"sheet {line.page} line {line.conn}"
        if len(keys) != 2:
            continue  # unidentified ends are reported by the bijection test
        tokens = {
            m.group(1)
            for run in d.pages[line.page - 1].page.runs
            for m in LINE_LABEL.finditer(run.text)
            if _dist_to_path((run.x0, run.top, run.x1, run.bottom), line.path.pts) <= LABEL_REACH
        }
        wire_tokens = sorted(
            t for t in tokens if t.startswith("w-") and index.resolve(t) == line.conn
        )
        if wire_tokens:
            rows = [r for w, r in wires.items() if any(w.startswith(t) for t in wire_tokens)]
            if len(rows) != 1:
                problems.append(f"{where}: wire {wire_tokens} is in {len(rows)} wire PDF rows")
                continue
            row = rows[0]
            drawn = {squash(index.label[k]) for k in keys}
            listed = {row[2], row[3]}
            if not all(any(lab in cell for cell in listed) for lab in drawn):
                problems.append(f"{where}: drawn between {sorted(drawn)}, wire PDF row {row[:4]}")
            if {squash(index.board[k]) for k in keys} != {row[0]}:
                problems.append(f"{where}: board differs from the wire PDF row {row[:4]}")
        for k, other in ((keys[0], keys[1]), (keys[1], keys[0])):
            label = squash(index.label[k])
            name, _, mark = label.rpartition(":")
            hits = [
                v
                for (board, owner_mark), v in peers.items()
                if board == squash(index.board[k]) and owner_mark.startswith(name + ":" + mark)
            ]
            if not any(squash(index.label[other]) in v for v in hits):
                problems.append(
                    f"{where}: no terminal PDF row of {index.board[k]}/{index.label[k]} lists "
                    f"{index.label[other]}"
                )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_drawing_and_schedule_pdfs_agree(case: str) -> None:
    """The drawing PDF and the terminal and wire schedule PDFs, as printed, agree.

    For each drawn or continued line: the same board, component (or strip),
    terminal marking and wire id in the wire schedule PDF's row, the far
    terminal in each end's terminal schedule PDF row, and the same design hash
    on every PDF.
    """
    problems = schedule_pdf_problems(case)
    assert not problems, _report(problems)


# --- 11g. Regressions and the adversarial cases themselves ----------------- #


def test_the_adversarial_models_are_what_they_claim() -> None:
    model, _ = build("route:both_sides")
    cbs = next(c for c in model.components if c.designation and c.designation.product == "Kbs")
    hub = entities.TerminalRef(component=cbs.id, terminal="t000").key
    starts = sum(x.start.key == hub for x in model.connections)
    ends = sum(x.end.key == hub for x in model.connections)
    assert starts >= 3
    assert ends >= 3
    model, _ = build("route:same_designation")
    names = defaultdict(set)
    for c in model.components:
        assert c.designation is not None
        names[c.designation.product].add(c.board_id)
    assert len(names["K1"]) == 3
    model, _ = build("route:jumper_every_point")
    jumpers = [x for x in model.connections if x.via == "jumper"]
    points = {k for x in jumpers for k in (x.start.key, x.end.key)}
    strip = next(s for s in model.strips if s.designation and s.designation.product == "Xjp")
    assert {p.id for p in strip.points} == {k[1] for k in points}
    # KFAR's four peers lie on cross-reference sheets other than its own (three of four
    # at least). The routed sheets regroup blocks by connection, so there they may meet.
    d = doc("route:all_off_sheet")
    sheet_of: dict[str, int] = {}
    for raw in d.pages:
        if raw.page.is_drawing and not raw.page.is_routed:
            for block in raw.page.blocks:
                sheet_of.setdefault(header_text(raw.page, block).split(" (")[0], raw.page.number)
    peers = ["Koc00", "Koc39", "Koc20", "Koc30"]
    assert "KFAR" in sheet_of, sheet_of
    assert all(p in sheet_of for p in peers), sheet_of
    assert sum(sheet_of[p] != sheet_of["KFAR"] for p in peers) >= 3, sheet_of


def test_nets_across_boards_are_rejected_by_the_codec() -> None:
    """A connection between boards is not valid pp-design/2: the codec refuses it."""
    spec = Spec(comps=[("xa", B, 1), ("xb", "DB-XB", 1)], boards=["DB-XB"])
    config = _configuration(spec)
    pattern = next(p for p in config["patterns"] if p["ref"].endswith("-db-v/v1"))
    pattern["slots"].append({"id": "sxb", "template": "tenant/synth-route-xb/v1"})
    pattern["nets"].append(
        {
            "role": "r999",
            "signal": "SYN-XB",
            "scope": "binding",
            "members": [
                {"slot": "sxa", "terminal": "t000"},
                {"slot": "sxb", "terminal": "t000"},
            ],
            "connections": [{"from": 0, "to": 1, "via": "wire"}],
        }
    )
    binding = next(b for b in config["bindings"] if b["board"] == B)
    binding["slots"]["sxb"] = {"component": "cxb"}
    with pytest.raises(compose.CompositionRefusedError, match="board_mismatch"):
        write.snapshot_v2_from_v1(_v1_with(spec.boards), config)


def knot_problems(lines: Sequence[Line], sheet: int) -> list[str]:
    """No line doubles back on itself or crosses itself (the earlier knots of jogs)."""
    problems = []
    for line in lines:
        pts = [p for n, p in enumerate(line.path.pts) if n == 0 or p != line.path.pts[n - 1]]
        for (a, b), (_b, c) in itertools.pairwise(itertools.pairwise(pts)):
            u, v = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
            cross = abs(u[0] * v[1] - u[1] * v[0])
            if cross < 1e-6 * (math.hypot(*u) * math.hypot(*v) + 1e-9) and (
                u[0] * v[0] + u[1] * v[1] < 0
            ):
                problems.append(f"sheet {sheet}: line {line.conn} doubles back at {_fmt(b)}")
        segs = list(itertools.pairwise(pts))
        for i, j in itertools.combinations(range(len(segs)), 2):
            if j - i < 2 or _apart(*segs[i], *segs[j]):
                continue
            if meet(*segs[i], *segs[j]):
                problems.append(f"sheet {sheet}: line {line.conn} crosses itself")
                break
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_line_is_knotted(case: str) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        problems += knot_problems(raw.lines, raw.page.number)
    assert not problems, _report(problems)


def test_the_knot_checker_finds_reversals_and_self_crossings() -> None:
    assert knot_problems([_line([(0, 0), (10, 0), (5, 0)], "x-a")], 1)
    assert knot_problems([_line([(0, 0), (10, 0), (10, 5), (5, 5), (5, -5)], "x-a")], 1)
    assert not knot_problems([_line([(0, 0), (10, 0), (10, 5), (20, 5)], "x-a")], 1)


def test_regression_junction_dots_on_continued_rows() -> None:
    """A terminal with two or more connections gets its junction dot even when they continue.

    Over every case (the earlier defect drew a plain terminal dot on a continued
    row); never vacuous: some continued row of such a terminal must exist.
    """
    problems = []
    checked = 0
    for case in ALL_CASES:
        d, index = doc(case), model_index(case)
        radii = [dot.r for raw in d.pages if raw.page.is_drawing for dot in raw.dots]
        if not radii:
            continue
        r_t = min(radii)
        for line in d.lines:
            if line.kind != "continued":
                continue
            here = next(e for e in line.ends if e.dot is not None)
            if here.key is None or here.dot is None or len(index.at[here.key]) < 2:
                continue
            raw = d.pages[line.page - 1]
            big = max(q.r for q in raw.dots if math.dist(q.c, here.dot.c) < 0.5)
            checked += 1
            if big < JUNCTION_RATIO * r_t - 0.01:
                problems.append(f"{case} sheet {line.page} {index.label[here.key]}: no junction")
    if not checked:
        problems.append("no continued row of a multi-connection terminal in any case")
    assert not problems, _report(problems)


@pytest.mark.parametrize("case", ["route:long_designations", "sample:long_names"])
def test_regression_continuation_flags_are_never_cut(case: str) -> None:
    """The earlier cut flags: every flag-like text is whole, or a resolvable marker."""
    problems = flag_problems(case)
    texts = [t for raw in doc(case).pages if raw.page.is_drawing for t, _r in flag_texts(raw.page)]
    if not texts:
        problems.append("no continuation flag to check")
    assert not problems, _report(problems)


# --- 11h. Lines arrive on their dot's row; labels keep apart --------------- #

#: A line's last horizontal run may sit this far off its end dot's row.
ARRIVAL_ROW_TOL = 0.5 * mm
#: The longest final vertical jog into a dot from a horizontal off the dot's row.
ARRIVAL_JOG_MAX = 1 * mm
#: The least vertical gap between two line labels / stub texts whose x-ranges overlap.
LABEL_GAP_MIN = 1.5 * mm
_NOT_ROUTED = re.compile(r"not routed")


def arrival_problems(raw: RawPage) -> list[str]:
    """Each routed line reaches its end dot along the dot's own row.

    From each dot end, the final vertical pieces (the jog into the dot) are summed up
    to the first horizontal piece; that horizontal must be within ARRIVAL_ROW_TOL of
    the dot's y, or the jog no longer than ARRIVAL_JOG_MAX. Otherwise the horizontal
    points at the gap between two rows.
    """
    problems = []
    for line in raw.lines:
        pts = [p for n, p in enumerate(line.path.pts) if n == 0 or p != line.path.pts[n - 1]]
        for end, seq in ((line.ends[0], pts), (line.ends[1], pts[::-1])):
            if end.dot is None:
                continue
            jog = 0.0
            for a, b in itertools.pairwise(seq):
                axis = _axis(a, b)
                if axis == "v":
                    jog += math.dist(a, b)
                    continue
                if axis == "h":
                    off = abs(a[1] - end.dot.c[1])
                    if off > ARRIVAL_ROW_TOL and jog > ARRIVAL_JOG_MAX:
                        problems.append(
                            f"sheet {raw.page.number}: line {line.conn} reaches its dot at "
                            f"{_fmt(end.dot.c)} by a {jog / mm:.1f} mm vertical from a horizontal "
                            f"{off / mm:.1f} mm off the dot's row"
                        )
                break  # a hop or diagonal: not a plain arrival, left to the other checks
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_line_arrives_on_its_dots_row(case: str) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        if raw.page.is_drawing:
            problems += arrival_problems(raw)
    assert not problems, _report(problems)


def test_the_arrival_checker_finds_a_jog_between_rows() -> None:
    def at(points: list[P]) -> RawPage:
        line = _line(points, "x-a")
        line.ends = (End(points[0], dot=Dot(points[0], 1.0)), End(points[-1]))
        page = RawPage(read_pdf(_blank_pdf())[0], [], [], [])
        page.lines = [line]
        return page

    assert not arrival_problems(at([(0, 0), (50, 0), (50, 40)]))
    assert not arrival_problems(at([(0, 0), (0, 2), (50, 2)]))  # a 0.7 mm jog
    assert arrival_problems(at([(0, 0), (0, 8), (50, 8)]))  # a 2.8 mm jog


@functools.cache
def _blank_pdf() -> bytes:
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    canvas = Canvas(buf, pagesize=(100, 100))
    canvas.drawString(10, 10, "x")
    canvas.save()
    return buf.getvalue()


def label_runs(page: Page) -> list[Run]:
    """Line labels (w-…, x-…) and stub texts ("… not routed") as printed runs."""
    return [r for r in page.runs if LINE_LABEL.search(r.text) or _NOT_ROUTED.search(r.text)]


def label_gap_problems(page: Page, minimum: float) -> list[str]:
    problems = []
    runs = sorted(label_runs(page), key=lambda r: r.top)
    for a, b in itertools.combinations(runs, 2):
        if min(a.x1, b.x1) <= max(a.x0, b.x0):
            continue  # x-ranges do not overlap
        gap = max(b.top - a.bottom, a.top - b.bottom)
        if gap < minimum - 0.01:
            problems.append(
                f"sheet {page.number}: {a.text[:30]!r} at ({a.x0:.0f},{a.top:.0f}) and "
                f"{b.text[:30]!r} at ({b.x0:.0f},{b.top:.0f}) are {gap / mm:.2f} mm apart "
                f"vertically, under {minimum / mm:.1f} mm"
            )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
@pytest.mark.parametrize("minimum", [LABEL_GAP_MIN])
def test_line_labels_and_stub_texts_keep_apart(case: str, minimum: float) -> None:
    problems: list[str] = []
    for raw in doc(case).pages:
        if raw.page.is_drawing:
            problems += label_gap_problems(raw.page, minimum)
    assert not problems, _report(problems)


def test_labels_are_found() -> None:
    """Non-vacuity: routed sheets carry line labels and stub texts to check."""
    labels = stubs = 0
    for case in ALL_CASES:
        for raw in doc(case).pages:
            if raw.page.is_routed:
                runs = label_runs(raw.page)
                labels += len(runs)
                stubs += sum(bool(_NOT_ROUTED.search(r.text)) for r in runs)
    assert labels
    assert stubs
