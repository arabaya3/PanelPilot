"""Where a board's devices sit on its DIN rails.

The rows follow the single-line diagram, so the panel reads as the drawing
does: the incomer; each residual current group (its group breaker, its RCCB,
then its circuits' devices); the circuits straight off the busbar; and the
outgoing terminal strip.

A device's width is its kind's width a pole, from the company profile
(``rail_widths_mm``), times its poles; for a breaker, only a modular one up to
63 A, and never for a device with a selected article, whose width is its own. Only what is sourced is given by
default (ABB S200 miniature breakers, 17.5 mm a pole, ``din_module_width``);
a device of another kind has no width here, is drawn as a dashed slot and
counted, and the total rail length leaves it out, so a short figure cannot
pass for a measured one. With a usable rail length per row, a row longer
than that continues on the next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.design import terminals
from app.models.schemas.design import Board, CompanyProfile, Device, DeviceKind

#: The largest modular (miniature) breaker the per-pole width covers: ABB's
#: S200 range, whose width is the default, ends at 63 A.
MODULAR_BREAKER_LIMIT_A = Decimal(63)


@dataclass(frozen=True)
class Slot:
    """One device on a rail.

    Attributes:
        device_id: The device, or "" for the terminal strip.
        label: What the layout prints on it.
        width_mm: Its width on the rail, where known.
    """

    device_id: str
    label: str
    width_mm: Decimal | None


@dataclass
class Rail:
    """One row of a board's DIN rail.

    Attributes:
        name: What the row holds.
        slots: Its devices, left to right.
    """

    name: str
    slots: list[Slot] = field(default_factory=list)

    @property
    def known_mm(self) -> Decimal:
        """The rail length the devices of known width take."""
        return sum((s.width_mm for s in self.slots if s.width_mm is not None), Decimal(0))

    @property
    def unknown(self) -> int:
        """How many of its devices have no width."""
        return sum(1 for s in self.slots if s.width_mm is None)


def width(device: Device, profile: CompanyProfile) -> Decimal | None:
    """A device's width on the rail.

    Args:
        device: The device.
        profile: The company, whose ranges' widths are its own.

    Returns:
        The width in mm, or ``None`` where its kind's width is not given.
    """
    per_pole = profile.rail_widths_mm.get(device.kind)
    if per_pole is None or device.part_key is not None:
        # A selected article (a motor starter's breaker, a drive) has its own
        # width, which the per-kind figure for the range does not give.
        return None
    if device.kind is DeviceKind.CIRCUIT_BREAKER and (
        device.rated_current_a is None or device.rated_current_a > MODULAR_BREAKER_LIMIT_A
    ):
        # Above the miniature range a breaker is a moulded-case one, of a
        # width the miniature breakers' figure says nothing about.
        return None
    return per_pole * (device.poles or 1)


def _label(device: Device) -> str:
    return f"-{device.designation.product}" if device.designation else device.id


def rails(board: Board, profile: CompanyProfile) -> list[Rail]:
    """Lay a board's devices out on rails, row by row.

    Args:
        board: The board, designated or not.
        profile: The company, for widths and the usable rail length.

    Returns:
        The rows, each no longer than the usable rail length where one is
        given (a single device longer than it still takes a row of its own).
    """
    by_id = {device.id: device for device in board.devices}
    placed: set[str] = set()

    def slot(device_id: str) -> Slot:
        placed.add(device_id)
        device = by_id[device_id]
        return Slot(device_id, _label(device), width(device, profile))

    rows: list[Rail] = [Rail("incomer", [slot(i) for i in board.incomer_ids if i in by_id])]
    groups: dict[str, Rail] = {}
    loose = Rail("busbar")
    for circuit in board.circuits:
        rcd = circuit.upstream_id
        if rcd is not None and rcd in by_id:
            rail = groups.get(rcd)
            if rail is None:
                group_breaker = by_id[rcd].upstream_id
                lead = [group_breaker] if group_breaker in by_id else []
                rail = Rail(f"group:{rcd}", [slot(d) for d in [*lead, rcd]])
                groups[rcd] = rail
        else:
            rail = loose
        rail.slots.extend(slot(d) for d in circuit.device_ids if d in by_id and d not in placed)
    rows.extend(groups.values())
    if loose.slots:
        rows.append(loose)
    strip = terminals.strip(board)
    if strip:
        terminal_width = profile.rail_widths_mm.get(DeviceKind.TERMINAL_STRIP)
        rows.append(
            Rail(
                "terminals",
                [Slot("", f"-{terminals.STRIP}:{t.number}", terminal_width) for t in strip],
            )
        )
    limit = profile.usable_rail_mm
    if limit is None:
        return rows
    split: list[Rail] = []
    for row in rows:
        current = Rail(row.name)
        for item in row.slots:
            taken = current.known_mm + (item.width_mm or Decimal(0))
            if current.slots and taken > limit:
                split.append(current)
                current = Rail(f"{row.name}:continued")
            current.slots.append(item)
        split.append(current)
    return split
