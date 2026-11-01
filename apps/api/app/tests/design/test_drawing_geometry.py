"""Adversarial geometry tests of the connection drawing PDF (M4), read from the PDF alone.

Every check here reads the *rendered* PDF with ``pdfplumber`` (``page.lines``,
``page.rects``, ``page.curves`` for what is drawn, ``page.chars`` for what is
printed) and compares it with the ``pp-design/2`` model. Nothing reads the
renderer's private constants, its lead router or its layout objects, so the
tests hold for any renderer that draws the same drawing.

Cases: every committed v2 configuration, plus synthetic stress models made
here (composed through ``write.snapshot_v2_from_v1``, so ``codec_v2`` checks
them): 80 terminals on one component, anchors on all four edges in interleaved
row order, very long tokens with no spaces, long names, many boards with few
devices, and many legacy devices with no terminals.

How drawn things are told apart, from the PDF alone
---------------------------------------------------

* **Terminal point**: a filled, closed, round curve smaller than 3 mm.
* **Lead**: the stroked path that leaves a terminal point to its *left* (the
  symbol side), followed point to point. A lead drawn as one path is taken
  whole; a lead drawn as separate segments is chained while exactly one
  continuation of the same stroke width and dash meets the current end. Its
  far end is its *anchor*.
* **Block**: from a header line (text holding ``(component)`` or ``(strip)``)
  down to the next header in the same column, or to the inner frame.
* **Symbol strokes** of a block: every other stroked line, rectangle, circle
  or path lying wholly inside the block, left of its terminal points, and
  inside the inner frame. Their bounding box is the symbol's drawn extent.
* **Frame**: the largest stroked rectangle is the outer frame, the largest
  inside it the inner drawing frame; other large rectangles outside the inner
  frame are the title block.
* **Zone grid**: the single digits 1-8 and letters A-F printed between the
  outer and inner frame name the zones; the short tick lines there bound
  them (or, without ticks, the midpoints between the labels).
* **Secondary text**: printed in a neutral grey fill; all else is primary.

Limits: a renderer that draws leads as separate segments with the same width
and dash as the symbol, meeting the symbol at a pin with no other branch,
would have the pin's symbol line absorbed into the lead. A block header that
does not hold ``(component)`` / ``(strip)`` is not found (then the leads
check fails, because every point must have its block). Wrapped text is
re-joined only within a paragraph (lines with the same left edge, one under
the other) or within a page's extracted text.
"""

from __future__ import annotations

import copy
import functools
import io
import itertools
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
import pytest
from reportlab.lib.units import mm

from app.design import connection_drawing_pdf, marking, snapshot
from app.design.model import connection_drawing, entities, findings_v2, write
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case
from app.tests.design.test_m4_consistency import expected_open_items

# --------------------------------------------------------------------------- #
# Thresholds: the one place they are set
# --------------------------------------------------------------------------- #

#: The smallest primary text, in points.
PRIMARY_MIN_PT = 6.0
#: The smallest secondary (grey) detail text, in points.
SECONDARY_MIN_PT = 5.0
#: The clear space kept between any text and the inner frame line.
FRAME_CLEARANCE = 0.5 * mm
#: What must follow a visible ellipsis: a reference to where the full value is.
TRUNCATION_MARKER = re.compile(r"…\s*(\[[^\]\s]{1,8}\]|[*†‡]\d{0,4}|\^\d{1,4})")
#: How far a terminal's marking may be printed from its point, and off its row.
MARKING_REACH = 10 * mm
MARKING_ROW = 3 * mm
#: Below this, two drawn points are the same point.
EPS = 0.3

# --------------------------------------------------------------------------- #
# Stress models, as configuration data
# --------------------------------------------------------------------------- #

_PROVENANCE = {"source": "synthetic test data", "author": "test suite", "created": "2026-11-01"}
_REVIEW: dict[str, Any] = {
    "status": "unreviewed",
    "reviewer": None,
    "reviewed_at": None,
    "standards": [],
    "scope_note": None,
}
_COMMENT = "SYNTHETIC TEST DATA for drawing geometry; no value here is an engineering value."


@dataclass
class Block:
    """One generated component, its own strip and one wired net per terminal."""

    key: str
    board: str
    designation: str | None
    markings: list[str]
    signals: list[str]
    wire_numbers: list[str]
    points: list[str]
    strip_designation: str | None
    symbol: dict[str, Any] | None = None


def _symbol(
    width: int, height: int, shapes: list[dict[str, Any]], anchors: list[dict[str, Any]]
) -> dict[str, Any]:
    shapes = sorted(shapes, key=lambda s: (s["kind"], tuple(s["points"]), s["text"] or ""))
    return {
        "width": width,
        "height": height,
        "shapes": shapes,
        "anchors": sorted(anchors, key=lambda a: a["terminal"]),
    }


def _line(*p: int) -> dict[str, Any]:
    return {"kind": "line", "points": list(p), "text": None}


def _rect(*p: int) -> dict[str, Any]:
    return {"kind": "rect", "points": list(p), "text": None}


def _configuration(blocks: Sequence[Block]) -> dict[str, Any]:
    templates, patterns, strips, components, bindings = [], [], [], [], []
    for b in blocks:
        tref = f"tenant/synth-geom-{b.key}/v1"
        pref = f"tenant/synth-geom-p-{b.key}/v1"
        terminals = [
            {"id": f"t{n:03d}", "marking": m, "kind": "signal", "required": True, "group": None}
            for n, m in enumerate(b.markings)
        ]
        template: dict[str, Any] = {
            "ref": tref,
            "function": "relay",
            "terminals": terminals,
            "contact_groups": [],
            "parameters": [],
            "provenance": _PROVENANCE,
            "review": _REVIEW,
            "restricted_scope": [],
        }
        if b.symbol is not None:
            template["symbol"] = b.symbol
        templates.append(template)
        points = [
            {"id": f"p{n:03d}", "strip": "x", "offset": n + 1, "level": 1, "kind": "signal"}
            for n in range(len(b.markings))
        ]
        nets = [
            {
                "role": f"r{n:03d}",
                "signal": b.signals[n],
                "scope": "binding",
                "members": [
                    {"slot": "c", "terminal": f"t{n:03d}"},
                    {"point": f"p{n:03d}", "side": "inner"},
                ],
                "connections": [{"from": 0, "to": 1, "via": "wire"}],
            }
            for n in range(len(b.markings))
        ]
        patterns.append(
            {
                "ref": pref,
                "slots": [{"id": "c", "template": tref}],
                "strips": ["x"],
                "points": points,
                "nets": nets,
                "provenance": _PROVENANCE,
                "review": _REVIEW,
            }
        )
        strip_id = f"X{b.key}"
        strips.append(
            {
                "id": strip_id,
                "board": b.board,
                "designation": (
                    None
                    if b.strip_designation is None
                    else {"function": None, "location": None, "product": b.strip_designation}
                ),
                "template": None,
            }
        )
        components.append(
            {
                "id": f"c{b.key}",
                "board": b.board,
                "template": tref,
                "designation": (
                    None
                    if b.designation is None
                    else {"function": None, "location": None, "product": b.designation}
                ),
            }
        )
        bindings.append(
            {
                "id": f"bind-{b.key}",
                "pattern": pref,
                "board": b.board,
                "circuit": None,
                "slots": {"c": {"component": f"c{b.key}"}},
                "strips": {"x": strip_id},
                "markings": {f"p{n:03d}": b.points[n] for n in range(len(b.markings))},
                "cable": None,
                "wires": {
                    f"r{n:03d}/0": {"number": {"value": b.wire_numbers[n], "source": "user"}}
                    for n in range(len(b.markings))
                },
            }
        )
    return {
        "name": "synthetic drawing geometry stress",
        "_comment": _COMMENT,
        "templates": sorted(templates, key=lambda t: t["ref"]),
        "patterns": sorted(patterns, key=lambda p: p["ref"]),
        "strips": strips,
        "components": components,
        "bindings": bindings,
    }


def _v1(base: str = "residential") -> dict[str, Any]:
    v1: dict[str, Any] = copy.deepcopy(DESIGNED[base]["stored"]["design_snapshot"])
    return v1


def _block(key: str, count: int, board: str = "DB-V", **kw: Any) -> Block:
    values: dict[str, Any] = {
        "designation": f"K{key}",
        "markings": [f"{n + 1}" for n in range(count)],
        "signals": [f"SYN-{key}-{n:03d}" for n in range(count)],
        "wire_numbers": [f"SYN-W{key}-{n:03d}" for n in range(count)],
        "points": [f"{n + 1}" for n in range(count)],
        "strip_designation": f"X{key}",
    }
    values.update(kw)
    return Block(key=key, board=board, **values)


def stress_many_terminals() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """80 terminals on one component, half on the left edge, half on the right."""
    count = 80
    anchors = [
        {"terminal": f"t{n:03d}", "x": 0 if n < 40 else 10, "y": 1 + 2 * (n % 40)}
        for n in range(count)
    ]
    shapes = [_rect(2, 0, 6, 81), _line(2, 40, 8, 40)]
    block = _block("mt", count, symbol=_symbol(10, 81, shapes, anchors))
    return _v1(), _configuration([block])


def stress_four_edges() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Anchors on all four edges, interleaved in row order: the worst case for crossings."""
    count = 16
    anchors = []
    shapes = [_rect(2, 2, 4, 4)]
    for n in range(count):
        edge, at = n % 4, 1 + 2 * (n // 4)
        x, y = [(at, 0), (8, at), (at, 8), (0, at)][edge]
        anchors.append({"terminal": f"t{n:03d}", "x": x, "y": y})
    shapes += [_line(3, 0, 3, 2), _line(0, 4, 2, 4), _line(5, 6, 5, 8), _line(6, 3, 8, 3)]
    block = _block("fe", count, symbol=_symbol(8, 8, shapes, anchors))
    return _v1(), _configuration([block])


def _token(prefix: str, n: int, length: int) -> str:
    body = f"{prefix}{n:03d}" + "".join(chr(ord("A") + (n * 7 + i) % 26) for i in range(length))
    return body[:length]


def stress_long_tokens() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Designations, markings, signals, wire numbers and points of 60-100 characters, no spaces."""
    count = 6
    block = _block(
        "lt",
        count,
        designation=_token("SYN-DES-", 0, 100),
        markings=[_token("M", n, 40) for n in range(count)],
        signals=[_token("SYN-SIG-", n, 100) for n in range(count)],
        wire_numbers=[_token("SYN-WIRE-", n, 80) for n in range(count)],
        points=[_token("PT", n, 60) for n in range(count)],
        strip_designation=_token("SYN-STRIP-", 0, 90),
        symbol=_symbol(
            6,
            8,
            [_rect(1, 1, 4, 6)],
            [{"terminal": f"t{n:03d}", "x": 6, "y": 1 + n} for n in range(count)],
        ),
    )
    return _v1(), _configuration([block])


def _words(prefix: str, n: int, length: int) -> str:
    words = ["synthetic", "drawing", "check", "value", "longer", "label", "spaced"]
    out = f"{prefix} {n:03d}"
    k = n
    while len(out) < length:
        out += " " + words[k % len(words)]
        k += 3
    return out[:length].rstrip()


def stress_long_names() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Long spaced names on 30 terminals; every strip point's outer side is an open item."""
    count = 30
    block = _block(
        "ln",
        count,
        designation=_words("SYN DESIGNATION", 0, 90),
        markings=[f"M{n:02d}" for n in range(count)],
        signals=[_words("SYN-SIGNAL", n, 60 + n) for n in range(count)],
        wire_numbers=[_words("SYN-WIRE", n, 60 + n) for n in range(count)],
        points=[_words("PT", n, 60 + n) for n in range(count)],
        strip_designation=_words("SYN STRIP", 0, 95),
        symbol=_symbol(
            4,
            31,
            [_rect(1, 0, 2, 31)],
            [{"terminal": f"t{n:03d}", "x": 4, "y": 1 + n} for n in range(count)],
        ),
    )
    return _v1(), _configuration([block])


def stress_many_boards() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Twelve boards with one legacy device and one small relay each."""
    v1 = _v1()
    template = v1["boards"][0]
    device = next(d for d in template["devices"] if d["upstream_id"] is not None)
    blocks = []
    for n in range(12):
        board_id = f"DB-S{n:02d}"
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
        anchors = [{"terminal": f"t{k:03d}", "x": [0, 6][k % 2], "y": 1 + 2 * k} for k in range(4)]
        blocks.append(
            _block(
                f"b{n:02d}",
                4,
                board=board_id,
                symbol=_symbol(6, 8, [_rect(1, 0, 4, 8)], anchors),
            )
        )
    return v1, _configuration(blocks)


def stress_legacy_devices() -> tuple[dict[str, Any], dict[str, Any] | None]:
    """120 legacy devices with no terminals on one board; no configuration."""
    v1 = _v1()
    board = v1["boards"][0]
    device = next(d for d in board["devices"] if d["upstream_id"] is not None)
    for n in range(120):
        board["devices"].append(
            {
                **copy.deepcopy(device),
                "id": f"legacy-{n:03d}",
                "upstream_id": device["upstream_id"],
                "designation": {
                    "function": board["id"],
                    "location": None,
                    "product": f"Q{100 + n}",
                },
            }
        )
    return v1, None


STRESS: dict[str, Callable[[], tuple[dict[str, Any], dict[str, Any] | None]]] = {
    "many_terminals": stress_many_terminals,
    "four_edges": stress_four_edges,
    "long_tokens": stress_long_tokens,
    "long_names": stress_long_names,
    "many_boards": stress_many_boards,
    "legacy_devices": stress_legacy_devices,
}

CONFIG_CASES = [f"config:{name}" for name in sorted(CONFIGS)]
STRESS_CASES = [f"stress:{name}" for name in STRESS]
ALL_CASES = CONFIG_CASES + STRESS_CASES
LONG_CASES = ["stress:long_names", "stress:long_tokens"]


@functools.cache
def build(case: str) -> tuple[entities.DesignModelV2, str]:
    kind, _, name = case.partition(":")
    configuration: dict[str, Any] | None
    if kind == "config":
        v1, configuration = config_case(name)
    else:
        v1, configuration = STRESS[name]()
    stored = write.snapshot_v2_from_v1(v1, configuration)
    model = read_snapshot(stored).v2
    assert model is not None
    return model, snapshot.sha256_of(stored)


_MARK = marking.ExportMark(label=marking.DRAFT_LABEL, identity="Synthetic, revision 01")


@functools.cache
def render(case: str) -> bytes:
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


def test_the_stress_models_are_what_they_claim() -> None:
    model, _ = build("stress:many_terminals")
    assert max(len(model.template(c.template_ref).terminals) for c in model.components) == 80  # type: ignore[union-attr]
    model, _ = build("stress:many_boards")
    assert len({c.board_id for c in model.components}) >= 12
    model, _ = build("stress:legacy_devices")
    assert sum(not model.template(c.template_ref).terminals for c in model.components) >= 120  # type: ignore[union-attr]
    assert len(sheets_of("stress:many_terminals")) >= 2


# --------------------------------------------------------------------------- #
# The PDF, read back
# --------------------------------------------------------------------------- #

P = tuple[float, float]  # (x, top): pdfplumber's top-down page coordinates
Box = tuple[float, float, float, float]  # x0, top, x1, bottom


@dataclass(frozen=True)
class Run:
    """Characters printed together: same baseline and size, no gap between them."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class Seg:
    a: P
    b: P
    width: float
    dash: str
    obj: int


@dataclass(frozen=True)
class Circle:
    c: P
    r: float
    obj: int


@dataclass
class Lead:
    dot: P
    segs: list[Seg]

    @property
    def anchor(self) -> P:
        return self.segs[-1].b


@dataclass
class BlockRead:
    header: Run
    bottom: float
    right: float  # the next column group's left, or the page's
    dots: list[P] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)
    strokes: list[Seg | Circle] = field(default_factory=list)


@dataclass
class Page:
    number: int
    chars: list[dict[str, Any]]
    runs: list[Run]
    paragraphs_at: list[tuple[str, Run]]
    text: str
    segs: list[Seg]
    circles: list[Circle]
    dots: list[P]
    outer: Box | None
    inner: Box | None
    titles: list[Box]
    columns: list[tuple[float, str]] = field(default_factory=list)  # (right bound, number)
    rows: list[tuple[float, str]] = field(default_factory=list)  # (bottom bound, letter)
    zone_labels: set[int] = field(default_factory=set)  # id() of label runs
    blocks: list[BlockRead] = field(default_factory=list)

    @property
    def paragraphs(self) -> list[str]:
        return [text for text, _run in self.paragraphs_at]

    @property
    def is_drawing(self) -> bool:
        return len(self.columns) >= 2 and len(self.rows) >= 2

    def zone_of(self, x: float, y: float) -> str | None:
        number = next((n for right, n in self.columns if x < right), None)
        letter = next((n for bottom, n in self.rows if y < bottom), None)
        if number is None or letter is None or self.inner is None:
            return None
        if not (self.inner[0] <= x <= self.inner[2] and self.inner[1] <= y <= self.inner[3]):
            return None
        return f"{number}{letter}"


def squash(text: str) -> str:
    return "".join(text.split())


def _runs(chars: list[dict[str, Any]]) -> list[Run]:
    out: list[Run] = []
    ordered = sorted(chars, key=lambda c: (round(c["top"], 1), round(c["size"], 2), c["x0"]))
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current and "".join(c["text"] for c in current).strip():
            out.append(
                Run(
                    text="".join(c["text"] for c in current).strip(),
                    x0=min(c["x0"] for c in current if c["text"].strip()),
                    x1=max(c["x1"] for c in current if c["text"].strip()),
                    top=min(c["top"] for c in current),
                    bottom=max(c["bottom"] for c in current),
                    size=current[0]["size"],
                )
            )
        current.clear()

    for c in ordered:
        if current:
            last = current[-1]
            same = (
                abs(c["top"] - last["top"]) < 0.6
                and abs(c["size"] - last["size"]) < 0.01
                and -0.5 <= c["x0"] - last["x1"] < 0.6 * c["size"]
            )
            if not same:
                flush()
        current.append(c)
    flush()
    return out


def _paragraphs(runs: list[Run]) -> list[tuple[str, Run]]:
    """Runs sharing a left edge, one under the next: wrapped lines re-joined."""
    out: list[tuple[str, Run]] = []
    pending = sorted(runs, key=lambda r: (round(r.x0), r.top))
    used: set[int] = set()
    for n, run in enumerate(pending):
        if n in used:
            continue
        text, last = run.text, run
        for m in range(n + 1, len(pending)):
            other = pending[m]
            if m in used or abs(other.x0 - run.x0) > 0.6:
                continue
            if 0 <= other.top - last.bottom < 1.2 * last.size:
                text += " " + other.text
                used.add(m)
                last = other
            elif other.top > last.bottom + 1.2 * last.size:
                break
        out.append((text, run))
    return out


def _closed(pts: Sequence[P]) -> bool:
    return len(pts) >= 3 and math.dist(pts[0], pts[-1]) < 0.1


def _box_of(points: Iterable[P]) -> Box:
    xs, ys = zip(*points, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


def _contains(outer: Box, inner: Box, tol: float = 0.5) -> bool:
    return (
        outer[0] - tol <= inner[0]
        and outer[1] - tol <= inner[1]
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


def _area(b: Box) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def _dash(obj: dict[str, Any]) -> str:
    return repr(obj.get("dash"))


def _read_page(number: int, page: Any) -> Page:
    chars = [c for c in page.chars if c["text"].strip()]
    runs = _runs(page.chars)
    segs: list[Seg] = []
    circles: list[Circle] = []
    dots: list[P] = []
    rects: list[Box] = []
    obj = 0
    for line in page.lines:
        obj += 1
        pts = [(float(x), float(y)) for x, y in line["pts"]]
        for a, b in itertools.pairwise(pts):
            segs.append(Seg(a, b, round(line["linewidth"], 2), _dash(line), obj))
    for rect in page.rects:
        obj += 1
        box = (rect["x0"], rect["top"], rect["x1"], rect["bottom"])
        if not rect.get("stroke"):
            continue
        rects.append(box)
        x0, t, x1, b = box
        corners = [(x0, t), (x1, t), (x1, b), (x0, b), (x0, t)]
        for p, q in itertools.pairwise(corners):
            segs.append(Seg(p, q, round(rect["linewidth"], 2), _dash(rect), obj))
    for curve in page.curves:
        obj += 1
        pts = [(float(x), float(y)) for x, y in curve["pts"]]
        box = (curve["x0"], curve["top"], curve["x1"], curve["bottom"])
        w, h = box[2] - box[0], box[3] - box[1]
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        round_ = (
            _closed(pts)
            and abs(w - h) < 0.5
            and all(abs(math.dist(p, centre) - w / 2) < 0.1 * w for p in pts)
        )
        if round_ and curve.get("fill") and w < 3 * mm:
            dots.append(centre)
            continue
        if round_:
            circles.append(Circle(centre, w / 2, obj))
            continue
        if not curve.get("stroke"):
            continue
        for a, b in itertools.pairwise(pts):
            if math.dist(a, b) > 1e-6:
                segs.append(Seg(a, b, round(curve["linewidth"], 2), _dash(curve), obj))
    outer = max(rects, key=_area) if rects else None
    inner = None
    titles: list[Box] = []
    if outer is not None:
        inside = [r for r in rects if r != outer and _contains(outer, r)]
        inner = max(inside, key=_area) if inside else None
        if inner is not None and _area(inner) < 0.5 * _area(outer):
            inner = None
        titles = [
            r
            for r in rects
            if r not in (outer, inner)
            and _area(r) > 1000
            and (inner is None or not _overlap(r, inner))
        ]
    text = page.extract_text() or ""
    out = Page(
        number=number,
        chars=chars,
        runs=runs,
        paragraphs_at=_paragraphs(runs),
        text=text,
        segs=segs,
        circles=circles,
        dots=dots,
        outer=outer,
        inner=inner,
        titles=titles,
    )
    _read_grid(out)
    if out.is_drawing:
        _read_blocks(out)
    return out


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _bounds(
    labels: list[tuple[float, str]], ticks: list[float], low: float, high: float
) -> list[tuple[float, str]]:
    """Each label's zone as (upper bound, label), from the ticks or the label midpoints."""
    labels = sorted(set(labels))
    edges = sorted({round(t, 1) for t in ticks if low + 1 < t < high - 1})
    if len(edges) == len(labels) - 1:
        uppers = [*edges, high]
    else:
        uppers = [(a[0] + b[0]) / 2 for a, b in itertools.pairwise(labels)] + [high]
    return [(upper, name) for upper, (_c, name) in zip(uppers, labels, strict=True)]


def _read_grid(page: Page) -> None:
    if page.outer is None or page.inner is None:
        return
    ox0, ot, _ox1, _ob = page.outer
    ix0, it, ix1, ib = page.inner
    numbers: dict[str, float] = {}
    letters: dict[str, float] = {}
    for run in page.runs:
        if len(run.text) != 1:
            continue
        in_band = _contains(page.outer, (run.x0, run.top, run.x1, run.bottom)) and not _overlap(
            page.inner, (run.x0, run.top, run.x1, run.bottom)
        )
        if not in_band:
            continue
        if run.text in "12345678" and (run.bottom <= it or run.top >= ib):
            numbers[run.text] = run.xc
            page.zone_labels.add(id(run))
        elif run.text in "ABCDEF" and (run.x1 <= ix0 or run.x0 >= ix1):
            letters[run.text] = run.yc
            page.zone_labels.add(id(run))
    if len(numbers) < 2 or len(letters) < 2:
        return
    xticks = [
        s.a[0]
        for s in page.segs
        if abs(s.a[0] - s.b[0]) < EPS
        and max(s.a[1], s.b[1]) <= it + EPS
        and min(s.a[1], s.b[1]) >= ot - EPS
    ]
    yticks = [
        s.a[1]
        for s in page.segs
        if abs(s.a[1] - s.b[1]) < EPS
        and max(s.a[0], s.b[0]) <= ix0 + EPS
        and min(s.a[0], s.b[0]) >= ox0 - EPS
    ]
    page.columns = _bounds([(x, n) for n, x in numbers.items()], xticks, ix0, ix1)
    page.rows = _bounds([(y, n) for n, y in letters.items()], yticks, it, ib)


_HEADER = re.compile(r"\((component|strip)\)")


def _read_blocks(page: Page) -> None:
    assert page.inner is not None
    headers = [r for r in page.runs if _HEADER.search(r.text)]
    groups: list[float] = []
    for h in sorted(headers, key=lambda r: r.x0):
        if not groups or h.x0 - groups[-1] > 3 * mm:
            groups.append(h.x0)
    for h in headers:
        g = max(n for n, x in enumerate(groups) if x <= h.x0 + 3 * mm)
        right = groups[g + 1] - 3 * mm if g + 1 < len(groups) else page.inner[2]
        below = [o.top for o in headers if abs(o.x0 - h.x0) < 3 * mm and o.top > h.top + EPS]
        page.blocks.append(BlockRead(h, min(below, default=page.inner[3]), right))
    for dot in page.dots:
        owners = [
            b
            for b in page.blocks
            if b.header.x0 - 3 * mm <= dot[0] < b.right and b.header.top < dot[1] < b.bottom
        ]
        if owners:
            max(owners, key=lambda b: b.header.x0).dots.append(dot)
    leads = _leads(page)
    lead_objs = {s.obj for lead in leads for s in lead.segs}
    for block in page.blocks:
        block.leads = [lead for lead in leads if lead.dot in block.dots]
        if not block.dots:
            continue
        x_max = min(d[0] for d in block.dots) - 1.0
        region = (block.header.x0 - 3 * mm, block.header.bottom, x_max, block.bottom)
        for s in page.segs:
            if s.obj in lead_objs:
                continue
            box = _box_of((s.a, s.b))
            if _contains(region, box, tol=0) and _contains(page.inner, box, tol=-EPS):
                block.strokes.append(s)
        for c in page.circles:
            box = (c.c[0] - c.r, c.c[1] - c.r, c.c[0] + c.r, c.c[1] + c.r)
            if _contains(region, box, tol=0):
                block.strokes.append(c)


def _leads(page: Page) -> list[Lead]:
    """Every lead: the stroked path leaving a terminal point to its left, followed."""
    ends: dict[tuple[int, int], list[tuple[Seg, bool]]] = {}

    def key(p: P) -> tuple[int, int]:
        return round(p[0] / EPS), round(p[1] / EPS)

    def near(p: P) -> list[tuple[Seg, bool]]:
        k = key(p)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                found += ends.get((k[0] + dx, k[1] + dy), [])
        return [(s, flip) for s, flip in found if math.dist(s.b if flip else s.a, p) < EPS]

    for s in page.segs:
        ends.setdefault(key(s.a), []).append((s, False))
        ends.setdefault(key(s.b), []).append((s, True))
    out: list[Lead] = []
    for dot in page.dots:
        starts = [
            (s, flip)
            for s, flip in near(dot)
            if (s.a if flip else s.b)[0] < dot[0] - EPS  # it heads left, to the symbol
        ]
        for s, flip in starts:
            seg = Seg(s.b, s.a, s.width, s.dash, s.obj) if flip else s
            chain, used = [seg], {id(s)}
            while True:
                here = chain[-1].b
                nxt = [
                    (t, f)
                    for t, f in near(here)
                    if id(t) not in used and t.width == seg.width and t.dash == seg.dash
                ]
                same = [(t, f) for t, f in nxt if t.obj == chain[-1].obj]
                pick = same if len(same) == 1 else (nxt if len(nxt) == 1 else [])
                if not pick:
                    break
                t, f = pick[0]
                used.add(id(t))
                chain.append(Seg(t.b, t.a, t.width, t.dash, t.obj) if f else t)
            out.append(Lead(dot, chain))
    return out


@functools.cache
def sheets_of(case: str) -> tuple[Page, ...]:
    return read_pdf(render(case))


def read_pdf(content: bytes) -> tuple[Page, ...]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return tuple(_read_page(n, page) for n, page in enumerate(pdf.pages, start=1))


# --------------------------------------------------------------------------- #
# Plane geometry
# --------------------------------------------------------------------------- #


def _cross(o: P, a: P, b: P) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def meet(p1: P, p2: P, q1: P, q2: P, tol: float = 0.05) -> list[P]:
    """The points two segments share: none, one, or the two ends of a collinear overlap."""
    lp, lq = math.dist(p1, p2), math.dist(q1, q2)
    if lp < 1e-9 or lq < 1e-9:
        return []
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-9 * lp * lq:
        if abs(_cross(p1, p2, q1)) / lp > tol:
            return []
        ux, uy = (p2[0] - p1[0]) / lp, (p2[1] - p1[1]) / lp
        tq = sorted(((q[0] - p1[0]) * ux + (q[1] - p1[1]) * uy) for q in (q1, q2))
        lo, hi = max(0.0, tq[0]), min(lp, tq[1])
        if lo > hi + tol:
            return []
        return [(p1[0] + ux * lo, p1[1] + uy * lo), (p1[0] + ux * hi, p1[1] + uy * hi)]
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    et, eu = tol / lp, tol / lq
    if -et <= t <= 1 + et and -eu <= u <= 1 + eu:
        return [(p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))]
    return []


def meet_circle(a: P, b: P, c: Circle, tol: float = 0.05) -> list[P]:
    """Where a segment crosses or touches a circle's outline."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - c.c[0], a[1] - c.c[1]
    qa = dx * dx + dy * dy
    if qa < 1e-12:
        return []
    out: list[P] = []
    for r in (c.r - tol, c.r + tol):
        qb, qc = 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
        disc = qb * qb - 4 * qa * qc
        if disc < 0:
            continue
        for sign in (-1, 1):
            t = (-qb + sign * math.sqrt(disc)) / (2 * qa)
            if 0 <= t <= 1:
                out.append((a[0] + t * dx, a[1] + t * dy))
    return out


def through_box(a: P, b: P, box: Box) -> float:
    """How long a segment runs inside a box's open interior (Liang-Barsky)."""
    x0, y0, x1, y1 = box
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return 0.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return 0.0
            continue
        r = q / p
        if p < 0:
            lo = max(lo, r)
        else:
            hi = min(hi, r)
        if lo >= hi:
            return 0.0
    return (hi - lo) * math.hypot(dx, dy)


def _stroke_box(s: Seg | Circle) -> Box:
    if isinstance(s, Circle):
        return s.c[0] - s.r, s.c[1] - s.r, s.c[0] + s.r, s.c[1] + s.r
    return _box_of((s.a, s.b))


def _fmt(p: P) -> str:
    return f"({p[0]:.1f},{p[1]:.1f})"


def _zone_name(page: Page, p: P) -> str:
    return page.zone_of(*p) or "?"


# --------------------------------------------------------------------------- #
# 1. Leads: no crossing, no overlap, never through a symbol
# --------------------------------------------------------------------------- #


def lead_problems(pages: Sequence[Page]) -> list[str]:
    problems: list[str] = []
    for page in pages:
        if not page.is_drawing:
            continue
        for dot in page.dots:
            if not any(dot in b.dots for b in page.blocks):
                problems.append(f"sheet {page.number}: point {_fmt(dot)} is in no block")
        leads = [lead for b in page.blocks for lead in b.leads]
        for block in page.blocks:
            name = block.header.text
            got = {lead.dot for lead in block.leads}
            for dot in block.dots:
                if dot not in got:
                    problems.append(f"sheet {page.number} {name}: point {_fmt(dot)} has no lead")
            extent: Box | None = None
            if block.strokes:
                boxes = [_stroke_box(s) for s in block.strokes]
                extent = (
                    min(b[0] for b in boxes) + EPS,
                    min(b[1] for b in boxes) + EPS,
                    max(b[2] for b in boxes) - EPS,
                    max(b[3] for b in boxes) - EPS,
                )
            for lead in block.leads:
                where = (
                    f"sheet {page.number} zone {_zone_name(page, lead.dot)} {name}: lead to "
                    f"{_fmt(lead.dot)} from anchor {_fmt(lead.anchor)}"
                )
                for seg in lead.segs:
                    if extent is not None and through_box(seg.a, seg.b, extent) > EPS:
                        problems.append(
                            f"{where}: segment {_fmt(seg.a)}-{_fmt(seg.b)} passes through the "
                            "symbol's drawn extent"
                        )
                    for stroke in block.strokes:
                        hits = (
                            meet_circle(seg.a, seg.b, stroke)
                            if isinstance(stroke, Circle)
                            else meet(seg.a, seg.b, stroke.a, stroke.b)
                        )
                        stray = [h for h in hits if math.dist(h, lead.anchor) > EPS]
                        if stray:
                            what = (
                                f"circle at {_fmt(stroke.c)}"
                                if isinstance(stroke, Circle)
                                else f"stroke {_fmt(stroke.a)}-{_fmt(stroke.b)}"
                            )
                            problems.append(
                                f"{where}: segment {_fmt(seg.a)}-{_fmt(seg.b)} meets symbol "
                                f"{what} at {_fmt(stray[0])}"
                            )
        for n, one in enumerate(leads):
            for other in leads[n + 1 :]:
                for s in one.segs:
                    for t in other.segs:
                        hits = meet(s.a, s.b, t.a, t.b)
                        if hits:
                            kind = "overlaps" if len(hits) == 2 else "crosses"
                            problems.append(
                                f"sheet {page.number} zone {_zone_name(page, hits[0])}: lead to "
                                f"{_fmt(one.dot)} {kind} lead to {_fmt(other.dot)} at "
                                f"{_fmt(hits[0])}"
                            )
    return problems


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_lead_crosses_another_or_passes_through_a_symbol(case: str) -> None:
    problems = lead_problems(sheets_of(case))
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_the_lead_reader_finds_the_leads_it_must() -> None:
    """The heuristic itself: every anchored terminal of the relay case has a lead found."""
    model, _ = build("config:drawing_control_relay")
    anchored = sum(
        len(t.symbol.anchors)
        for c in model.components
        if (t := model.template(c.template_ref)) is not None and t.symbol is not None
    )
    pages = [p for p in sheets_of("config:drawing_control_relay") if p.is_drawing]
    solid = [
        lead
        for p in pages
        for b in p.blocks
        for lead in b.leads
        if lead.segs[0].dash == repr(None) and b.strokes
    ]
    assert len(solid) >= anchored
    assert all(b.strokes for p in pages for b in p.blocks if b.dots)


# --------------------------------------------------------------------------- #
# 2. Readable text
# --------------------------------------------------------------------------- #


def is_secondary(char: dict[str, Any]) -> bool:
    """Secondary detail is printed in a neutral grey (not black, not a colour)."""
    colour = char.get("non_stroking_color")
    if colour is None:
        return False
    values = tuple(float(v) for v in (colour if isinstance(colour, Iterable) else (colour,)))
    if len(values) == 1:
        return 0.05 < values[0] < 0.95
    if len(values) == 3:
        return max(values) - min(values) < 0.02 and 0.05 < values[0] < 0.95
    return False


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_text_is_smaller_than_the_readability_minimum(case: str) -> None:
    small: dict[tuple[str, float], list[str]] = {}
    for page in sheets_of(case):
        for c in page.chars:
            minimum = SECONDARY_MIN_PT if is_secondary(c) else PRIMARY_MIN_PT
            if c["size"] < minimum - 1e-6:
                kind = "secondary" if is_secondary(c) else "primary"
                small.setdefault((kind, round(c["size"], 2)), []).append(
                    f"sheet {page.number} at ({c['x0']:.0f},{c['top']:.0f}) {c['text']!r}"
                )
    assert not small, "text below the minimum:\n" + "\n".join(
        f"{kind} {size} pt: {len(v)} chars, e.g. {v[:3]}"
        for (kind, size), v in sorted(small.items())
    )


# --------------------------------------------------------------------------- #
# 3. No silent truncation
# --------------------------------------------------------------------------- #


def model_values(model: entities.DesignModelV2) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {
        "designation": set(),
        "signal": set(),
        "wire number": set(),
        "terminal marking": set(),
        "point marking": set(),
    }
    for c in model.components:
        if c.designation is not None:
            values["designation"].add(c.designation.product)
        template = model.template(c.template_ref)
        if template is not None:
            values["terminal marking"] |= {t.marking for t in template.terminals}
    for s in model.strips:
        if s.designation is not None:
            values["designation"].add(s.designation.product)
        values["point marking"] |= {p.marking for p in s.points}
    values["signal"] = {n.signal for n in model.nets}
    values["wire number"] = {str(w.number.value) for w in model.wires if w.number.value is not None}
    return values


def without_markers(text: str) -> str:
    """The text with every reference marker right after an ellipsis removed."""
    return TRUNCATION_MARKER.sub("…", text)


def has_note(text: str, marker: str) -> bool:
    """Whether a marker also appears as a note: not right after an ellipsis, followed by text."""
    for found in re.finditer(re.escape(marker), text):
        before = text[: found.start()].rstrip()
        after = text[found.end() :].lstrip()
        if not before.endswith("…") and after[:1] not in ("", "\n") and after[:1].strip():
            return True
    return False


@functools.cache
def _haystacks(case: str) -> tuple[str, ...]:
    pages = sheets_of(case)
    return tuple(squash(t) for p in pages for t in (p.text, *p.paragraphs))


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_ellipsis_carries_a_marker_and_every_value_is_printed_in_full(case: str) -> None:
    model, _ = build(case)
    problems: list[str] = []
    everything = "\n".join(p.text for p in sheets_of(case))
    for page in sheets_of(case):
        for text in page.paragraphs:
            for n, _ in enumerate(text):
                if text[n] != "…":
                    continue
                found = TRUNCATION_MARKER.match(text, n)
                if found is None:
                    problems.append(f"sheet {page.number}: cut with no marker: {text[:120]!r}")
                elif not has_note(everything, found.group(1)):
                    problems.append(
                        f"sheet {page.number}: marker {found.group(1)!r} has no full-text note"
                    )
    hay = _haystacks(case)
    for kind, values in model_values(model).items():
        for value in sorted(values):
            if not any(squash(value) in h for h in hay):
                problems.insert(0, f"{kind} never printed in full: {value!r}")
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_markers_are_stripped_and_notes_are_required() -> None:
    assert without_markers("id abc…[F1] rest") == "id abc… rest"
    assert without_markers(squash("id abc… [F1] rest")) == "idabc…rest"
    assert has_note("value abc…[F1]\n[F1] abcdef full", "[F1]")
    assert not has_note("value abc…[F1] and abc…[F1]", "[F1]")


def test_the_long_cases_exercise_long_values() -> None:
    for case in LONG_CASES:
        values = model_values(build(case)[0])
        assert max(len(v) for v in values["signal"]) >= 60
        assert max(len(v) for v in values["designation"]) >= 60
        assert max(len(v) for v in values["wire number"]) >= 60
        assert max(len(v) for v in values["point marking"]) >= 60


# --------------------------------------------------------------------------- #
# 4. Text inside the inner frame
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_all_text_is_inside_the_inner_frame_with_clearance(case: str) -> None:
    problems: list[str] = []
    for page in sheets_of(case):
        assert page.outer is not None, page.number
        assert page.inner is not None, page.number
        labels = [r for r in page.runs if id(r) in page.zone_labels]
        ix0, it, ix1, ib = page.inner
        c = FRAME_CLEARANCE
        for ch in page.chars:
            box = (ch["x0"], ch["top"], ch["x1"], ch["bottom"])
            if any(_contains(t, box) for t in page.titles):
                continue
            if not _overlap(page.outer, box):
                continue  # the page margin's export marks
            if any(_contains((r.x0, r.top, r.x1, r.bottom), box) for r in labels):
                continue
            if not (
                box[0] >= ix0 + c and box[2] <= ix1 - c and box[1] >= it + c and box[3] <= ib - c
            ):
                problems.append(
                    f"sheet {page.number}: {ch['text']!r} at ({box[0]:.1f},{box[1]:.1f})-"
                    f"({box[2]:.1f},{box[3]:.1f}) outside the inner frame "
                    f"({ix0:.1f},{it:.1f})-({ix1:.1f},{ib:.1f}) less {c:.1f} pt"
                )
    assert not problems, f"{len(problems)} chars:\n" + "\n".join(problems[:30])


# --------------------------------------------------------------------------- #
# 5. No open item or finding split across sheets
# --------------------------------------------------------------------------- #


def _body_lines(page: Page) -> list[str]:
    """The text page's lines inside the inner frame, top down, title first."""
    assert page.inner is not None
    inside = [r for r in page.runs if _contains(page.inner, (r.x0, r.top, r.x1, r.bottom), tol=1)]
    lines: list[list[Run]] = []
    for run in sorted(inside, key=lambda r: (r.top, r.x0)):
        if lines and abs(lines[-1][0].top - run.top) < 1.0:
            lines[-1].append(run)
        else:
            lines.append([run])
    return [" ".join(r.text for r in sorted(line, key=lambda r: r.x0)) for line in lines]


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_open_item_or_finding_continues_on_the_next_sheet(case: str) -> None:
    model, _ = build(case)
    pages = [p for p in sheets_of(case)[1:] if not p.is_drawing]
    kinds = {kind for kind, _text in expected_open_items(model)}
    starts = [f"{k}:" for k in kinds] + ["No open items."]
    finding_starts = [
        "[",
        "Structural findings",
        "The design's other findings",
        "No structural findings",
        *(line[:16] for line in marking.finding_lines(_MARK)),
    ]
    problems: list[str] = []
    for page in pages:
        lines = _body_lines(page)
        if len(lines) < 2:
            continue
        title, first = lines[0], lines[1]
        if title.startswith("Open items"):
            allowed = starts
        elif title.startswith("Findings"):
            allowed = finding_starts
        else:
            continue
        if not any(squash(first).startswith(squash(s)) for s in allowed):
            problems.append(f"sheet {page.number} ({title}) starts mid-entry: {first[:140]!r}")
    # Every structural finding, whole, on one sheet.
    # A finding message may itself shorten a value with "…"; the page prints
    # that "…" with its reference marker, which is dropped before comparing.
    texts = [without_markers(squash(p.text)) for p in pages]
    for f in findings_v2.structural_findings(model):
        whole = squash(f"[{f.severity.upper()}] {f.code}: {f.message}")
        if not any(whole in t for t in texts):
            problems.append(f"finding {f.code} is not whole on any one sheet")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------- #
# 6. Every zone named is where the thing is printed
# --------------------------------------------------------------------------- #

_REF = re.compile(
    r"to (?P<label>.+?) \((?:sheet (?P<sheet>\d+)|this sheet), zone (?P<zone>\d+[A-Z])\)"
)
_CONT = re.compile(
    r"(?P<how>continues on|continued from) (?:sheet (?P<sheet>\d+)|this sheet), "
    r"zone (?P<zone>\d+[A-Z])"
)


def _owner_names(model: entities.DesignModelV2) -> list[str]:
    owners: list[Any] = [*model.components, *model.strips]
    names = [o.designation.product if o.designation else o.id for o in owners]
    return sorted(names, key=len, reverse=True)


def _blocks_named(page: Page, name: str) -> list[BlockRead]:
    return [b for b in page.blocks if squash(b.header.text).startswith(squash(name) + "(")]


def _marking_at(page: Page, name: str, marking_text: str) -> tuple[Run, P] | None:
    """The marking run nearest its point on the same row, in a block of that owner, and the point."""
    want = squash(marking_text)
    found: list[tuple[float, Run, P]] = []
    for block in _blocks_named(page, name):
        for dot in block.dots:
            for run in page.runs:
                if (
                    squash(run.text) == want
                    and run.x0 >= block.header.x0 - 3 * mm
                    and run.x1 <= block.right
                    and block.header.bottom < run.yc < block.bottom
                    and abs(run.yc - dot[1]) < MARKING_ROW
                ):
                    gap = max(dot[0] - run.x1, run.x0 - dot[0], 0.0)
                    found.append((gap + abs(run.yc - dot[1]), run, dot))
    if not found:
        return None
    _gap, run, dot = min(found, key=lambda f: f[0])
    return run, dot


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_zone_reference_points_where_the_far_end_is_printed(case: str) -> None:
    model, _ = build(case)
    pages = sheets_of(case)
    names = _owner_names(model)
    problems: list[str] = []
    found = 0
    for page in pages:
        if not page.is_drawing:
            continue
        for text in page.paragraphs:
            for ref in _REF.finditer(text):
                found += 1
                label, zone = ref["label"], ref["zone"]
                sheet = int(ref["sheet"]) if ref["sheet"] else page.number
                name = next((n for n in names if label.startswith(n + ":")), None)
                if name is None or not 1 <= sheet <= len(pages):
                    problems.append(f"sheet {page.number}: {ref[0]!r} names nothing drawn")
                    continue
                target = pages[sheet - 1]
                at = _marking_at(target, name, label[len(name) + 1 :])
                if at is None:
                    problems.append(
                        f"sheet {page.number}: {ref[0]!r}: no such marking printed on sheet {sheet}"
                    )
                    continue
                run, dot = at
                got = target.zone_of(*dot)
                if got != zone:
                    problems.append(
                        f"sheet {page.number}: {ref[0]!r}: its point is drawn in zone {got} "
                        f"at {_fmt(dot)} of sheet {sheet}"
                    )
                gap = max(dot[0] - run.x1, run.x0 - dot[0], 0.0)
                if gap > MARKING_REACH or abs(run.yc - dot[1]) > MARKING_ROW:
                    problems.append(
                        f"sheet {page.number}: {ref[0]!r}: marking at x {run.x0:.0f}-"
                        f"{run.x1:.0f}, y {run.yc:.0f} is {gap:.1f} pt from its point {_fmt(dot)}"
                    )
        for text, first in page.paragraphs_at:
            for cont in _CONT.finditer(text):
                sheet = int(cont["sheet"]) if cont["sheet"] else page.number
                above = [
                    b
                    for b in page.blocks
                    if abs(b.header.x0 - first.x0) < 3 * mm or b.header.x0 <= first.x0 < b.right
                ]
                above = [b for b in above if b.header.top <= first.top + 1.0]
                if not above or not 1 <= sheet <= len(pages):
                    problems.append(f"sheet {page.number}: {cont[0]!r} belongs to no block")
                    continue
                block = max(above, key=lambda b: (b.header.top, b.header.x0))
                title = _HEADER.split(block.header.text)[0]
                target = pages[sheet - 1]
                heads = [b for b in target.blocks if b is not block or sheet == page.number]
                heads = [
                    b
                    for b in heads
                    if squash(_HEADER.split(b.header.text)[0]) == squash(title) and b is not block
                ]
                zones = {target.zone_of(b.header.x0 + 1, b.header.yc) for b in heads}
                if cont["zone"] not in zones:
                    problems.append(
                        f"sheet {page.number}: {title.strip()} {cont[0]!r}: its other part's "
                        f"header on sheet {sheet} is in zone(s) {sorted(z or '?' for z in zones)}"
                    )
    if found != 2 * len(model.connections):
        problems.append(
            f"{found} cross-references read, the model has {len(model.connections)} connections "
            f"({2 * len(model.connections)} ends): some are cut or unreadable"
        )
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


# --------------------------------------------------------------------------- #
# 7. Every sheet marked, hashed and numbered
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_sheet_is_marked_hashed_and_numbered(case: str) -> None:
    _model, digest = build(case)
    pages = sheets_of(case)
    for page in pages:
        text = squash(page.text)
        assert squash("NOT FOR EXECUTION") in text, page.number
        assert digest in text, page.number
        assert re.findall(r"Sheet(\d+)of(\d+)", text) == [(str(page.number), str(len(pages)))]


# --------------------------------------------------------------------------- #
# The readers themselves, on a hand-drawn page
# --------------------------------------------------------------------------- #


def _hand_drawn(leads: Sequence[Sequence[P]]) -> bytes:
    """A frame, a zone grid, one block and the given leads (bottom-up points, dot last)."""
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(842, 595))
    canvas.setLineWidth(0.6)
    canvas.rect(30, 30, 782, 535)
    canvas.setLineWidth(0.3)
    canvas.rect(42, 42, 758, 511)
    canvas.setFont("Helvetica", 6)
    for n in range(8):
        canvas.drawString(42 + (n + 0.5) * 758 / 8, 556, str(n + 1))
        if n:
            canvas.line(42 + n * 758 / 8, 553, 42 + n * 758 / 8, 565)
    for n, letter in enumerate("ABCDEF"):
        canvas.drawString(33, 553 - (n + 0.5) * 511 / 6, letter)
        if n:
            canvas.line(30, 553 - n * 511 / 6, 42, 553 - n * 511 / 6)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawString(60, 530, "S1 (component)")
    canvas.setLineWidth(0.5)
    canvas.rect(70, 440, 40, 60)
    canvas.setLineWidth(0.3)
    for points in leads:
        path = canvas.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        canvas.drawPath(path, stroke=1, fill=0)
        canvas.circle(*points[-1], 2, stroke=1, fill=1)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_the_lead_checker_finds_crossings_and_leads_through_a_symbol() -> None:
    clean = [[(110, 470), (200, 470)], [(110, 490), (130, 490), (130, 480), (200, 480)]]
    pages = read_pdf(_hand_drawn(clean))
    assert pages[0].is_drawing
    assert pages[0].zone_of(150, 100) == "2A"  # x 150 in column 2; 100 pt from the top: row A
    assert len(pages[0].blocks) == 1
    assert len(pages[0].blocks[0].leads) == 2
    assert lead_problems(pages) == []
    through = [[(70, 460), (200, 460)]]
    problems = lead_problems(read_pdf(_hand_drawn(clean + through)))
    assert any("passes through the symbol" in p for p in problems), problems
    assert any("meets symbol" in p for p in problems), problems
    crossing = [[(110, 445), (140, 445), (140, 475), (200, 475)]]
    problems = lead_problems(read_pdf(_hand_drawn(clean + crossing)))
    assert any("crosses lead" in p for p in problems), problems
    overlapping = [[(110, 490), (130, 490), (130, 450), (200, 450)]]
    problems = lead_problems(read_pdf(_hand_drawn(clean + overlapping)))
    assert any("overlaps lead" in p for p in problems), problems
