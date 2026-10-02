"""Price a designed project from a company's price list.

Each device is matched to a price by its article where one is chosen
(order number, then type number) and by its rating key where not
("circuit_breaker:1P:C16"), so a company can price a design before its
catalogue is loaded. Cables are priced per metre at their own length, or
at the company's default length where they have none.

A line with no price is never priced at a guess: it stays in the quotation
without an amount, is listed as unpriced, and the quotation says it is
incomplete. The total is the total of what was priced.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.core.errors import ValidationError
from app.design.export_lists import write_csv
from app.design.schedule_import import read_rows
from app.models.schemas.design import (
    Cable,
    DesignProject,
    Device,
    DeviceKind,
    Part,
    PriceListEntry,
    PricingSettings,
    Quotation,
    QuotationLine,
)

_CENT = Decimal("0.01")

_KIND_NAMES: dict[DeviceKind, str] = {
    DeviceKind.CIRCUIT_BREAKER: "Circuit-breaker",
    DeviceKind.RESIDUAL_CURRENT_DEVICE: "Residual current circuit-breaker",
    DeviceKind.CONTACTOR: "Contactor",
    DeviceKind.SURGE_PROTECTOR: "Surge protective device",
}


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def device_key(device: Device) -> str:
    """The rating key a device is priced by when it has no article.

    Args:
        device: The device.

    Returns:
        "circuit_breaker:1P:C16", "residual_current_device:4P:40A:30mA",
        "contactor:2P:20A", "switch_disconnector:4P:63A", ...
    """
    parts = [device.kind.value]
    if device.poles:
        parts.append(f"{device.poles}P")
    if device.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE:
        parts.append(f"{_plain(device.rated_current_a)}A")
        parts.append(f"{_plain(device.residual_current_ma)}mA")
    elif (
        device.kind in (DeviceKind.CONTACTOR, DeviceKind.SWITCH_DISCONNECTOR)
        and device.rated_current_a is not None
    ):
        parts.append(f"{_plain(device.rated_current_a)}A")
    elif device.rated_current_a is not None:
        parts.append(f"{device.curve or ''}{_plain(device.rated_current_a)}")
    return ":".join(parts)


def cable_key(cable: Cable) -> str:
    """The key a cable type is priced by, per metre.

    Args:
        cable: The cable.

    Returns:
        "cable:3G2.5:Cu:PVC".
    """
    return (
        f"cable:{cable.cores}G{_plain(cable.cross_section_mm2)}:{cable.material}:{cable.insulation}"
    )


def _normalise(key: str) -> str:
    return key.strip().lower().replace(" ", "")


@dataclass
class _Item:
    """Identical devices or cable runs gathered onto one quotation line."""

    name: str
    unit: str
    quantity: Decimal
    labels: list[str]
    entry: PriceListEntry | None


def _device_name(device: Device, part: Part | None) -> str:
    if part:
        return f"{part.manufacturer} {part.type_number}"
    rating = device_key(device).split(":", 1)[-1].replace(":", " ")
    return f"{_KIND_NAMES.get(device.kind, device.kind.value)} {rating}"


def price_project(project: DesignProject, pricing: PricingSettings) -> Quotation:
    """Price a designed project.

    Args:
        project: The project, designated.
        pricing: The company's prices and rates.

    Returns:
        The quotation, with every unpriced line named.
    """
    prices = {_normalise(entry.key): entry for entry in pricing.price_list}

    def find(*keys: str | None) -> tuple[str, PriceListEntry | None]:
        for key in keys:
            if key and _normalise(key) in prices:
                return key, prices[_normalise(key)]
        return next((k for k in keys if k), ""), None

    # Identical items share a line, in the order first seen.
    items: dict[str, _Item] = {}

    def add(key: str, item: _Item) -> None:
        if key in items:
            items[key].quantity += item.quantity
            items[key].labels.extend(item.labels)
        else:
            items[key] = item

    for board in project.boards:
        prefix = f"{board.name} " if len(project.boards) > 1 else ""
        for device in board.devices:
            part = project.part(device.part_key) if device.part_key else None
            key, entry = find(
                part.order_number if part else None,
                part.type_number if part else None,
                device_key(device),
            )
            label = f"{prefix}-{device.designation.product}" if device.designation else device.id
            add(key, _Item(_device_name(device, part), "pcs", Decimal(1), [label], entry))
        for cable in board.cables:
            length = cable.length_m or pricing.cable_length_m
            key, entry = find(cable_key(cable))
            label = f"{prefix}-{cable.designation.product}" if cable.designation else cable.id
            name = (
                f"Cable {cable.cores}G{_plain(cable.cross_section_mm2)} "
                f"{cable.material} {cable.insulation}"
            )
            add(key, _Item(name, "m", length or Decimal(0), [label], entry))

    lines: list[QuotationLine] = []
    unpriced: list[str] = []
    materials = Decimal(0)
    for key, item in items.items():
        missing: Literal["price", "length"] | None = None
        if item.unit == "m" and not item.quantity:
            missing = "length"
        elif item.entry is None:
            missing = "price"
        total = None
        if item.entry is not None and missing is None:
            total = _money(item.entry.unit_price * item.quantity)
            materials += total
        else:
            unpriced.append(f"{key} (no {missing})")
        lines.append(
            QuotationLine(
                description=(item.entry.description if item.entry else "") or item.name,
                designations=item.labels,
                quantity=item.quantity,
                unit=item.unit,
                key=key,
                unit_price=item.entry.unit_price if item.entry else None,
                total=total,
                missing=missing,
            )
        )

    circuits = sum(len(board.circuits) for board in project.boards)
    boards = len(project.boards)
    labour = Decimal(0)
    for description, quantity, rate in (
        ("Wiring and testing, per circuit", Decimal(circuits), pricing.labour_per_circuit),
        ("Assembly and testing, per board", Decimal(boards), pricing.labour_per_board),
        ("Enclosure, busbars and accessories", Decimal(boards), pricing.enclosure_price),
    ):
        if rate > 0:
            total = _money(rate * quantity)
            labour += total
            lines.append(
                QuotationLine(
                    description=description,
                    designations=[],
                    quantity=quantity,
                    unit="pcs",
                    key="",
                    unit_price=rate,
                    total=total,
                )
            )
    markup = _money((materials + labour) * pricing.markup_percent / 100)
    vat = _money((materials + labour + markup) * pricing.vat_percent / 100)
    return Quotation(
        currency=pricing.currency,
        lines=lines,
        materials=_money(materials),
        labour=_money(labour),
        markup=markup,
        vat=vat,
        total=_money(materials + labour + markup + vat),
        unpriced=unpriced,
        complete=not unpriced,
    )


def read_price_list(data: bytes) -> list[PriceListEntry]:
    """Read a price list from a spreadsheet (.xlsx or .csv).

    The header row names a key column ("Key", "Order number", "Item",
    "الكود") and a price column ("Price", "Unit price", "السعر"); a
    description column is read where there is one.

    Args:
        data: The file's bytes.

    Returns:
        The prices.

    Raises:
        ValidationError: If the file has no key and price columns, or no
            row with both.
    """
    rows = read_rows(data)
    keys = ("key", "order number", "order no", "code", "item", "article", "الكود", "الرمز", "الصنف")
    prices = ("price", "unit price", "cost", "السعر", "سعر الوحده")
    descriptions = ("description", "الوصف", "البيان")

    def find(header: list[str], words: tuple[str, ...]) -> int | None:
        for index, cell in enumerate(header):
            if cell.strip().lower() in words:
                return index
        return None

    for start, header in enumerate(rows[:30]):
        key_col, price_col = find(header, keys), find(header, prices)
        if key_col is None or price_col is None:
            continue
        desc_col = find(header, descriptions)
        entries: list[PriceListEntry] = []
        for row in rows[start + 1 :]:
            if max(key_col, price_col) >= len(row):
                continue
            key = row[key_col].strip()
            raw = row[price_col].replace(",", "").strip()
            if not key or not raw:
                continue
            try:
                price = Decimal(raw)
            except ArithmeticError:
                continue
            description = (
                row[desc_col].strip() if desc_col is not None and desc_col < len(row) else ""
            )
            entries.append(PriceListEntry(key=key, description=description, unit_price=price))
        if not entries:
            raise ValidationError(
                "the price list has no row with both a key and a price", code="price_list_empty"
            )
        return entries
    raise ValidationError(
        "no header row names a key (or order number) and a price column",
        code="price_list_no_header",
    )


def quotation_csv(quotation: Quotation) -> str:
    """Write a quotation as CSV, totals last.

    Args:
        quotation: The quotation.

    Returns:
        The CSV text, with a byte-order mark for Excel.
    """
    rows = [
        [
            line.description,
            " ".join(line.designations),
            _plain(line.quantity),
            line.unit,
            _plain(line.unit_price) if line.unit_price is not None else "not priced",
            _plain(line.total) if line.total is not None else "",
            line.key,
        ]
        for line in quotation.lines
    ]
    rows += [
        [label, "", "", "", "", _plain(amount), ""]
        for label, amount in (
            ("Materials", quotation.materials),
            ("Labour and enclosure", quotation.labour),
            ("Markup", quotation.markup),
            ("VAT", quotation.vat),
            (f"Total ({quotation.currency})", quotation.total),
        )
    ]
    if not quotation.complete:
        rows.append(["Not priced: " + "; ".join(quotation.unpriced)])
    return write_csv(
        ["Description", "Designations", "Quantity", "Unit", "Unit price", "Total", "Key"], rows
    )
