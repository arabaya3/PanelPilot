"""What an export says, separated from how it looks, for the renderer-version check.

``RENDERER_VERSION`` must change whenever the content a reader acts on
changes. This module reduces each available export to that content, so a test
can tell a material change from a presentational one:

* **PDF** (drawing, calculations, quotation): per page, its size, every word
  with its position and height (0.1 pt), and every line, rectangle and curve
  with its points, stroke width and dash pattern. Font files, colours and the
  file's metadata are left out.
* **DXF**: every entity's layer, type, text and geometry, polyline vertices
  included (0.001 mm), sorted. Handles, header variables, layer colours, line
  types and text styles are left out (dashing is the ``DASHED`` layer).
* **JSON**: the parsed document, canonically re-serialised.

Anything that may carry meaning is content, by the owner's rule that an
uncertain change is re-reviewed (the Phase 1 record, "Classification policy").
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import pdfplumber
from ezdxf import recover


def _round(value: Any, places: int = 1) -> Any:
    if isinstance(value, (list, tuple)):
        return [_round(item, places) for item in value]
    if isinstance(value, float | int):
        return round(float(value), places)
    return value


def _stroke(obj: dict[str, Any]) -> list[Any]:
    return [
        _round(obj.get("pts", []), 1),
        _round(obj.get("linewidth", 0.0), 2),
        str(obj.get("dash")),
        bool(obj.get("fill")),
        bool(obj.get("stroke", True)),
    ]


def pdf_content(content: bytes) -> list[dict[str, Any]]:
    """Each page's words with position and size, and every line, rect and curve.

    Positions to 0.1 pt; stroke width and dash pattern are kept, since a
    dashed line means something different from a solid one on a drawing.
    """
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            words = [
                [
                    w["text"],
                    _round(w["x0"]),
                    _round(w["top"]),
                    _round(w["bottom"] - w["top"]),
                    bool(w.get("upright", True)),
                ]
                for w in page.extract_words(extra_attrs=["size"])
            ]
            pages.append(
                {
                    "size": _round([page.width, page.height]),
                    "words": words,
                    "lines": sorted(json.dumps(_stroke(o)) for o in page.lines),
                    "rects": sorted(json.dumps(_stroke(o)) for o in page.rects),
                    "curves": sorted(json.dumps(_stroke(o)) for o in page.curves),
                }
            )
    return pages


def _point(value: Any) -> list[float]:
    return [round(float(axis), 3) for axis in value]


def dxf_content(content: bytes) -> list[list[Any]]:
    """Every entity's layer, type, text and geometry, sorted."""
    document, _auditor = recover.read(io.BytesIO(content))
    entities: list[list[Any]] = []
    entity: Any
    for entity in document.modelspace():
        kind = entity.dxftype()
        item: list[Any] = [entity.dxf.layer, kind]
        if kind == "LINE":
            item += [_point(entity.dxf.start), _point(entity.dxf.end)]
        elif kind in ("CIRCLE", "ARC"):
            item += [_point(entity.dxf.center), round(entity.dxf.radius, 3)]
            if kind == "ARC":
                item += [round(entity.dxf.start_angle, 3), round(entity.dxf.end_angle, 3)]
        elif kind == "POLYLINE":
            item += [[_point(p) for p in entity.points()], bool(entity.is_closed)]
        elif kind == "LWPOLYLINE":
            item += [[_point(p) for p in entity.get_points("xy")], bool(entity.closed)]
        elif kind in ("TEXT", "MTEXT"):
            item += [
                entity.dxf.text if kind == "TEXT" else entity.text,
                _point(entity.dxf.insert),
                round(entity.dxf.height if kind == "TEXT" else entity.dxf.char_height, 3),
            ]
        else:
            item.append(sorted(str(key) for key in entity.dxfattribs()))
        entities.append(item)
    return sorted(entities, key=lambda item: json.dumps(item, ensure_ascii=False))


def json_content(content: bytes) -> Any:
    """The parsed document."""
    return json.loads(content)


def fingerprint(content: object) -> str:
    """The SHA-256 of ``content``'s canonical JSON."""
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
