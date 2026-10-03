"""The enclosure a board's modular devices fit in, from ABB's catalogue.

ABB's *Distribution Boards* catalogue (1SKC802027C0201) gives the Mini Center
compact multi-row boards as 2 to 5 rows of 16 modules (p. 7), for 220-440 V,
a 200/250 A busbar and a 35 kA fault level, IP41 (p. 5). A module is ABB's
17.5 mm, the width of one pole of an S200 breaker (``din_module_width``).

A board is laid out on rows of 16 modules (``layout``); the smallest of these
enclosures with that many rows, and room for the company's spare ways, is
named. Nothing is named where the company sets its own rail length (its
enclosure is its own), where a device has no width or is not modular (a
moulded-case breaker, a starter, a drive), or where the board's incomer or
fault level is beyond the enclosure's: 200 A, the lower busbar rating, and
35 kA. The outgoing terminal strip is not counted, nor the neutral and earth
bars the enclosure carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from app.design import layout
from app.design.notes import note
from app.models.schemas.design import Board, CompanyProfile, DesignNote

SOURCE = "ABB Distribution Boards catalogue (1SKC802027C0201), Mini Center compact, pp. 5, 7"

#: ABB's module width, mm, and the modules on one row.
MODULE_MM = Decimal("17.5")
MODULES_PER_ROW = 16

#: The busbar (the lower of 200/250 A) and fault level the boards are given for.
BUSBAR_A = Decimal(200)
FAULT_KA = Decimal(35)


@dataclass(frozen=True)
class Enclosure:
    """One Mini Center compact multi-row board.

    Attributes:
        type_number: ABB's type code.
        order_number: ABB's order code.
        rows: Its rows of 16 modules.
        size_mm: Height, width and depth.
    """

    type_number: str
    order_number: str
    rows: int
    size_mm: tuple[int, int, int]


ENCLOSURES: tuple[Enclosure, ...] = (
    Enclosure("GDMS332 RX", "2CVA250001P0001", 2, (498, 400, 117)),
    Enclosure("GDMS348 RX", "2CVA360001P0001", 3, (648, 400, 117)),
    Enclosure("GDMS364 RX", "2CVA470001P0001", 4, (752, 400, 117)),
    Enclosure("GDMS380 RX", "2CVA580001P0001", 5, (920, 400, 117)),
)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def notes_for(board: Board, profile: CompanyProfile) -> list[DesignNote]:
    """What the board says of its enclosure.

    Args:
        board: The designed board.
        profile: The company, for widths, its rail length and spare ways.

    Returns:
        One note: the enclosure named, or why none is; none at all where the
        company sets its own rail length.
    """
    if profile.usable_rail_mm is not None:
        return []
    row_mm = MODULE_MM * MODULES_PER_ROW
    rows = [
        row
        for row in layout.rails(board, profile.model_copy(update={"usable_rail_mm": row_mm}))
        if not row.name.startswith("terminals") and row.slots
    ]
    unknown = sum(row.unknown for row in rows)
    if unknown:
        return [note("enclosure_unknown_widths", count=unknown)]
    if any(board.device(slot.device_id).part_key for row in rows for slot in row.slots):
        return [note("enclosure_not_modular")]
    incomer = board.device(board.incomer_ids[0]) if board.incomer_ids else None
    fault = board.supply.fault_level_ka
    too_much = incomer is not None and (incomer.rated_current_a or BUSBAR_A + 1) > BUSBAR_A
    if too_much or (fault is not None and fault > FAULT_KA):
        return [note("enclosure_beyond", busbar=_plain(BUSBAR_A), fault=_plain(FAULT_KA))]
    used = sum((row.known_mm for row in rows), Decimal(0)) / MODULE_MM
    spare = (used * profile.spare_ways_percent / 100).to_integral_value(ROUND_CEILING)
    needed = len(rows)
    while needed * MODULES_PER_ROW - used < spare:
        needed += 1
    chosen = next((e for e in ENCLOSURES if e.rows >= needed), None)
    if chosen is None:
        return [note("enclosure_beyond", busbar=_plain(BUSBAR_A), fault=_plain(FAULT_KA))]
    height, width, depth = chosen.size_mm
    return [
        note(
            "enclosure_selected",
            type=chosen.type_number,
            order=chosen.order_number,
            rows=chosen.rows,
            size=f"{height} x {width} x {depth}",
            free=_plain(chosen.rows * MODULES_PER_ROW - used),
            source=SOURCE,
        )
    ]
