"""Tests for `app/design/marking.py`."""

from __future__ import annotations

import io
import json

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from app.design import marking
from app.design.sheet import Sheet, Text
from app.models.schemas.design import DesignFinding, DesignProject, FindingSeverity

_FINDING = DesignFinding(
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


def test_the_page_stamp_label_and_identity_do_not_overlap() -> None:
    identity = "A long project name, revision 01, approved by Eng. Example on 2026-10-23, " + (
        "design 40beff92fcac, rules 2026.10.23.1"
    )
    mark = marking.ExportMark(marking.REVIEW_APPROVED_LABEL, identity)
    buffer = io.BytesIO()
    stamp = marking.pdf_page_stamp(mark)
    document = SimpleDocTemplate(buffer, pagesize=A4)
    document.build([Paragraph("Body", getSampleStyleSheet()["Normal"])], onFirstPage=stamp)
    with pdfplumber.open(io.BytesIO(buffer.getvalue())) as pdf:
        lines = (pdf.pages[0].extract_text() or "").splitlines()
    # Overlapping strings interleave into one garbled line; apart, each reads whole.
    assert lines[0] == marking.REVIEW_APPROVED_LABEL
    assert lines[1] == identity


def test_every_title_block_counts_the_findings_sheets(hall_project: DesignProject) -> None:
    """D6: a 10-page PDF said "1 of 9"; the findings sheets were not counted."""
    from app.design import profile
    from app.domain.design import drawing_sheets

    # Enough findings for more than one findings sheet.
    mark = marking.ExportMark(marking.DRAFT_LABEL, "Unsaved draft", (_FINDING,) * 60)
    assert marking.findings_sheet_count(mark) == 2
    sheets = drawing_sheets(hall_project, profile.default_profile(), mark)
    total = len(sheets)
    assert [sheet.number for sheet in sheets] == list(range(1, total + 1))
    drawing = [sheet for sheet in sheets if sheet.title != "Findings"]
    for sheet in drawing:
        assert f"{sheet.number} of {total}" in _texts(sheet), sheet.title
    findings = sheets[len(drawing) :]
    assert len(findings) == 2
    for sheet in findings:
        assert any(text.endswith(f"sheet {sheet.number} of {total}") for text in _texts(sheet))


def test_the_findings_sheet_count_matches_the_sheets_added() -> None:
    for count in (0, 1, 50, 51, 120):
        mark = marking.ExportMark("x", "y", (_FINDING,) * count)
        added = len(marking.mark_sheets([Sheet(number=1, title="A")], mark)) - 1
        assert marking.findings_sheet_count(mark) == added
