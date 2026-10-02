"""Tests for `app/design/export_dxf.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections import Counter
from io import BytesIO, StringIO

import ezdxf
from ezdxf import recover

from app.design import export_dxf, pages, profile
from app.design.sheet import SHEET_HEIGHT, SHEET_WIDTH, Anchor, Circle, Line, Rect, Sheet, Text
from app.models.schemas.design import DesignProject


def _read(data: bytes) -> ezdxf.document.Drawing:
    doc, auditor = recover.read(BytesIO(data))
    assert not auditor.errors, [str(e) for e in auditor.errors]
    return doc


def test_a_drawing_set_reads_back_without_errors(hall_project: DesignProject) -> None:
    sheets = pages.build_drawing_set(hall_project, profile.default_profile())
    doc = _read(export_dxf.export_dxf(sheets))
    assert doc.dxfversion == "AC1009"
    kinds = Counter(e.dxftype() for e in doc.modelspace())
    assert kinds["LINE"] > 100
    assert kinds["TEXT"] > 100
    assert {layer.dxf.name for layer in doc.layers} >= {"FRAME", "SYMBOLS", "TEXT", "DASHED"}


def test_geometry_is_placed_and_flipped() -> None:
    first = Sheet(number=1, title="A")
    first.add(Line(10, 20, 30, 20), Rect(0, 0, 5, 5, dashed=True), Circle(1, 2, 3))
    first.add(
        Text(100, 50, "Q1 C16"),
        Text(100, 60, "mid", anchor=Anchor.MIDDLE, rotation=90),
        Text(100, 70, "Café", anchor=Anchor.END),
    )
    second = Sheet(number=2, title="B")
    second.add(Line(10, 20, 30, 20))
    doc = _read(export_dxf.export_dxf([first, second]))
    lines = sorted(
        (e for e in doc.modelspace() if e.dxftype() == "LINE"), key=lambda e: e.dxf.start.x
    )
    assert (lines[0].dxf.start.x, lines[0].dxf.start.y) == (10, SHEET_HEIGHT - 20)
    # The second sheet sits to the right of the first, 20 mm apart.
    assert lines[1].dxf.start.x == 10 + SHEET_WIDTH + export_dxf.SHEET_GAP
    texts = {e.dxf.text: e for e in doc.modelspace() if e.dxftype() == "TEXT"}
    assert texts["mid"].dxf.halign == 1
    assert texts["mid"].dxf.rotation == 90
    assert "Caf" in next(t for t in texts if t.startswith("Caf"))
    polyline = next(e for e in doc.modelspace() if e.dxftype() == "POLYLINE")
    assert polyline.dxf.layer == "DASHED"
    assert len(list(polyline.vertices)) == 4  # type: ignore[attr-defined]


def test_output_is_ascii() -> None:
    sheet = Sheet(number=1, title="T")
    sheet.add(Text(0, 0, "مرحبا"))
    data = export_dxf.export_dxf([sheet])
    data.decode("ascii")
    assert "\\U+0645" in StringIO(data.decode()).getvalue()
