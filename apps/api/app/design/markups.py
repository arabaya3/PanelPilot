"""Read a reviewer's markups off a drawing set PDF.

A consultant or checker returns the drawing set with comments: sticky notes,
text boxes, clouds and highlights with a note, as any PDF reader makes them.
Each one that carries text is read here with its page and, where the PDF is
this project's own drawing set (the same pages, in the same order), the
board that page draws and the label nearest the mark: the device, cable or
circuit it is most likely about.

Nothing is changed by reading them: the engineer reads the list and makes
the changes, which is the review they sign.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import pdfplumber

from app.core.errors import ValidationError
from app.design.sheet import AREA_BOTTOM, AREA_LEFT, AREA_RIGHT, AREA_TOP, Sheet, Text

#: Pages read at most; a drawing set is far shorter.
MAX_PAGES = 200

#: Annotations that are not a reviewer's mark: links, form fields, and the
#: popup window a note opens, which repeats its note's text.
_NOT_MARKUPS = frozenset({"Link", "Widget", "Popup"})

_MM_PER_POINT = 25.4 / 72


@dataclass(frozen=True)
class Markup:
    """One reviewer's mark with text.

    Attributes:
        page: Its page, from 1.
        kind: The PDF annotation type ("Text", "FreeText", "Square", ...).
        author: Who made it, where the PDF says.
        text: What it says.
        sheet: The title of the page it is on, where the PDF is this project's.
        board: The board that page draws, likewise.
        near: The drawing's label nearest the mark, likewise.
        labels: The drawing's labels by distance from the mark, nearest
            first, a few of them, for finding what it is about.
    """

    page: int
    kind: str
    author: str
    text: str
    sheet: str
    board: str
    near: str
    labels: tuple[str, ...] = ()


#: How many of the nearest labels a mark carries.
LABELS_KEPT = 8


def _nearest(sheet: Sheet, x_mm: float, y_mm: float) -> list[str]:
    """The drawing area's text labels by distance from a point, nearest first."""
    found: list[tuple[float, str]] = []
    for item in sheet.items:
        if not isinstance(item, Text) or len(item.text.strip()) < 2:
            continue
        if not (AREA_LEFT <= item.x <= AREA_RIGHT and AREA_TOP <= item.y <= AREA_BOTTOM):
            continue
        found.append((math.hypot(item.x - x_mm, item.y - y_mm), item.text.strip()))
    ordered: list[str] = []
    for _, text in sorted(found):
        if text not in ordered:
            ordered.append(text)
        if len(ordered) == LABELS_KEPT:
            break
    return ordered


def _subtype(annotation: dict[str, object]) -> str:
    data = annotation.get("data")
    raw = data.get("Subtype") if isinstance(data, dict) else None
    name = getattr(raw, "name", raw)
    return str(name).lstrip("/") if name else ""


def read_markups(data: bytes, sheets: list[Sheet] | None = None) -> tuple[list[Markup], bool]:
    """Read every mark with text from a PDF.

    Args:
        data: The PDF's bytes.
        sheets: This project's drawing set, laid out as the PDF was; ``None``
            when there is none to match.

    Returns:
        The marks in page order, and whether the PDF's pages matched
        ``sheets`` (when they did not, marks carry no sheet, board or label).

    Raises:
        ValidationError: If the file is not a readable PDF or is too long.
    """
    markups: list[Markup] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ValidationError(
                    f"the PDF has {len(pdf.pages)} pages; at most {MAX_PAGES} are read",
                    code="markups_too_long",
                    params={"limit": str(MAX_PAGES)},
                )
            matched = sheets is not None and len(sheets) == len(pdf.pages)
            for number, page in enumerate(pdf.pages, start=1):
                sheet = sheets[number - 1] if matched and sheets is not None else None
                for annotation in page.annots:
                    kind = _subtype(annotation)
                    text = str(annotation.get("contents") or "").strip()
                    if kind in _NOT_MARKUPS or not text:
                        continue
                    x_mm = (annotation["x0"] + annotation["x1"]) / 2 * _MM_PER_POINT
                    y_mm = (annotation["top"] + annotation["bottom"]) / 2 * _MM_PER_POINT
                    labels = _nearest(sheet, x_mm, y_mm) if sheet else []
                    markups.append(
                        Markup(
                            page=number,
                            kind=kind,
                            author=str(annotation.get("title") or ""),
                            text=text,
                            sheet=sheet.title if sheet else "",
                            board=sheet.board if sheet else "",
                            near=labels[0] if labels else "",
                            labels=tuple(labels),
                        )
                    )
    except ValidationError:
        raise
    except Exception as exc:  # pdfminer raises many types for a bad file
        raise ValidationError(
            f"the PDF could not be read: {exc}", code="markups_unreadable"
        ) from exc
    return markups, matched
