"""Drawing sheets: what a page of the drawing set holds, as plain geometry.

Pages are laid out once, as lines, rectangles, circles and text in
millimetres on the sheet, and every renderer (PDF, DXF) draws the same
geometry. A renderer that laid out its own pages would let the PDF and the
DXF of one project disagree.

Coordinates run from the sheet's top-left corner, x to the right and y
down, as a drawing is read; a renderer whose format counts from the bottom
flips them.

The sheet follows the frame convention of EPLAN and most ECAD tools: an A3
landscape frame divided into ten columns (0-9) and six rows (A-F), so a
cross-reference ("/3.4") names a page and a column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: A3 landscape, in millimetres.
SHEET_WIDTH = 420.0
SHEET_HEIGHT = 297.0

#: The drawing frame inside the sheet's margins.
FRAME_LEFT = 20.0
FRAME_TOP = 10.0
FRAME_RIGHT = SHEET_WIDTH - 10.0
FRAME_BOTTOM = SHEET_HEIGHT - 10.0

#: The title block's height, along the bottom of the frame.
TITLE_BLOCK_HEIGHT = 30.0

#: The drawing area: inside the frame, above the title block, inside the
#: column and row index strips.
INDEX_STRIP = 5.0
AREA_LEFT = FRAME_LEFT + INDEX_STRIP
AREA_RIGHT = FRAME_RIGHT - INDEX_STRIP
AREA_TOP = FRAME_TOP + INDEX_STRIP
AREA_BOTTOM = FRAME_BOTTOM - TITLE_BLOCK_HEIGHT - INDEX_STRIP

COLUMNS = 10
ROWS = "ABCDEF"


class Anchor(StrEnum):
    """Where a text's reference point is."""

    START = "start"
    MIDDLE = "middle"
    END = "end"


@dataclass(frozen=True)
class Line:
    """A straight line.

    Attributes:
        x1: Start, from the left edge.
        y1: Start, from the top edge.
        x2: End, from the left edge.
        y2: End, from the top edge.
        width: Pen width.
        dashed: Drawn dashed (a mechanical link, a boundary).
    """

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.25
    dashed: bool = False


@dataclass(frozen=True)
class Rect:
    """A rectangle, from its top-left corner.

    Attributes:
        x: Left edge.
        y: Top edge.
        w: Width.
        h: Height.
        width: Pen width.
        dashed: Drawn dashed.
    """

    x: float
    y: float
    w: float
    h: float
    width: float = 0.25
    dashed: bool = False


@dataclass(frozen=True)
class Circle:
    """A circle.

    Attributes:
        x: Centre, from the left edge.
        y: Centre, from the top edge.
        r: Radius.
        width: Pen width.
    """

    x: float
    y: float
    r: float
    width: float = 0.25


@dataclass(frozen=True)
class Text:
    """A line of text.

    Attributes:
        x: Reference point, from the left edge.
        y: Baseline, from the top edge.
        text: What it says.
        size: Character height.
        anchor: Where the reference point is along the text.
        bold: Drawn bold.
        rotation: Degrees counter-clockwise.
    """

    x: float
    y: float
    text: str
    size: float = 2.5
    anchor: Anchor = Anchor.START
    bold: bool = False
    rotation: float = 0.0


Item = Line | Rect | Circle | Text


@dataclass
class Sheet:
    """One page of the drawing set.

    Attributes:
        number: Page number, from 1.
        title: The page title the title block and contents print.
        board: The board it belongs to, where it belongs to one.
        items: The page's geometry, frame and title block included.
    """

    number: int
    title: str
    board: str = ""
    items: list[Item] = field(default_factory=list)

    def add(self, *items: Item) -> None:
        """Add geometry to the page."""
        self.items.extend(items)


def column_x(column: int) -> float:
    """Return the x of a column's centre.

    Args:
        column: 0 to 9.

    Returns:
        The centre, in millimetres from the left edge.
    """
    width = (AREA_RIGHT - AREA_LEFT) / COLUMNS
    return AREA_LEFT + width * (column + 0.5)


def column_of(x: float) -> int:
    """Return the column an x falls in, for a cross-reference.

    Args:
        x: Millimetres from the left edge.

    Returns:
        0 to 9, clamped to the frame.
    """
    width = (AREA_RIGHT - AREA_LEFT) / COLUMNS
    return max(0, min(COLUMNS - 1, int((x - AREA_LEFT) // width)))


def cross_reference(page: int, x: float) -> str:
    """Write a cross-reference to a point on a page, as EPLAN does.

    Args:
        page: The page number.
        x: The point's x.

    Returns:
        "/page.column", for example "/4.3".
    """
    return f"/{page}.{column_of(x)}"
