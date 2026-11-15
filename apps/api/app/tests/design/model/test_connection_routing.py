"""The routed sheets' layout (M5): one path per connection, from its data only.

Checks the layout objects of ``connection_routing`` on every configuration
(so a configuration added as data is checked with no code change); the PDF
read-back checks are in ``test_routed_topology.py``.
"""

from __future__ import annotations

import itertools
import math

import pytest

from app.design.model import connection_drawing, connection_routing, entities, write
from app.design.model.read import read_snapshot
from app.tests.design.model.v2_support import CONFIGS, config_case

CASES = sorted(CONFIGS)
Point = tuple[float, float]


def _model(case: str) -> entities.DesignModelV2:
    v1, configuration = config_case(case)
    model = read_snapshot(write.snapshot_v2_from_v1(v1, configuration)).v2
    assert model is not None
    return model


def _routed(case: str) -> tuple[entities.DesignModelV2, connection_routing.RoutedSet]:
    model = _model(case)
    routed = connection_drawing.drawing_set(model).routed
    assert isinstance(routed, connection_routing.RoutedSet)
    return model, routed


def _points(line: connection_routing.RoutedLine) -> list[Point]:
    """The line's corners, hops flattened to the straight run they bridge."""
    return [line.start, *((x, y) for _op, x, y in line.path)]


def _segments(line: connection_routing.RoutedLine) -> list[tuple[Point, Point]]:
    """The line's straight pieces; a hop (a curve over a crossed line) is not one."""
    out: list[tuple[Point, Point]] = []
    here = line.start
    for op, x, y in line.path:
        if op == "L" and math.dist(here, (x, y)) > 1e-6:
            out.append((here, (x, y)))
        here = (x, y)
    return out


@pytest.mark.parametrize("case", CASES)
def test_every_connection_is_routed_continued_or_unrouted_exactly_once(case: str) -> None:
    model, routed = _routed(case)
    ids = {c.id for c in model.connections}
    unrouted = {u.connection_id for u in routed.unrouted}
    drawn, continued = set(routed.routed), set(routed.continued)
    assert drawn | continued | unrouted == ids
    assert not drawn & continued
    assert not drawn & unrouted
    assert not continued & unrouted
    lines = [line for sheet in routed.sheets for line in sheet.lines]
    per = {cid: [line for line in lines if line.connection_id == cid] for cid in ids}
    for cid in drawn:
        assert len(per[cid]) == 1, cid
        assert not per[cid][0].continued, cid
    for cid in continued:
        # One flagged line on each of the two sheets its ends are on.
        assert len(per[cid]) == 2, cid
        assert all(line.continued for line in per[cid]), cid
    for cid in unrouted:
        assert not per[cid], cid
    for item in routed.unrouted:
        assert len(item.reason) >= 3


@pytest.mark.parametrize("case", CASES)
def test_each_line_runs_between_its_own_two_terminal_dots(case: str) -> None:
    model, routed = _routed(case)
    ends = {c.id: (c.start.key, c.end.key) for c in model.connections}
    for sheet in routed.sheets:
        dots = {(round(x, 3), round(y, 3)): r for (x, y), r in sheet.dots}
        for line in sheet.lines:
            pts = _points(line)
            a, b = ends[line.connection_id]
            here = [k for k in (a, b) if routed.places[k].sheet == sheet.number]
            if line.continued:
                assert len(here) == 1 or a == b, line.connection_id
            if not line.continued:
                assert len(here) == 2, line.connection_id
            for key in here:
                place = routed.places[key]
                radius = dots[(round(place.x, 3), round(place.y, 3))]
                near = min(math.dist(p, (place.x, place.y)) for p in (pts[0], pts[-1]))
                assert near <= radius, (line.connection_id, key)


@pytest.mark.parametrize("case", CASES)
def test_lines_never_overlap_and_cross_only_at_right_angles_with_a_hop(case: str) -> None:
    routed = _routed(case)[1]
    for sheet in routed.sheets:
        segs = [(line.connection_id, s) for line in sheet.lines for s in _segments(line)]
        for (c1, (a, b)), (c2, (p, q)) in itertools.combinations(segs, 2):
            if c1 == c2:
                continue
            for s, t in (((a, b), (p, q)), ((p, q), (a, b))):
                (ax, ay), (bx, by) = s
                (px, py), (qx, qy) = t
                horizontal = abs(ay - by) < 1e-6 and abs(px - qx) < 1e-6 and abs(py - qy) > 1e-6
                if horizontal:
                    x0, x1 = sorted((ax, bx))
                    y0, y1 = sorted((py, qy))
                    # A crossing in both interiors: the horizontal one hops it.
                    if x0 < px < x1 and y0 < ay < y1:
                        raise AssertionError(f"{c1} and {c2} cross with no hop at {px}, {ay}")
            same_h = abs(a[1] - b[1]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 and abs(a[1] - p[1]) < 1e-6
            same_v = abs(a[0] - b[0]) < 1e-6 and abs(p[0] - q[0]) < 1e-6 and abs(a[0] - p[0]) < 1e-6
            if same_h or same_v:
                k = 0 if same_h else 1
                lo1, hi1 = sorted((a[k], b[k]))
                lo2, hi2 = sorted((p[k], q[k]))
                overlap = min(hi1, hi2) - max(lo1, lo2)
                # Only lines leaving one dot may touch, and only inside it.
                assert overlap <= connection_routing.JUNCTION_R, (c1, c2, a, b, p, q)


@pytest.mark.parametrize("case", CASES)
def test_lines_stay_out_of_the_boxes(case: str) -> None:
    routed = _routed(case)[1]
    for sheet in routed.sheets:
        for line in sheet.lines:
            for a, b in _segments(line):
                for box in sheet.boxes:
                    x, y, w, h = box.rect
                    inside_x = min(a[0], b[0]) < x + w - 1e-6 and max(a[0], b[0]) > x + 1e-6
                    inside_y = min(a[1], b[1]) < y + h - 1e-6 and max(a[1], b[1]) > y + 1e-6
                    assert not (inside_x and inside_y), (line.connection_id, a, b, box.rect)


@pytest.mark.parametrize("case", CASES)
def test_labels_are_unique_prefixes_of_the_wire_or_connection_id(case: str) -> None:
    model, routed = _routed(case)
    wire_of = {w.connection_id: w.id for w in model.wires}
    ids = {c.id for c in model.connections} | set(wire_of.values())
    for cid, label in routed.labels.items():
        full = wire_of.get(cid, cid)
        assert label.startswith("w-" if cid in wire_of else "x-")
        assert full.startswith(label)
        assert [i for i in ids if i.startswith(label)] == [full]


@pytest.mark.parametrize("case", CASES)
def test_the_routed_layout_is_deterministic(case: str) -> None:
    model = _model(case)
    first = connection_drawing.drawing_set(model).routed
    second = connection_drawing.drawing_set(model).routed
    assert first == second


def test_junction_dots_mark_terminals_with_two_or_more_connections() -> None:
    model, routed = _routed("drawing_routed_crowded")
    count: dict[tuple[str, str, str], int] = {}
    for c in model.connections:
        for ref in (c.start, c.end):
            count[ref.key] = count.get(ref.key, 0) + 1
    for sheet in routed.sheets:
        radius = {(x, y): r for (x, y), r in sheet.dots}
        for key, place in routed.places.items():
            if place.sheet != sheet.number:
                continue
            want = (
                connection_routing.JUNCTION_R
                if count.get(key, 0) >= 2
                else connection_routing.DOT_R
            )
            assert radius[(place.x, place.y)] == want, key


def test_the_crowded_case_exercises_crossings_continuations_and_unrouted() -> None:
    routed = _routed("drawing_routed_crowded")[1]
    assert routed.routed
    assert routed.continued
    assert any(sheet.hops for sheet in routed.sheets)
    assert routed.unrouted
    assert all(
        u.reason.startswith(f"more than {connection_routing.MAX_FAN} connections")
        for u in routed.unrouted
    )
    assert len(routed.open_items) == len(routed.unrouted)
    assert all(item.kind == "unrouted" for item in routed.open_items)


def test_a_continuation_names_the_other_ends_sheet_and_zone() -> None:
    routed = _routed("drawing_routed_crowded")[1]
    texts = [t.text for sheet in routed.sheets for t in sheet.texts if t.role == "far"]
    assert texts
    for place in routed.places.values():
        assert place.zone  # every dot has a zone on its sheet
    for text in texts:
        assert text.startswith("to ")
        assert "(sheet " in text
        assert ", zone " in text
