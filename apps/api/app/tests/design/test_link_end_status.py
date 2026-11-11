"""A terminal ending a board-to-board feeder link is not called "unconnected" (M7b).

A strip point side in no net has no board-local wire; when it ends a feeder
link (a ``pp-design/3`` revision's links) the connection drawing says so and
names the far board and terminal, instead of "UNCONNECTED". The link adds no
wire, net or connection to either board's model.
"""

from __future__ import annotations

import io
from collections import Counter

import pdfplumber

from app.design.model import connection_drawing, entities, links, schedules
from app.models.schemas.design import ExportFormat
from app.tests.design import test_renderer_version as reference


def _end(role: str, board: str, terminal: str, key: tuple[str, str, str] | None) -> links.EndRow:
    return links.EndRow(
        role=role,
        board=board,
        terminal=terminal if key is not None else links.TERMINAL_NOT_SET,
        label=None,
        resolved=key is not None,
        key=key,
    )


def _row(link_id: str, a: links.EndRow, b: links.EndRow) -> links.LinkRow:
    return links.LinkRow(
        link_id=link_id,
        supply_board=a.board,
        fed_board=b.board,
        circuit_id="c2",
        carrier="c2-cable",
        ends=(a, b),
        attributes=(),
        differences=(),
    )


def test_each_modelled_end_names_its_far_end() -> None:
    a = _end("from", "MB-1", "X1:2 (inner)", ("point", "p-a", "inner"))
    b = _end("to", "SB-1", "X2:1 (inner)", ("point", "p-b", "inner"))
    unset = _end("to", "SB-2", "", None)
    notes = links.link_end_notes([_row("l-1", a, b), _row("l-2", a, unset)])
    assert notes[("point", "p-a", "inner")] == (
        "feeder link to SB-1 X2:1 (inner) (link l-1); "
        "feeder link to SB-2 terminal NOT SET (link l-2)"
    )
    assert notes[("point", "p-b", "inner")] == "feeder link to MB-1 X1:2 (inner) (link l-1)"
    assert len(notes) == 2


def _v3_model() -> entities.DesignModelV2:
    from app.design.model import write
    from app.design.model.read import read_snapshot
    from app.domain import design as design_domain
    from app.models.schemas.design import ProjectDesignRequest
    from app.tests.design.model import m7a_support

    request = ProjectDesignRequest.model_validate(m7a_support.request())
    designed, _ = design_domain.build_design(request)
    stored = write.snapshot_v2_from_v1(
        designed.model_dump(mode="json"), m7a_support.configuration()
    )
    model = read_snapshot(stored).v2
    assert model is not None
    return model


def test_a_strip_point_ending_a_link_says_so_instead_of_unconnected() -> None:
    row = next(
        r
        for r in schedules.terminal_schedule(_v3_model())
        if r.owner_kind != "component" and r.net_id is None
    )
    assert row.net_id is None
    key = connection_drawing._row_key(row)
    plain = connection_drawing._notes(row, key, {}, False)
    assert plain == [("unconnected_strip_point", "UNCONNECTED: this point side is in no net")]
    linked = connection_drawing._notes(row, key, {}, False, "feeder link to SB-1 X2:1 (link l-1)")
    assert linked == [
        (
            "strip_point_link_end",
            "LINK END: no board-local wire (in no net); feeder link to SB-1 X2:1 (link l-1)",
        )
    ]


def test_the_v3_drawing_marks_the_link_end_and_changes_nothing_electrical() -> None:
    model = _v3_model()
    point = next(s for s in model.strips if s.board_id == "MB-1").points[0].id
    ends = {("point", point, "outer"): "feeder link to SB-1 X (link l-1)"}
    plain = connection_drawing.drawing_set(model)
    marked = connection_drawing.drawing_set(model, link_ends=ends)
    kinds = Counter(item.kind for item in marked.open_items)
    before = Counter(item.kind for item in plain.open_items)
    assert kinds["strip_point_link_end"] == 1
    assert kinds["unconnected_strip_point"] == before["unconnected_strip_point"] - 1
    # Every other open item is still there (zones may move with the longer
    # note): no wire, net or connection is added.
    expected = before.copy()
    expected["unconnected_strip_point"] -= 1
    expected["strip_point_link_end"] += 1
    assert kinds == expected
    item = next(i for i in marked.open_items if i.kind == "strip_point_link_end")
    assert "no board-local wire" in item.text
    assert "SB-1" in item.text


def test_the_exported_v3_drawing_prints_link_end_not_unconnected() -> None:
    """The link starts at MB-1's strip point's outer side, which has no local wire."""
    exported = reference.v3_exports("entered", from_side="outer")[
        ExportFormat.CONNECTION_DRAWING_PDF.value
    ]
    with pdfplumber.open(io.BytesIO(exported)) as pdf:
        text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)
    assert "strip_point_link_end: MB-1/" in text
    assert "LINK END: no board-local wire (in no net); feeder link to SB-1" in text
    assert "UNCONNECTED: this point side is in no net" in text  # SB-1's outer side
    assert "LINK END: a terminal with no board-local wire" in text  # the legend
    assert text.count("unconnected_strip_point: ") == 1


def test_a_link_end_with_a_local_wire_keeps_its_net_note() -> None:
    """The reference link starts at an inner side in a net: nothing about it changes."""
    exported = reference.v3_exports("entered")[ExportFormat.CONNECTION_DRAWING_PDF.value]
    with pdfplumber.open(io.BytesIO(exported)) as pdf:
        text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)
    assert "strip_point_link_end" not in text
    assert text.count("unconnected_strip_point: ") == 2


def _component_row_in_no_net() -> schedules.TerminalRow:
    return next(
        r
        for r in schedules.terminal_schedule(_v3_model())
        if r.owner_kind == "component" and r.net_id is None
    )


def test_a_component_terminal_ending_a_link_leads_with_the_link_and_keeps_its_requirement() -> None:
    """The link note leads; an unmet board-local requirement stays its own open clause."""
    import dataclasses

    row = _component_row_in_no_net()
    key = connection_drawing._row_key(row)
    link = "feeder link to SB-1 X2:1 (link l-1)"
    lead = ("terminal_link_end", f"LINK END: no board-local wire (in no net); {link}")

    required = connection_drawing._notes(
        dataclasses.replace(row, required=True), key, {}, False, link
    )
    assert required[0] == lead
    assert required[1][0] == "unconnected_required"
    assert required[1][1].startswith("UNCONNECTED: required (documented)")
    assert "not met" in required[1][1]
    assert link not in required[1][1]  # the requirement is not called satisfied by the link

    unresolved = connection_drawing._notes(
        dataclasses.replace(row, required=None), key, {}, False, link
    )
    assert unresolved[0] == lead
    assert unresolved[1][0] == "unconnected_unresolved"
    assert unresolved[1][1].startswith("UNCONNECTED: requiredness unresolved")

    not_required = connection_drawing._notes(
        dataclasses.replace(row, required=False), key, {}, False, link
    )
    assert not_required == [lead, ("", "board-local connection not required (documented)")]

    # Without a link nothing changes.
    plain = connection_drawing._notes(dataclasses.replace(row, required=True), key, {}, False)
    assert plain == [("unconnected_required", "UNCONNECTED: required (documented)")]


def test_the_v3_drawing_lists_a_linked_component_terminal_as_link_end_and_still_open() -> None:
    model = _v3_model()
    row = _component_row_in_no_net()
    assert row.required is None
    key = connection_drawing._row_key(row)
    ends = {key: "feeder link to SB-1 X (link l-1)"}
    plain = connection_drawing.drawing_set(model)
    marked = connection_drawing.drawing_set(model, link_ends=ends)
    before = Counter(item.kind for item in plain.open_items)
    kinds = Counter(item.kind for item in marked.open_items)
    expected = before.copy()
    expected["terminal_link_end"] += 1  # the link end is listed; the requirement stays open
    assert kinds == expected
    subject = next(i.subject for i in marked.open_items if i.kind == "terminal_link_end")
    mine = [i for i in marked.open_items if i.subject == subject and i.board_id == row.board_id]
    assert [i.kind for i in mine] == ["unconnected_unresolved", "terminal_link_end"]
    lead = next(i for i in mine if i.kind == "terminal_link_end")
    assert "LINK END: no board-local wire" in lead.text
    assert "SB-1" in lead.text
    unresolved = next(i for i in mine if i.kind == "unconnected_unresolved")
    assert "feeder link" not in unresolved.text.split(": ", 1)[1].split(";")[0]
    drawn = next(r for r in marked.rows if r.terminal is not None and r.label == lead.subject)
    assert drawn.notes[0].startswith("LINK END")
