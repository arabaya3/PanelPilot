"""Export drawing sheets as DXF: the drawing format every CAD tool opens.

The same sheet geometry the PDF is drawn from, written as AutoCAD R12 ASCII
DXF, the oldest and most widely read version: AutoCAD, AutoCAD Electrical,
BricsCAD, LibreCAD, EPLAN's and SEE Electrical's DXF import all read it.
Each sheet is laid side by side in model space, 20 mm apart, in millimetres,
on layers by kind (frame, symbols, text) so a CAD user can switch them.

A DXF is a drawing, not a schematic: it carries no device or connection
data. The device and cable lists (export_lists) carry that.

CAD text is drawn glyph after glyph as written, with no joining or
direction, so Arabic and Hebrew are written the way the PDF draws them:
joined and in visual order (``rtl``). They take a text style on Arial, which
holds Arabic and Hebrew, since the default ``txt`` shape font does not;
Latin text keeps the default style.
"""

from __future__ import annotations

from io import StringIO

from app.design import rtl
from app.design.sheet import SHEET_HEIGHT, SHEET_WIDTH, Anchor, Circle, Line, Rect, Sheet, Text

#: Space between sheets laid side by side.
SHEET_GAP = 20.0

_LAYERS = {"FRAME": 7, "SYMBOLS": 7, "TEXT": 7, "DASHED": 8}

#: The text style right-to-left text is written in, and its font.
RTL_STYLE = "PP_RTL"
_RTL_FONT = "arial.ttf"

#: DXF horizontal justification codes (group 72), and the code a centred or
#: right-aligned text needs its second alignment point (11/21) for.
_JUSTIFY = {Anchor.START: 0, Anchor.MIDDLE: 1, Anchor.END: 2}


def _pairs(out: StringIO, *pairs: tuple[int, object]) -> None:
    for code, value in pairs:
        out.write(f"{code}\n{value}\n")


def _number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _clean(text: str) -> str:
    r"""R12 text is single-line ASCII; anything else becomes a ``\U+`` escape."""
    return "".join(ch if 32 <= ord(ch) < 127 else f"\\U+{ord(ch):04X}" for ch in text)


def export_dxf(sheets: list[Sheet]) -> bytes:
    """Write sheets as one DXF drawing, side by side.

    Args:
        sheets: The pages, in order.

    Returns:
        The DXF file's bytes (ASCII).
    """
    out = StringIO()
    _pairs(out, (0, "SECTION"), (2, "HEADER"))
    _pairs(out, (9, "$ACADVER"), (1, "AC1009"), (9, "$INSUNITS"), (70, 4))
    _pairs(out, (0, "ENDSEC"))
    _pairs(out, (0, "SECTION"), (2, "TABLES"))
    _pairs(out, (0, "TABLE"), (2, "LTYPE"), (70, 2))
    _pairs(out, (0, "LTYPE"), (2, "CONTINUOUS"), (70, 0), (3, "Solid line"))
    _pairs(out, (72, 65), (73, 0), (40, 0.0))
    _pairs(out, (0, "LTYPE"), (2, "DASHED"), (70, 0), (3, "Dashed"))
    _pairs(out, (72, 65), (73, 2), (40, 2.5), (49, 1.5), (49, -1.0))
    _pairs(out, (0, "ENDTAB"))
    _pairs(out, (0, "TABLE"), (2, "LAYER"), (70, len(_LAYERS)))
    for name, colour in _LAYERS.items():
        linetype = "DASHED" if name == "DASHED" else "CONTINUOUS"
        _pairs(out, (0, "LAYER"), (2, name), (70, 0), (62, colour), (6, linetype))
    _pairs(out, (0, "ENDTAB"))
    _pairs(out, (0, "TABLE"), (2, "STYLE"), (70, 1))
    _pairs(
        out,
        (0, "STYLE"),
        (2, RTL_STYLE),
        (70, 0),
        (40, 0.0),
        (41, 1.0),
        (50, 0.0),
        (71, 0),
        (42, 2.5),
        (3, _RTL_FONT),
        (4, ""),
    )
    _pairs(out, (0, "ENDTAB"), (0, "ENDSEC"))

    _pairs(out, (0, "SECTION"), (2, "ENTITIES"))
    for index, sheet in enumerate(sheets):
        dx = index * (SHEET_WIDTH + SHEET_GAP)

        def point(x: float, y: float, dx: float = dx) -> tuple[str, str]:
            # Sheets count y down from the top; DXF counts up from the bottom.
            return _number(x + dx), _number(SHEET_HEIGHT - y)

        for item in sheet.items:
            if isinstance(item, Line):
                (x1, y1), (x2, y2) = point(item.x1, item.y1), point(item.x2, item.y2)
                layer = "DASHED" if item.dashed else "SYMBOLS"
                _pairs(
                    out,
                    (0, "LINE"),
                    (8, layer),
                    (10, x1),
                    (20, y1),
                    (30, 0),
                    (11, x2),
                    (21, y2),
                    (31, 0),
                )
            elif isinstance(item, Rect):
                corners = [
                    (item.x, item.y),
                    (item.x + item.w, item.y),
                    (item.x + item.w, item.y + item.h),
                    (item.x, item.y + item.h),
                ]
                layer = "DASHED" if item.dashed else "FRAME"
                _pairs(out, (0, "POLYLINE"), (8, layer), (66, 1), (70, 1))
                for x, y in corners:
                    px, py = point(x, y)
                    _pairs(out, (0, "VERTEX"), (8, layer), (10, px), (20, py), (30, 0))
                _pairs(out, (0, "SEQEND"), (8, layer))
            elif isinstance(item, Circle):
                cx, cy = point(item.x, item.y)
                _pairs(
                    out,
                    (0, "CIRCLE"),
                    (8, "SYMBOLS"),
                    (10, cx),
                    (20, cy),
                    (30, 0),
                    (40, _number(item.r)),
                )
            elif isinstance(item, Text):
                tx, ty = point(item.x, item.y)
                pairs: list[tuple[int, object]] = [
                    (0, "TEXT"),
                    (8, "TEXT"),
                    (10, tx),
                    (20, ty),
                    (30, 0),
                    (40, _number(item.size)),
                    (1, _clean(rtl.visual(item.text))),
                ]
                if rtl.has_rtl(item.text):
                    pairs.append((7, RTL_STYLE))
                if item.rotation:
                    pairs.append((50, _number(item.rotation)))
                justify = _JUSTIFY[item.anchor]
                if justify:
                    pairs += [(72, justify), (11, tx), (21, ty), (31, 0)]
                _pairs(out, *pairs)
    _pairs(out, (0, "ENDSEC"), (0, "EOF"))
    return out.getvalue().encode("ascii")
