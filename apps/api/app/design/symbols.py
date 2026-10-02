"""Single-line symbols, after IEC 60617, as sheet geometry.

Each symbol is drawn downward from a top connection point and returns its
geometry with the y of its bottom connection point, so a circuit is drawn by
stacking symbols down a column. Sizes are in millimetres, on the 2.5 mm text
grid most ECAD frames use.
"""

from __future__ import annotations

from app.design.sheet import Anchor, Circle, Item, Line, Rect, Text

#: Height of a switching device symbol, lead to lead.
DEVICE_HEIGHT = 20.0


def pole_marks(x: float, y: float, poles: int) -> list[Item]:
    """Mark a single-line conductor with its number of poles.

    One oblique stroke, and the number beside it when there is more than one
    conductor: the single-line convention of IEC 60617-3.

    Args:
        x: The conductor's x.
        y: Where along it to mark.
        poles: The number of conductors.

    Returns:
        The geometry.
    """
    items: list[Item] = [Line(x - 1.5, y + 1.0, x + 1.5, y - 1.0)]
    if poles > 1:
        items.append(Text(x + 2.0, y - 1.2, str(poles), size=2.0))
    return items


def circuit_breaker(x: float, top: float, poles: int = 1) -> tuple[list[Item], float]:
    """Draw a circuit-breaker: a switch with the breaker cross on its fixed contact.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    fixed = top + 6.0
    pivot = top + 14.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, fixed),
        # The breaker cross on the fixed contact.
        Line(x - 1.2, fixed - 1.2, x + 1.2, fixed + 1.2),
        Line(x - 1.2, fixed + 1.2, x + 1.2, fixed - 1.2),
        # The blade, open.
        Line(x, pivot, x - 3.5, fixed + 0.8),
        Line(x, pivot, x, bottom),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def switch_disconnector(x: float, top: float, poles: int = 4) -> tuple[list[Item], float]:
    """Draw a switch-disconnector: a switch with the isolating bar and load-switch ring.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    fixed = top + 6.0
    pivot = top + 14.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, fixed - 1.0),
        # The disconnector's bar across the fixed contact, and the ring that
        # makes it a switch able to break load (IEC 60617 S00288).
        Line(x - 1.5, fixed, x + 1.5, fixed),
        Circle(x, fixed - 1.0, 1.0),
        # The blade, open.
        Line(x, pivot, x - 3.5, fixed + 0.8),
        Line(x, pivot, x, bottom),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def residual_current_device(x: float, top: float, poles: int = 4) -> tuple[list[Item], float]:
    """Draw a residual current circuit-breaker: a switch with a summation transformer.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    contact = top + 6.0
    pivot = top + 12.0
    toroid = top + 16.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, contact),
        Line(x, pivot, x - 3.5, contact + 0.8),
        Line(x, pivot, x, bottom),
        # The summation transformer around the conductor, linked to the blade.
        Rect(x - 2.5, toroid - 1.2, 5.0, 2.4),
        Line(x - 2.5, toroid, x - 5.0, toroid, dashed=True),
        Line(x - 5.0, toroid, x - 5.0, contact + 4.0, dashed=True),
        Line(x - 5.0, contact + 4.0, x - 2.0, contact + 4.0, dashed=True),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def contactor(x: float, top: float, poles: int = 2) -> tuple[list[Item], float]:
    """Draw a contactor's main contact: a switch with the contactor's arc mark.

    The arc on the fixed contact (IEC 60617-7) is drawn as a small circle, the
    closest the sheet's primitives come; it reads the same on a single-line
    diagram and is unlike the breaker's cross.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    fixed = top + 6.0
    pivot = top + 14.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, fixed - 1.2),
        Circle(x, fixed, 1.2),
        Line(x, pivot, x - 3.5, fixed + 0.8),
        Line(x, pivot, x, bottom),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def overload_relay(x: float, top: float, poles: int = 3) -> tuple[list[Item], float]:
    """Draw a thermal overload relay: a box with the thermal element's step.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    bottom = top + DEVICE_HEIGHT
    box_top = top + 6.0
    items: list[Item] = [
        Line(x, top, x, box_top),
        Rect(x - 3.0, box_top, 6.0, 8.0),
        # The thermal element (IEC 60617-7 07-15-01): a step in the conductor.
        Line(x, box_top, x, box_top + 2.5),
        Line(x, box_top + 2.5, x + 1.5, box_top + 2.5),
        Line(x + 1.5, box_top + 2.5, x + 1.5, box_top + 5.5),
        Line(x + 1.5, box_top + 5.5, x, box_top + 5.5),
        Line(x, box_top + 5.5, x, bottom),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def fuse(x: float, top: float, poles: int = 3) -> tuple[list[Item], float]:
    """Draw a fuse: a rectangle with the conductor through it (IEC 60617-7 07-21-01).

    Args:
        x: The conductor's x.
        top: The upper connection point's y.
        poles: The number of poles, marked on the upper lead.

    Returns:
        The geometry, and the lower connection point's y.
    """
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, bottom),
        Rect(x - 1.5, top + 6.0, 3.0, 8.0),
        *pole_marks(x, top + 3.0, poles),
    ]
    return items, bottom


def drive(x: float, top: float) -> tuple[list[Item], float]:
    """Draw a variable-speed drive: a converter box, AC in and AC out (IEC 60617-6).

    Args:
        x: The conductor's x.
        top: The upper connection point's y.

    Returns:
        The geometry, and the lower connection point's y.
    """
    box_top = top + 4.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, box_top),
        Rect(x - WIDE_HALF_WIDTH, box_top, 2 * WIDE_HALF_WIDTH, 12.0),
        Line(x - WIDE_HALF_WIDTH, box_top + 12.0, x + WIDE_HALF_WIDTH, box_top),
        Text(x - 4.5, box_top + 4.0, "~", size=2.5),
        Text(x + 1.5, box_top + 10.0, "~", size=2.5),
        Line(x, box_top + 12.0, x, bottom),
    ]
    return items, bottom


def star_delta(x: float, top: float) -> tuple[list[Item], float]:
    """Draw a star-delta changeover as one block, the way a single-line diagram shows it.

    Args:
        x: The conductor's x.
        top: The upper connection point's y.

    Returns:
        The geometry, and the lower connection point's y.
    """
    box_top = top + 4.0
    bottom = top + DEVICE_HEIGHT
    items: list[Item] = [
        Line(x, top, x, box_top),
        Rect(x - WIDE_HALF_WIDTH, box_top, 2 * WIDE_HALF_WIDTH, 12.0),
        # "Y/D": the frame fonts have no delta.
        Text(x, box_top + 7.5, "Y/D", size=2.8, anchor=Anchor.MIDDLE),
        Line(x, box_top + 12.0, x, bottom),
    ]
    return items, bottom


def cable_end(x: float, top: float, length: float = 8.0) -> tuple[list[Item], float]:
    """Draw an outgoing cable ending in a terminal point.

    Args:
        x: The conductor's x.
        top: Where the cable leaves the device above.
        length: How far it runs before the terminal point.

    Returns:
        The geometry, and the y below the terminal point.
    """
    end = top + length
    return [Line(x, top, x, end - 1.0), Circle(x, end, 1.0)], end + 1.0


def busbar(x1: float, x2: float, y: float) -> list[Item]:
    """Draw a busbar between two x's.

    Args:
        x1: Left end.
        x2: Right end.
        y: Its height on the sheet.

    Returns:
        The geometry.
    """
    return [Line(x1, y, x2, y, width=0.8)]


#: Half the width of the widest symbol (a drive or a star-delta block).
WIDE_HALF_WIDTH = 6.0


def labels(
    x: float, y: float, lines: list[str], size: float = 2.2, *, clearance: float = 4.0
) -> list[Item]:
    """Write a device's labels beside it, one per line.

    Args:
        x: The device's x; text starts ``clearance`` right of it.
        y: The first baseline.
        lines: The labels, top to bottom; empty ones are skipped.
        size: Character height.
        clearance: How far right of the conductor the text starts; more
            than the symbol's half-width, for a boxed symbol.

    Returns:
        The geometry.
    """
    items: list[Item] = []
    row = y
    for line in lines:
        if line:
            items.append(Text(x + clearance, row, line, size=size, anchor=Anchor.START))
            row += size * 1.35
    return items
