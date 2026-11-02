"""M4 consistency: the connection drawing, the schedules and the saved model agree.

Independent of the drawing's own tests: every expectation here is computed
from the ``pp-design/2`` model itself (its components, templates, strips,
nets, connections and wires), not from the layout's text helpers, and the PDF
is read back by position (``pdfplumber`` characters, placed by the drawing
area's slot and column geometry) so that what is *printed* is checked, not only
what the layout holds.

Cases, all synthetic:

* ``config:<name>``: every committed configuration (``model/configurations``);
* ``fixture:<name>``: every designed M1 fixture migrated to v2 (no configuration);
* ``open:<name>``: a configuration with open items forced into it (a wire
  record dropped, a net split, a net dropped, designations removed, wire
  attributes unset), so every kind of open item is exercised;
* ``scale``: a model generated here with many components and a strip long
  enough to continue across columns and sheets.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from reportlab.lib.units import mm

from app.design import connection_drawing_pdf as pdf_module
from app.design import marking, snapshot
from app.design.model import connection_drawing, entities, findings_v2, schedules, write
from app.design.model.read import read_snapshot
from app.domain import design_configuration, design_projects
from app.tests.design.model.compat_support import DESIGNED
from app.tests.design.model.v2_support import CONFIGS, config_case

API_ROOT = Path(__file__).resolve().parents[3]

# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

Key = tuple[str, str, str]


def _replace[T](obj: T, **changes: Any) -> T:
    return dataclasses.replace(obj, **changes)  # type: ignore[type-var]


def _with_open_items(model: entities.DesignModelV2) -> entities.DesignModelV2:
    """The model with every kind of open item forced in, from its own data."""
    connections = list(model.connections)
    wires = list(model.wires)
    nets = list(model.nets)
    # A via-wire connection whose wire record is dropped.
    wired = {w.connection_id for w in wires}
    dropped_wire = next((c for c in connections if c.via == "wire" and c.id in wired), None)
    if dropped_wire is not None:
        wires = [w for w in wires if w.connection_id != dropped_wire.id]
    # A net whose connections are all dropped: its members are no longer joined.
    split = next(
        (
            n
            for n in nets
            if len(n.members) >= 2
            and any(c.net_id == n.id for c in connections)
            and (dropped_wire is None or n.id != dropped_wire.net_id)
        ),
        None,
    )
    if split is not None:
        gone = {c.id for c in connections if c.net_id == split.id}
        connections = [c for c in connections if c.id not in gone]
        wires = [w for w in wires if w.connection_id not in gone]
    # A net dropped with its connections: its members are unconnected.
    dropped_net = next(
        (
            n
            for n in nets
            if (split is None or n.id != split.id)
            and (dropped_wire is None or n.id != dropped_wire.net_id)
        ),
        None,
    )
    if dropped_net is not None:
        gone = {c.id for c in connections if c.net_id == dropped_net.id}
        connections = [c for c in connections if c.id not in gone]
        wires = [w for w in wires if w.connection_id not in gone]
        nets = [n for n in nets if n.id != dropped_net.id]
    # Every attribute of the first wire left unset.
    if wires:
        unset = entities.WireAttribute(value=None, source="unset", assumption=None)
        wires[0] = _replace(wires[0], number=unset, cross_section_mm2=unset, colour=unset)
    components = list(model.components)
    if components:
        components[0] = _replace(components[0], designation=None)
    strips = list(model.strips)
    if strips:
        strips[-1] = _replace(strips[-1], designation=None)
    return _replace(
        model,
        components=tuple(components),
        strips=tuple(strips),
        nets=tuple(nets),
        connections=tuple(connections),
        wires=tuple(wires),
    )


#: How many copies of the base configuration the scale case holds.
SCALE_COPIES = 24
#: Points on the scale case's long strip: two rows each, many columns' worth.
SCALE_POINTS = 90
SCALE_BASE = "io_marshalling"


def _scaled(model: entities.DesignModelV2, copies: int) -> entities.DesignModelV2:
    """``copies`` renamed copies of every component, strip, net, connection and wire.

    Plus one long strip with no template whose points are in no net, so its
    block must continue over several columns and sheets.
    """

    def ref(r: entities.TerminalRef, s: str) -> entities.TerminalRef:
        if r.component is not None:
            return _replace(r, component=r.component + s)
        return _replace(r, point=(r.point or "") + s)

    def designation(d: Any, s: str) -> Any:
        return None if d is None else _replace(d, product=d.product + s)

    components, strips, nets, connections, wires = [], [], [], [], []
    for n in range(copies):
        s = f"-c{n:02d}"
        components += [
            _replace(c, id=c.id + s, designation=designation(c.designation, s))
            for c in model.components
        ]
        strips += [
            _replace(
                t,
                id=t.id + s,
                designation=designation(t.designation, s),
                points=tuple(_replace(p, id=p.id + s) for p in t.points),
            )
            for t in model.strips
        ]
        nets += [
            _replace(
                x, id=x.id + s, signal=x.signal + s, members=tuple(ref(m, s) for m in x.members)
            )
            for x in model.nets
        ]
        connections += [
            _replace(
                c,
                id=c.id + s,
                net_id=c.net_id + s,
                start=ref(c.start, s),
                end=ref(c.end, s),
                core=None if c.core is None else _replace(c.core, cable_id=c.core.cable_id + s),
            )
            for c in model.connections
        ]
        wires += [_replace(w, id=w.id + s, connection_id=w.connection_id + s) for w in model.wires]
    board = model.components[0].board_id if model.components else "B1"
    long_strip = entities.TerminalStrip(
        id="strip-scale-long",
        board_id=board,
        designation=None,
        template_ref=None,
        points=tuple(
            entities.StripPoint(
                id=f"pt-scale-{n:03d}", position=n + 1, level=1, marking=str(n + 1), kind="feed"
            )
            for n in range(SCALE_POINTS)
        ),
    )
    strips.append(long_strip)
    return _replace(
        model,
        components=tuple(sorted(components, key=lambda c: c.id)),
        strips=tuple(sorted(strips, key=lambda t: t.id)),
        nets=tuple(sorted(nets, key=lambda x: x.id)),
        connections=tuple(sorted(connections, key=lambda c: c.id)),
        wires=tuple(sorted(wires, key=lambda w: w.id)),
    )


def _config_stored(name: str) -> dict[str, Any]:
    v1, configuration = config_case(name)
    return write.snapshot_v2_from_v1(v1, configuration)


def _read(stored: dict[str, Any]) -> entities.DesignModelV2:
    model = read_snapshot(stored).v2
    assert model is not None
    return model


def _build(case: str) -> tuple[entities.DesignModelV2, str]:
    """The model and the snapshot hash printed for a case."""
    kind, _, name = case.partition(":")
    if kind == "config":
        stored = _config_stored(name)
        return _read(stored), snapshot.sha256_of(stored)
    if kind == "fixture":
        stored = write.snapshot_v2_from_v1(
            json.loads(json.dumps(DESIGNED[name]["stored"]["design_snapshot"]))
        )
        return _read(stored), snapshot.sha256_of(stored)
    if kind == "open":
        stored = _config_stored(name)
        return _with_open_items(_read(stored)), "0" * 63 + "1"
    if kind == "route":
        from app.tests.design import test_routed_topology  # its synthetic stress models

        return test_routed_topology.build(case)
    assert case == "scale"
    stored = _config_stored(SCALE_BASE)
    return _scaled(_read(stored), SCALE_COPIES), "f" * 64


CONFIG_CASES = [f"config:{name}" for name in sorted(CONFIGS)]
FIXTURE_CASES = [f"fixture:{name}" for name in sorted(DESIGNED)]
OPEN_CASES = [f"open:{name}" for name in sorted(CONFIGS)]
ALL_CASES = [*CONFIG_CASES, *FIXTURE_CASES, *OPEN_CASES, "scale"]
#: Regression: K1/K2 on three boards, each wired within its own board, all on one
#: cross-reference sheet. A bare "to K2:1" there could be any board's K2.
SAME_NAME_CASES = ["route:same_designation"]


def _header(case: str, digest: str) -> pdf_module.DrawingHeader:
    return pdf_module.DrawingHeader(
        project=f"Synthetic {case}",
        revision="01",
        design_sha256=digest,
        schema="pp-design/2",
        rules_version="rules-under-test",
        renderer_version="renderer-under-test",
    )


def render(case: str) -> bytes:
    """The drawing PDF of a case, made as the export makes it."""
    model, digest = _build(case)
    structural = [
        design_configuration.finding_view(f, design_projects._V2_CONFIRM)
        for f in findings_v2.structural_findings(model)
    ]
    mark = marking.ExportMark(label=marking.DRAFT_LABEL, identity="Synthetic, revision 01")
    return pdf_module.render_connection_drawing(
        connection_drawing.drawing_set(model), _header(case, digest), mark, structural
    )


# --------------------------------------------------------------------------- #
# The model, read independently of the layout
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Expected:
    """One terminal row as the model alone says it must be printed."""

    key: Key
    board_id: str
    owner_kind: str
    owner_id: str
    title: str
    marking: str  # as printed beside the point
    label: str  # as a far end names it
    net: entities.Net | None
    requiredness: bool | None


def _ref_key(r: entities.TerminalRef) -> Key:
    return r.key


def _owners(model: entities.DesignModelV2) -> list[Any]:
    return [*model.components, *model.strips]


def expected_rows(model: entities.DesignModelV2) -> dict[Key, Expected]:
    net_of = {m.key: n for n in model.nets for m in n.members}
    out: dict[Key, Expected] = {}
    for component in model.components:
        template = model.template(component.template_ref)
        assert template is not None
        name = component.designation.product if component.designation else component.id
        for terminal in template.terminals:
            key = entities.TerminalRef(component=component.id, terminal=terminal.id).key
            assert key not in out
            out[key] = Expected(
                key=key,
                board_id=component.board_id,
                owner_kind="component",
                owner_id=component.id,
                title=f"{name} (component)",
                marking=terminal.marking,
                label=f"{name}:{terminal.marking}",
                net=net_of.get(key),
                requiredness=entities.documented_requiredness(template, terminal),
            )
    for strip in model.strips:
        name = strip.designation.product if strip.designation else strip.id
        for point in strip.points:
            for side in ("inner", "outer"):
                key = entities.TerminalRef(point=point.id, side=side).key
                assert key not in out
                printed = f"{point.marking} {side} L{point.level}"
                out[key] = Expected(
                    key=key,
                    board_id=strip.board_id,
                    owner_kind="strip",
                    owner_id=strip.id,
                    title=f"{name} (strip)",
                    marking=printed,
                    label=f"{name}:{printed}",
                    net=net_of.get(key),
                    requiredness=None,
                )
    return out


def net_groups(model: entities.DesignModelV2) -> dict[str, list[set[Key]]]:
    """Each net's members, grouped by which of them its own connections join."""
    groups: dict[str, list[set[Key]]] = {}
    for net in model.nets:
        members = [m.key for m in net.members]
        adjacency: dict[Key, set[Key]] = {k: set() for k in members}
        for c in model.connections:
            for a, b in ((c.start.key, c.end.key), (c.end.key, c.start.key)):
                adjacency.setdefault(a, set()).add(b)
        seen: set[Key] = set()
        found: list[set[Key]] = []
        for member in members:
            if member in seen:
                continue
            stack, group = [member], set()
            while stack:
                k = stack.pop()
                if k in group:
                    continue
                group.add(k)
                stack.extend(adjacency.get(k, ()))
            seen |= group
            found.append(group & set(members))
        groups[net.id] = found
    return groups


def cell(model: entities.DesignModelV2, attribute: entities.WireAttribute) -> str:
    """A wire attribute as the model says it must read: value and source, or not set."""
    if attribute.value is None:
        return "not set"
    if attribute.source == "assumption":
        return f"{attribute.value} assumed ({attribute.assumption or 'not set'})"
    source = "unresolved" if attribute.source == "unset" else attribute.source
    return f"{attribute.value} [{source}]"


def second_line(model: entities.DesignModelV2, c: entities.Connection) -> str:
    wire = next((w for w in model.wires if w.connection_id == c.id), None)
    if wire is not None:
        return (
            f"wire {wire.id}: no. {cell(model, wire.number)}; "
            f"mm² {cell(model, wire.cross_section_mm2)}; colour {cell(model, wire.colour)}"
        )
    if c.via == "wire":
        return "WIRE RECORD MISSING (connection listed in the open items)"
    if c.start.point is not None and c.start.point == c.end.point:
        return bridge_line(c)
    if c.core is not None:
        label = c.core.label if c.core.label is not None else "not set"
        return f"cable {c.core.cable_id} core {c.core.index}, core label {label}"
    return f"no wire record (via {c.via})"


def bridge_line(c: entities.Connection) -> str:
    """The line beside a strip point's bridge mark: its via, and who declared it."""
    source = f"per connection pattern {c.pattern_ref}" if c.pattern_ref else "per model"
    return f"bridge between the sides: {c.via} {source}"


def far_labels(
    expected: dict[Key, Expected], here: Key, far: Key, sheet_boards: set[str]
) -> list[str]:
    """The labels a far end may be printed as, on a sheet showing ``sheet_boards``.

    The board qualifier is required when the far end is on another board, or when
    the sheet shows more than one board (a bare "K2:A2" would then be ambiguous);
    on a one-board sheet it is accepted but not required.
    """
    row = expected[far]
    qualified = f"{row.board_id}/{row.label}"
    if row.board_id != expected[here].board_id or len(sheet_boards) > 1:
        return [qualified]
    return [row.label, qualified]


def row_notes(
    expected: Expected, groups: dict[str, list[set[Key]]], has_connection: bool
) -> list[str]:
    if expected.net is None:
        if expected.owner_kind == "strip":
            return ["UNCONNECTED: this point side is in no net"]
        return [
            {
                True: "UNCONNECTED: required (documented)",
                None: "UNCONNECTED: requiredness unresolved",
                False: "unconnected: not required (documented)",
            }[expected.requiredness]
        ]
    net = expected.net
    found = groups[net.id]
    if len(found) > 1:
        index = next(i for i, g in enumerate(found) if expected.key in g) + 1
        return [
            f"UNREALISED: net {net.signal} ({net.id}) is not joined by its connections; "
            f"this terminal is in group {index} of {len(found)}"
        ]
    if not has_connection:
        return [f"in net {net.signal} ({net.id}) alone: no connection"]
    return []


# --------------------------------------------------------------------------- #
# The PDF, read back by position
# --------------------------------------------------------------------------- #

ROW_STEP = connection_drawing.ZONE_ROW


@dataclass(frozen=True)
class Char:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


def _join(chars: Iterable[Char]) -> str:
    return " ".join("".join(c.text for c in sorted(chars, key=lambda c: c.x0)).split())


@dataclass
class Page:
    text: str  # whole page, whitespace normalised
    area_lines: list[str]  # the drawing area's text lines (text sheets)
    chars: list[Char]  # in the drawing area
    dots: list[tuple[float, float]]  # filled terminal points (PDF coordinates)


@functools.cache
def pdf_pages(case: str) -> tuple[Page, ...]:
    pages: list[Page] = []
    with pdfplumber.open(io.BytesIO(render(case))) as pdf:
        for page in pdf.pages:
            height = float(page.height)
            chars = [
                Char(c["text"], c["x0"], c["x1"], height - c["bottom"], height - c["top"])
                for c in page.chars
                if pdf_module._AREA_LEFT < (c["x0"] + c["x1"]) / 2 < pdf_module._AREA_RIGHT
                and pdf_module._AREA_BOTTOM
                < height - (c["top"] + c["bottom"]) / 2
                < pdf_module._AREA_TOP
            ]
            area = page.crop(
                (
                    pdf_module._AREA_LEFT,
                    height - pdf_module._AREA_TOP,
                    pdf_module._AREA_RIGHT,
                    height - pdf_module._AREA_BOTTOM,
                )
            )
            dots = [
                ((c["x0"] + c["x1"]) / 2, height - (c["top"] + c["bottom"]) / 2)
                for c in page.curves
                if c.get("fill") and c["x1"] - c["x0"] < 3 * mm
            ]
            pages.append(
                Page(
                    text=" ".join((page.extract_text() or "").split()),
                    area_lines=[
                        " ".join(line.split())
                        for line in (area.extract_text() or "").splitlines()
                        if line.strip()
                    ],
                    chars=chars,
                    dots=dots,
                )
            )
    return tuple(pages)


@functools.cache
def model_of(case: str) -> entities.DesignModelV2:
    return _build(case)[0]


@functools.cache
def drawing_of(case: str) -> connection_drawing.DrawingSet:
    return connection_drawing.drawing_set(model_of(case))


@dataclass(frozen=True)
class Printed:
    """Where a terminal's point is drawn, read from the PDF."""

    sheet: int
    zone: str


def _zone_of(x: float, y: float) -> str:
    """The zone of a point, from the printed grid's geometry."""
    number = int((x - pdf_module._AREA_LEFT) // pdf_module._ZONE) + 1
    letter = connection_drawing.ZONE_LETTERS[int((pdf_module._AREA_TOP - y) // ROW_STEP)]
    return f"{number}{letter}"


@dataclass
class Read:
    """A drawing read back: each row's printed lines, place and what is unaccounted for."""

    printed: dict[Key, Printed]
    texts: dict[Key, list[tuple[int, str, str, str]]]  # (sheet, upper, lower, kind) per line
    markings: dict[Key, list[str]]  # marking texts printed, first and continued
    nets: dict[Key, list[str]]
    headers: list[tuple[connection_drawing.BlockPart, str, str, str]]  # part, upper, lower, left
    stray: list[tuple[int, str]]  # sheet, text
    boards: dict[int, set[str]] = field(default_factory=dict)  # boards each sheet shows


def _found(page: Page, text: connection_drawing.Text, used: set[int]) -> list[Char]:
    """The characters printed where the layout put a text."""
    width = text.width
    x0, x1 = (text.x - width, text.x) if text.right else (text.x, text.x + width)
    out = []
    for n, ch in enumerate(page.chars):
        if (
            x0 - 0.5 <= ch.xc <= x1 + 0.5
            and text.y - 0.3 * text.size <= ch.yc <= text.y + 0.8 * text.size
        ):
            out.append(ch)
            used.add(n)
    return out


@functools.cache
def read_back(case: str) -> Read:
    """Every drawing sheet's texts, read where the layout says they are and assigned to rows.

    The layout says only *where* to look (each text's page point); what is
    found there is checked against the model by the tests, and any printed
    character the layout does not account for (outside a symbol's own box)
    is stray.
    """
    drawing = drawing_of(case)
    pages = pdf_pages(case)
    out = Read({}, defaultdict(list), defaultdict(list), defaultdict(list), [], [])
    for sheet in drawing.sheets:
        page = pages[sheet.number - 1]
        used: set[int] = set()
        out.boards[sheet.number] = {part.board_id for part in sheet.parts}
        for part in sheet.parts:
            got: dict[tuple[str, Key | None, int], list[str]] = defaultdict(list)
            for text in part.texts:
                found = _join(_found(page, text, used))
                if found:
                    got[(text.role, text.key, text.index)].append(found)

            def joined(
                role: str,
                key: Key | None = None,
                index: int = -1,
                got: dict[tuple[str, Key | None, int], list[str]] = got,
            ) -> str:
                return " ".join(got.get((role, key, index), []))

            out.headers.append(
                (
                    part,
                    " ".join([joined("title"), joined("continues")]).strip(),
                    joined("header"),
                    joined("placeholder"),
                )
            )
            seen: set[Key] = set()
            for line in part.lines:
                key = line.row.key
                if key not in seen:
                    seen.add(key)
                    mark = joined("marking", key)
                    out.markings[key].append(mark)
                    out.nets[key].append(joined("net", key))
                    if not mark.endswith("(cont.)") and key not in out.printed:
                        markers = [t for t in part.texts if t.role == "marking" and t.key == key]
                        tx, ty = markers[0].x, markers[0].y
                        dot = min(page.dots, key=lambda d: abs(d[0] - tx) + abs(d[1] - ty))
                        assert abs(dot[1] - ty) < 3 * mm, (key, dot)
                        assert 0 < tx - dot[0] < 4 * mm, (key, dot)
                        out.printed[key] = Printed(sheet.number, _zone_of(*dot))
                if line.kind == "note":
                    out.texts[key].append(
                        (sheet.number, "", joined("note", key, line.index), "note")
                    )
                elif line.kind == "end":
                    out.texts[key].append(
                        (
                            sheet.number,
                            joined("far", key, line.index),
                            joined("detail", key, line.index),
                            "end",
                        )
                    )
        frames = []
        for part in sheet.parts:
            if part.symbol is not None and part.frame is not None:
                frames.append(part.frame.box(part.symbol))
        for n, ch in enumerate(page.chars):
            if n in used or not ch.text.strip():
                continue
            if any(
                x0 - 0.5 <= ch.xc <= x1 + 0.5 and y0 - 0.5 <= ch.yc <= y1 + 0.5
                for x0, y0, x1, y1 in frames
            ):
                continue
            out.stray.append((sheet.number, ch.text))
    return out


ELLIPSIS = "…"


def fits(printed: str, full: str) -> bool:
    """Printed in full (wrapped lines rejoined), or cut with a reference to its full text."""
    if squash(printed) == squash(full):
        return True
    found = re.search(r"…\[(\d+)\]", printed)
    if not found:
        return False
    head = printed[: found.start()].rstrip()
    return bool(head) and squash(full).startswith(squash(head))


def header_at(place: connection_drawing.Place) -> Printed:
    """Where a block part's header is: its place (the geometry tests read it from the PDF)."""
    return Printed(place.sheet, place.zone)


def far_line(label: str, target: Printed, here: int, via: str) -> str:
    return f"to {label} ({reference(target, here)}) via {via}"


def reference(target: Printed, here: int) -> str:
    if target.sheet == here:
        return f"this sheet, zone {target.zone}"
    return f"sheet {target.sheet}, zone {target.zone}"


def open_item_lines(case: str) -> list[str]:
    """The open-items sheets' items, each joined across its wrapped lines."""
    items: list[str] = []
    inside = False
    for page in pdf_pages(case):
        lines = page.area_lines
        if not lines:
            continue
        if lines[0].startswith("Open items: shown on the sheets and listed here"):
            inside = True
        elif inside:
            break
        if not inside:
            continue
        for line in lines[1:]:
            head = line.split(":", 1)[0]
            if head in connection_drawing.OPEN_KINDS or line == "No open items.":
                items.append(line)
            else:
                assert items, line
                items[-1] += " " + line
    return items


def squash(text: str) -> str:
    return "".join(text.split())


# --------------------------------------------------------------------------- #
# 1. Drawing, terminal schedule and wire schedule: the same rows
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_drawing_and_schedules_hold_the_models_terminals_nets_connections_and_wires(
    case: str,
) -> None:
    model = model_of(case)
    drawing = drawing_of(case)
    expected = expected_rows(model)
    terminal_rows = schedules.terminal_schedule(model)
    wire_rows = schedules.wire_schedule(model)
    assert drawing.terminal_rows == terminal_rows
    assert drawing.wire_rows == wire_rows
    assert [r.terminal for r in drawing.rows] == list(terminal_rows)
    # Nothing dropped or added, against the model itself.
    assert [r.key for r in drawing.rows] == [connection_drawing._row_key(t) for t in terminal_rows]
    assert Counter(r.key for r in drawing.rows) == Counter(list(expected))
    assert {w.wire_id for w in wire_rows} == {w.id for w in model.wires}
    assert len(wire_rows) == len(model.wires)
    assert drawing.wire_ids() == {w.id for w in model.wires}
    assert set(drawing.nets) == {n.id for n in model.nets} == {n.id for n in model.nets}
    assert drawing.connections == tuple(sorted(c.id for c in model.connections))
    assert drawing.connection_ids() == {c.id for c in model.connections}
    # Every net with a member is named by its members' rows.
    for net in model.nets:
        named = {r.key for r in drawing.rows if r.terminal.net_id == net.id}
        assert named == {m.key for m in net.members}, net.id
    # Each connection is drawn at exactly its two ends.
    ends = Counter((end.connection_id, row.key) for row in drawing.rows for end in row.ends)
    for c in model.connections:
        assert ends[(c.id, c.start.key)] >= 1
        assert ends[(c.id, c.end.key)] >= 1
    assert sum(ends.values()) == 2 * len(model.connections)
    # Each wire row is its wire's connection, ends and net.
    connections = {c.id: c for c in model.connections}
    for wire_row in wire_rows:
        made = connections[wire_row.connection_id]
        assert (wire_row.start_ref.key, wire_row.end_ref.key) == (made.start.key, made.end.key)
        assert wire_row.net_id == made.net_id
    # The terminal schedule's row for each terminal: owner, marking, net.
    for row in terminal_rows:
        found = expected[connection_drawing._row_key(row)]
        printed = row.marking if row.side is None else f"{row.marking} {row.side} L{row.level}"
        assert printed == found.marking
        assert (row.owner_kind, row.owner_id, row.board_id) == (
            found.owner_kind,
            found.owner_id,
            found.board_id,
        )
        assert row.net_id == (found.net.id if found.net else None)
        assert row.signal == (found.net.signal if found.net else None)


@pytest.mark.parametrize("case", [*ALL_CASES, *SAME_NAME_CASES])
def test_the_printed_drawing_holds_every_row_end_and_wire_and_nothing_else(case: str) -> None:
    model = model_of(case)
    expected = expected_rows(model)
    groups = net_groups(model)
    got = read_back(case)
    assert got.stray == []
    # Every terminal printed once, by its own marking, under its own owner.
    assert set(got.printed) == set(expected)
    owners: dict[str, Any] = {o.id: o for o in _owners(model)}
    for part, upper, _lower, _left in got.headers:
        if part.owner_kind == "no_terminals":
            assert all(m in owners for m in part.members)
            continue
        owner = owners[part.owner_id]
        name = owner.designation.product if owner.designation else owner.id
        assert squash(upper).startswith(squash(f"{name} ({part.owner_kind})")), (upper, name)
    for key, row in expected.items():
        marks = [squash(m) for m in got.markings[key]]
        assert marks[0] == squash(row.marking), (marks, row.marking)
        assert all(m == squash(f"{row.marking} (cont.)") for m in marks[1:]), marks
        net_text = f"net {row.net.signal}" if row.net else "net: none"
        assert [squash(n) for n in got.nets[key]] == [squash(net_text)] * len(marks)
    # Every row's lines: its notes, then one pair of lines per connection end,
    # each naming the far end where it is actually printed.
    by_end: dict[Key, list[entities.Connection]] = defaultdict(list)
    for c in model.connections:
        by_end[c.start.key].append(c)
        by_end[c.end.key].append(c)
    cut: list[tuple[Key, tuple[str, str], str]] = []
    for key, row in expected.items():
        printed = got.texts[key]
        notes = row_notes(row, groups, bool(by_end[key]))
        ends = [(c.end.key if c.start.key == key else c.start.key, c) for c in by_end[key]]
        needed = Counter(("note", n) for n in notes) + Counter(("end", c.id) for _f, c in ends)
        matched: Counter[tuple[str, str]] = Counter()
        # Each printed line is one note or one end; an end's reference reads
        # from the sheet the line is on. A text cut to fit ends in an ellipsis.
        for sheet, upper, lower, _kind in printed:
            options = [("note", n) for n in notes if not upper and fits(lower, n)]
            options += [
                ("end", c.id)
                for far, c in ends
                if any(
                    fits(upper, far_line(label, got.printed[far], sheet, c.via))
                    for label in far_labels(expected, key, far, got.boards[sheet])
                )
                and fits(lower, second_line(model, c))
            ]
            assert options, (key, sheet, upper, lower)
            matched[options[0]] += 1
            for text in (upper, lower):
                if ELLIPSIS in text:
                    cut.append((key, options[0], text))
        assert matched == needed, (key, matched, needed)
    # A text cut to fit names its full text, printed on the full-text sheets.
    whole = squash(" ".join(p.text for p in pdf_pages(case)))
    for _key, _option, text in cut:
        for number in re.findall(r"…\[(\d+)\]", text):
            full = drawing_of(case).full_texts[int(number) - 1]
            assert squash(f"[{number}] {full}") in whole, (text, full)
    # The wire numbers printed: each wire at both ends of its connection, no others.
    printed_wires = Counter(
        m.group(1)
        for lines in got.texts.values()
        for _s, _u, low, _k in lines
        if (m := re.match(r"^wire (\S+): no\. ", low))
    )
    assert printed_wires == Counter({w.id: 2 for w in model.wires})
    # The cover counts the model, not the layout.
    cover = pdf_pages(case)[0].text
    assert (
        f"Terminals: {len(expected)}; nets: {len(model.nets)}; "
        f"connections: {len(model.connections)}; wires: {len(model.wires)};"
    ) in cover


# --------------------------------------------------------------------------- #
# 2. Every open item shown on its row and listed
# --------------------------------------------------------------------------- #


def expected_open_items(model: entities.DesignModelV2) -> Counter[tuple[str, str]]:
    """``(kind, text the listed item must start with or contain)``, from the model."""
    rows = expected_rows(model)
    groups = net_groups(model)
    want: Counter[tuple[str, str]] = Counter()
    for net in model.nets:
        if len(groups[net.id]) > 1:
            want[
                ("unrealised_net", f"net {net.signal} ({net.id}): its connections do not join")
            ] += 1
    for row in rows.values():
        if row.net is not None:
            continue
        if row.owner_kind == "strip":
            kind = "unconnected_strip_point"
        else:
            kind = {
                True: "unconnected_required",
                None: "unconnected_unresolved",
                False: "unconnected_not_required",
            }[row.requiredness]
        want[(kind, f"{row.label}: ")] += 1
    wired = {w.connection_id for w in model.wires}
    for c in model.connections:
        if c.via == "wire" and c.id not in wired:
            want[("wire_record_missing", f"connection {c.id} (")] += 1
    for w in model.wires:
        for _name, attribute in (
            ("number", w.number),
            ("cross-section mm²", w.cross_section_mm2),
            ("colour", w.colour),
        ):
            if attribute.value is None:
                want[("wire_attribute_unset", f"wire {w.id} ")] += 1
            elif attribute.source == "assumption":
                want[("wire_attribute_assumed", f"wire {w.id} ")] += 1
    owners: list[tuple[str, Any]] = [("component", c) for c in model.components]
    owners += [("strip", s) for s in model.strips]
    listed_boards: set[str] = set()
    for kind, owner in owners:
        name = owner.designation.product if owner.designation else owner.id
        if owner.designation is None:
            want[("designation_not_set", f"{kind} {owner.id}: designation not set")] += 1
        template = model.template(owner.template_ref) if owner.template_ref else None
        if (
            kind == "component"
            and template is not None
            and not template.terminals
            and template.symbol is None
        ):
            # Listed once per board, each named in the item (M5-0).
            if owner.board_id not in listed_boards:
                listed_boards.add(owner.board_id)
                want[("no_terminals_defined", f"board {owner.board_id}: ")] += 1
            continue
        if template is None or template.symbol is None:
            want[("symbol_not_defined", f"{name} ({kind}): symbol not defined")] += 1
        if kind == "component":
            empty = template is not None and not template.terminals
        else:
            empty = not owner.points
        if empty:
            want[("no_terminals_defined", f"{name} ({kind}): ")] += 1
    return +want


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_open_item_is_shown_on_its_row_and_listed(case: str) -> None:
    model = model_of(case)
    drawing = drawing_of(case)
    want = expected_open_items(model)
    # The layout's list. ``unrouted`` items (M5) are a routing outcome, not a
    # model fact the oracle can derive: each names a connection of the model
    # the routed sheets list as not routed, once.
    layout_kinds = Counter(item.kind for item in drawing.open_items)
    unrouted = [item for item in drawing.open_items if item.kind == "unrouted"]
    assert sorted(item.subject for item in unrouted) == sorted(
        u.connection_id for u in drawing.routed.unrouted
    )
    assert {item.subject for item in unrouted} <= {c.id for c in model.connections}
    model_kinds = layout_kinds - Counter({"unrouted": len(unrouted)})
    assert model_kinds == sum((Counter({kind: n}) for (kind, _t), n in want.items()), Counter()), (
        layout_kinds,
        want,
    )
    # The printed list: one item per open item, each with its subject.
    listed = open_item_lines(case)
    if not want and not unrouted:
        assert listed == ["No open items."]
        return
    by_kind: dict[str, list[str]] = defaultdict(list)
    for line in listed:
        kind, _, rest = line.partition(": ")
        by_kind[kind].append(squash(rest))
    assert Counter({k: len(v) for k, v in by_kind.items()}) == layout_kinds
    for (kind, text), n in want.items():
        assert sum(squash(text) in item for item in by_kind[kind]) >= n, (kind, text)
    # Each terminal-level item names where its row is printed.
    rows = expected_rows(model)
    got = read_back(case)
    for row in rows.values():
        if row.net is None:
            where = got.printed[row.key]
            assert any(
                item.startswith(squash(f"{row.board_id}/{row.label}: "))
                and item.endswith(squash(f"(sheet {where.sheet}, zone {where.zone})"))
                for items in by_kind.values()
                for item in items
            ), row.label
    # Designation not set and symbol not defined are shown on the block too.
    for part, _upper, lower, left in got.headers:
        if part.owner_kind == "no_terminals":
            listed_members = " ".join(by_kind["no_terminals_defined"])
            for member in part.members:
                assert squash(f"component {member},") in listed_members, member
            continue
        owner: Any = next((o for o in _owners(model) if o.id == part.owner_id), None)
        assert owner is not None
        if owner.designation is None:
            assert f"designation not set (id {owner.id})" in lower
        template = model.template(owner.template_ref) if owner.template_ref else None
        if part.part == 1 and (template is None or template.symbol is None):
            assert left.startswith("symbol not defined"), (part.owner_id, left)


def test_the_open_cases_exercise_every_kind_of_open_item() -> None:
    kinds = {item.kind for case in OPEN_CASES + ALL_CASES for item in drawing_of(case).open_items}
    # A template with no terminals and an unresolved requiredness come from the data.
    assert {
        "unrealised_net",
        "unconnected_strip_point",
        "wire_record_missing",
        "wire_attribute_unset",
        "wire_attribute_assumed",
        "designation_not_set",
        "symbol_not_defined",
        "no_terminals_defined",
    } <= kinds
    assert kinds & {
        "unconnected_required",
        "unconnected_unresolved",
        "unconnected_not_required",
    }


# --------------------------------------------------------------------------- #
# 3. No invented values
# --------------------------------------------------------------------------- #

_WIRE = re.compile(r"^wire (\S+): no\. (.+); mm² (.+); colour (.+)$")
_FAR = re.compile(r"^to (.+) \((?:this sheet|sheet \d+), zone \d+[A-F]\) via (\S+)$")
_CORE = re.compile(r"^cable (\S+) core (\d+), core label (.+)$")
_VALUE = re.compile(r"^(?:not set|(.+) \[(user|data|unresolved)\]|(.+) assumed \((\S+)\))$")


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_printed_value_is_in_the_snapshot_or_reads_not_set(case: str) -> None:
    model = model_of(case)
    rows = expected_rows(model)
    got = read_back(case)
    wires = {w.id: w for w in model.wires}
    labels = {r.label for r in rows.values()}
    vias = {c.via for c in model.connections}
    cores = {
        (c.core.cable_id, str(c.core.index)): c.core.label
        for c in model.connections
        if c.core is not None
    }
    markings = {r.marking for r in rows.values()}
    signals = {f"net {n.signal}" for n in model.nets} | {"net: none"}
    assumptions = {a.id for a in model.assumptions}
    for key in rows:
        for mark in got.markings[key]:
            assert mark.removesuffix(" (cont.)") in markings, mark
        for net in got.nets[key]:
            assert net in signals, net
        for _sheet, upper, lower, _kind in got.texts[key]:
            if upper:
                far = _FAR.match(upper)
                assert far, upper
                assert far.group(1) in labels, upper
                assert far.group(2) in vias, upper
            wire = _WIRE.match(lower)
            if wire:
                held = wires[wire.group(1)]
                attributes = (held.number, held.cross_section_mm2, held.colour)
                for printed, attribute in zip(wire.groups()[1:], attributes, strict=True):
                    value = _VALUE.match(printed)
                    assert value, printed
                    if printed == "not set":
                        assert attribute.value is None, printed
                        continue
                    assert attribute.value is not None
                    shown = value.group(1) or value.group(3)
                    assert shown == str(attribute.value), (printed, attribute)
                    if value.group(3):
                        assert attribute.source == "assumption"
                        assert value.group(4) in assumptions | {"not set"}
                    else:
                        source = "unresolved" if attribute.source == "unset" else attribute.source
                        assert value.group(2) == source
            core = _CORE.match(lower)
            if core:
                label = cores[(core.group(1), core.group(2))]
                assert core.group(3) == (label if label is not None else "not set")
    # Values in the open-items list are the model's too.
    for line in open_item_lines(case):
        kind, _, rest = line.partition(": ")
        if kind in {"wire_attribute_unset", "wire_attribute_assumed"}:
            wire_id = rest.split(" ", 2)[1]
            assert wire_id in wires, line
            if kind == "wire_attribute_assumed":
                held = wires[wire_id]
                values = {
                    squash(f"{a.value} assumed ({a.assumption})")
                    for a in (held.number, held.cross_section_mm2, held.colour)
                    if a.source == "assumption"
                }
                assert any(v in squash(rest) for v in values), line


# --------------------------------------------------------------------------- #
# 4. Cross-references: symmetric, and to where the far end is drawn
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_cross_references_are_symmetric_and_name_where_the_far_end_is_drawn(case: str) -> None:
    drawing = drawing_of(case)
    got = read_back(case)
    by_key = {row.key: row for row in drawing.rows}
    for row in drawing.rows:
        # The row's own place is where its marking is printed.
        assert got.printed[row.key] == Printed(row.place.sheet, row.place.zone), row.label
        assert drawing.places[row.key] == row.place
        for end in row.ends:
            far = by_key[end.far_key]
            assert end.far_place == far.place
            back = [
                e for e in far.ends if e.connection_id == end.connection_id and e.far_key == row.key
            ]
            assert len(back) == (2 if far.key == row.key else 1), (row.label, end.connection_id)
            assert back[0].far_place == row.place
            assert back[0].via == end.via
            assert back[0].net_id == end.net_id
            assert (back[0].wire is None) == (end.wire is None)
            assert got.printed[end.far_key] == Printed(end.far_place.sheet, end.far_place.zone)
    # A continued block points at the slot where it continues, both ways.
    parts = [part for sheet in drawing.sheets for part in sheet.parts]
    headers = {(p.owner_id, p.part): (p, upper, lower) for p, upper, lower, _l in got.headers}
    for part in parts:
        if part.continues_on is not None:
            following = next(
                p for p in parts if p.owner_id == part.owner_id and p.part == part.part + 1
            )
            assert following.place == part.continues_on
            assert following.continued_from == part.place
            _p, upper, _lower = headers[(part.owner_id, part.part)]
            listing = part.owner_kind == "no_terminals"
            onward, back_word = (
                ("list goes on at", "list started on")
                if listing
                else ("continues on", "continued from")
            )
            assert f"{onward} {reference(header_at(following.place), part.place.sheet)}" in upper
            _p, _upper, lower = headers[(following.owner_id, following.part)]
            assert lower.startswith(
                f"{back_word} {reference(header_at(part.place), following.place.sheet)};"
            )


# --------------------------------------------------------------------------- #
# 5. Every sheet marked, hashed and numbered
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_sheet_is_marked_hashed_and_numbered_n_of_n(case: str) -> None:
    _model, digest = _build(case)
    pages = pdf_pages(case)
    drawing = drawing_of(case)
    total = len(pages)
    assert [s.number for s in drawing.sheets] == list(
        range(connection_drawing.FIRST_SHEET, connection_drawing.FIRST_SHEET + len(drawing.sheets))
    )
    assert total >= 1 + len(drawing.sheets) + 3
    for number, page in enumerate(pages, start=1):
        assert "NOT FOR EXECUTION" in page.text
        assert pdf_module.DRAWING_LABEL in page.text
        assert f"Snapshot SHA-256: {digest}" in page.text
        assert re.findall(r"Sheet (\d+) of (\d+)", page.text) == [(str(number), str(total))]
        assert set(re.findall(r"\b[0-9a-f]{64}\b", page.text)) == {digest}


# --------------------------------------------------------------------------- #
# 6. Determinism
# --------------------------------------------------------------------------- #

DETERMINISM_CASES = [CONFIG_CASES[0], "config:pe_n_distribution", OPEN_CASES[-1], "scale"]

_DIGESTS = """
import hashlib, json, sys
from app.tests.design.test_m4_consistency import render
print(json.dumps({c: hashlib.sha256(render(c)).hexdigest() for c in sys.argv[1:]}))
"""


@pytest.mark.parametrize("case", ALL_CASES)
def test_two_renders_are_byte_identical(case: str) -> None:
    assert render(case) == render(case)


def test_the_bytes_do_not_depend_on_the_hash_seed() -> None:
    here = {c: hashlib.sha256(render(c)).hexdigest() for c in DETERMINISM_CASES}
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", _DIGESTS, *DETERMINISM_CASES],
            capture_output=True,
            text=True,
            cwd=API_ROOT,
            env=env,
            check=False,
            timeout=300,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        assert json.loads(done.stdout.strip().splitlines()[-1]) == here, seed


# --------------------------------------------------------------------------- #
# 8. Scale
# --------------------------------------------------------------------------- #


def test_a_large_generated_model_paginates_without_overflow_and_lists_every_terminal() -> None:
    model = model_of("scale")
    drawing = drawing_of("scale")
    rows = expected_rows(model)
    assert len(model.components) >= 10 * SCALE_COPIES // 2
    assert len(rows) > 4 * connection_drawing.SLOTS * connection_drawing.COLUMNS
    assert len(drawing.sheets) > 3
    long_parts = [p for s in drawing.sheets for p in s.parts if p.owner_id == "strip-scale-long"]
    assert len(long_parts) > 2
    assert len({p.place.sheet for p in long_parts}) > 1
    # Every slot inside its column; every terminal in exactly one place.
    for sheet in drawing.sheets:
        for part in sheet.parts:
            assert part.place.sheet == sheet.number
            assert 0 <= part.place.column < connection_drawing.COLUMNS
            assert all(line.slot + line.span <= connection_drawing.SLOTS for line in part.lines)
    assert set(drawing.places) == set(rows)
    got = read_back("scale")
    assert set(got.printed) == set(rows)
    assert got.stray == []


@pytest.mark.parametrize("case", ALL_CASES)
def test_nothing_printed_crosses_its_column_or_the_drawing_area(case: str) -> None:
    drawing = drawing_of(case)
    pages = pdf_pages(case)
    for sheet in drawing.sheets:
        for ch in pages[sheet.number - 1].chars:
            if not ch.text.strip():
                continue
            # Inside the block part it belongs to (a block may take several columns).
            assert any(
                part.left - 0.5 <= ch.x0
                and ch.x1 <= part.right + 0.5
                and part.bottom - 0.5 <= ch.yc <= part.top + 0.5
                for part in sheet.parts
            ), (sheet.number, ch)
            assert ch.y0 >= pdf_module._AREA_BOTTOM - 0.5, (sheet.number, ch)
            assert ch.y1 <= pdf_module._AREA_TOP + 0.5, (sheet.number, ch)


@pytest.mark.parametrize("case", ALL_CASES)
def test_no_two_printed_characters_overlap(case: str) -> None:
    """Notes, cross-references and markings never share a baseline over each other."""
    drawing = drawing_of(case)
    pages = pdf_pages(case)
    for sheet in drawing.sheets:
        chars = sorted(
            (c for c in pages[sheet.number - 1].chars if c.text.strip()), key=lambda c: c.x0
        )
        for n, a in enumerate(chars):
            for b in chars[n + 1 :]:
                if b.x0 >= a.x1:
                    break
                vertical = min(a.y1, b.y1) - max(a.y0, b.y0)
                horizontal = min(a.x1, b.x1) - max(a.x0, b.x0)
                assert vertical < 1.0 or horizontal < 0.6, (sheet.number, a, b)
