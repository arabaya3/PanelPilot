"""Tests for `app/design/marking.py`."""

from __future__ import annotations

import json

from app.design import marking
from app.design.sheet import Sheet, Text
from app.models.schemas.design import FindingSeverity, ValidationFinding

_FINDING = ValidationFinding(
    code="integrity_device_above_cable_capacity",
    severity=FindingSeverity.BLOCKING,
    board_id="DB1",
    circuit_id="c1",
    message="200 A on 1.5 mm²",
    source="ABB",
)
MARK = marking.ExportMark(marking.DRAFT_LABEL, "Unsaved draft", (_FINDING,))


def _texts(sheet: Sheet) -> list[str]:
    return [item.text for item in sheet.items if isinstance(item, Text)]


def test_every_sheet_is_marked_and_findings_follow() -> None:
    sheets = marking.mark_sheets([Sheet(number=1, title="A"), Sheet(number=2, title="B")], MARK)
    assert len(sheets) == 3
    for sheet in sheets:
        assert marking.DRAFT_LABEL in _texts(sheet)
    assert any("[BLOCKING]" in text for text in _texts(sheets[-1]))


def test_text_formats_carry_the_mark() -> None:
    assert marking.mark_csv("﻿a,b\n", MARK).startswith("﻿# DRAFT — NOT FOR EXECUTION")
    xml = marking.mark_xml(b'<?xml version="1.0"?>\n<root/>', MARK)
    assert xml.splitlines()[1].startswith("<!-- DRAFT — NOT FOR EXECUTION".encode())
    assert marking.mark_plc_source("PROGRAM P END_PROGRAM", MARK).startswith("(* DRAFT")
    envelope = json.loads(marking.mark_json('{"info": {}}', MARK))
    assert envelope["export_status"]["label"] == marking.DRAFT_LABEL
    assert envelope["findings"][0]["code"] == _FINDING.code
    assert envelope["project"] == {"info": {}}


def test_no_findings_is_said() -> None:
    assert marking.finding_lines(marking.ExportMark("x", "y")) == ["No unresolved findings."]
