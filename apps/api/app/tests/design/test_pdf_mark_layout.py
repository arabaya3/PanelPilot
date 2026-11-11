"""No PDF export's mark (label, identity, approval) overlaps other text or leaves the page.

The mark is drawn at the top and foot of every page of every PDF export
(``marking.pdf_page_stamp`` for the calculations, quotation, schedule and
connection-drawing PDFs; ``marking.mark_sheets`` for the drawing PDF), beside
each document's own corner labels (the schedules' "REVIEW SCHEDULE — NOT FOR
EXECUTION — page N", the connection drawing's label). A long project name,
approver name or identity must wrap clear of those, never run under them,
off the page, or be cut short without saying so.

The check is on what a reader sees: every word's box (pdfplumber), in the
top and bottom bands of each page, must not intersect another's and must lie
inside the page.
"""

from __future__ import annotations

import io
from typing import Any

import pdfplumber
import pytest

from app.design import marking
from app.models.schemas.design import ExportFormat
from app.tests.design import test_renderer_version as reference

#: The PDF exports a v3 (and v2) review revision has; v1 has the first three.
PDF_FORMATS = (
    ExportFormat.PDF,
    ExportFormat.CALCULATIONS_PDF,
    ExportFormat.QUOTATION_PDF,
    ExportFormat.TERMINAL_SCHEDULE_PDF,
    ExportFormat.WIRE_SCHEDULE_PDF,
    ExportFormat.CONNECTION_DRAWING_PDF,
)

#: Project and approver names at their schema limits (200 characters each).
_LONG = {
    "en": (
        ("Synthetic long project name for the footer layout check " * 4)[:200].strip(),
        ("Approver With A Very Long Display Name For The Footer Check " * 4)[:200].strip(),
    ),
    "ar": (
        ("مشروع تجريبي باسم طويل جدا لفحص تذييل الصفحة " * 5)[:200].strip(),
        ("اسم المعتمد الطويل جدا لفحص التذييل " * 6)[:200].strip(),
    ),
    "he": (
        ("פרויקט לדוגמה עם שם ארוך מאוד לבדיקת כותרת תחתונה " * 5)[:200].strip(),
        ("שם מאשר ארוך מאוד לבדיקת הכותרת התחתונה " * 6)[:200].strip(),
    ),
}

#: How far from the top and bottom edge (pt) the mark and corner labels lie.
_BAND = 22 * 72 / 25.4


def long_identity(language: str) -> tuple[str, str]:
    """A worst-case identity (``_revision_mark``'s wording) and its project name."""
    project, approver = _LONG[language]
    return (
        f"{project}, revision 06, approved by {approver} on 2026-11-11, design b523d6af8653, "
        "rules 2026.11.03.1, renderer 2026.11.10.v3-1"
    ), project


def band_chars(page: Any) -> list[dict[str, Any]]:
    """The visible characters in a page's top and bottom bands."""
    height = float(page.height)
    return [
        c
        for c in page.chars
        if c["text"].strip() and (c["top"] < _BAND or c["bottom"] > height - _BAND)
    ]


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """The share of the smaller of two glyph boxes that the other covers."""
    w = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min(
        (a["x1"] - a["x0"]) * (a["bottom"] - a["top"]),
        (b["x1"] - b["x0"]) * (b["bottom"] - b["top"]),
    )
    return w * h / smaller if smaller > 0 else 0.0


def problems(content: bytes) -> list[str]:
    """Each two band glyphs drawn over each other, or a band glyph off the page.

    Glyphs, not words: pdfplumber merges two texts drawn over each other into
    one garbled "word", which a word check would pass. Neighbouring glyphs of
    one line may touch (kerning, joined scripts); a third of the smaller box
    covered is an overlap.
    """
    found: list[str] = []
    eps = 0.25
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            chars = sorted(band_chars(page), key=lambda c: c["x0"])
            for c in chars:
                if (
                    c["x0"] < -eps
                    or c["top"] < -eps
                    or c["x1"] > float(page.width) + eps
                    or c["bottom"] > float(page.height) + eps
                ):
                    found.append(f"page {number}: {c['text']!r} leaves the page")
            for i, a in enumerate(chars):
                for b in chars[i + 1 :]:
                    if b["x0"] >= a["x1"]:
                        break
                    if _overlap(a, b) > 1 / 3:
                        found.append(
                            f"page {number}: {a['text']!r} at ({a['x0']:.0f}, {a['top']:.0f}) "
                            f"overlaps {b['text']!r}"
                        )
    return found


def page_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)


@pytest.mark.parametrize("language", sorted(_LONG))
def test_a_worst_case_mark_overlaps_nothing_in_any_pdf(language: str) -> None:
    identity, project = long_identity(language)
    exported = reference.v3_exports("entered", identity=identity, project=project)
    failures = {
        name.value: found[:5] for name in PDF_FORMATS if (found := problems(exported[name.value]))
    }
    assert not failures, failures


def test_the_short_reference_mark_overlaps_nothing() -> None:
    exported = reference.v3_exports("entered")
    for name in PDF_FORMATS:
        assert not problems(exported[name.value]), name


def test_a_worst_case_mark_is_printed_whole() -> None:
    """Wrapped, the approver and the renderer version are not cut off the foot."""
    identity, project = long_identity("en")
    exported = reference.v3_exports("entered", identity=identity, project=project)
    for name in (ExportFormat.WIRE_SCHEDULE_PDF, ExportFormat.CALCULATIONS_PDF):
        text = page_text(exported[name.value])
        assert "renderer 2026.11.10.v3-1" in text, name
        assert marking.REVIEW_APPROVED_LABEL in text, name
