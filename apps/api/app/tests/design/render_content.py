"""What an export says, separated from how it looks, for the renderer-version check.

``RENDERER_VERSION`` must change whenever the content a reader acts on
changes. This module reduces each available export to that content, so a test
can tell a material change from a presentational one:

* **PDF** (drawing, calculations, quotation): the page count and, per page,
  the words printed on it, in reading order. Fonts, colours, line widths,
  dash patterns and the file's metadata are left out.
* **DXF**: every entity's layer, type, text and geometry (rounded to
  0.001 mm), sorted. Handles, header variables, layer colours, line types and
  text styles are left out.
* **JSON**: the parsed document, canonically re-serialised.

Classifications the code and documents do not settle (whether moving printed
text or drawing geometry without changing it is material) are counted as
material here, the stricter reading, and listed for an engineering decision in
the Phase 1 record ("Renderer version").
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import pdfplumber
from ezdxf import recover


def pdf_content(content: bytes) -> list[list[str]]:
    """Each page's words, in the order pdfplumber reads them."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [[word["text"] for word in page.extract_words()] for page in pdf.pages]


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
