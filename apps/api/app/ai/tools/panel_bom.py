"""Control panel bill-of-materials generation.

Pure functions. Each formula cites the manufacturer guide it came from.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas.calculations import (
    EnclosureConstraints,
    LoadScheduleItem,
    PanelBomResult,
)


def build_bom(
    *,
    loads: list[LoadScheduleItem],
    constraints: EnclosureConstraints,
) -> PanelBomResult:
    """Expand a load schedule into an itemised panel bill of materials.

    Each load contributes its protective device, contactor or drive, terminals,
    and wiring; shared items (busbar, control transformer, enclosure) are sized
    from the aggregate.

    Source:
        Rittal Handbook 36, §2 "Configuring enclosures" (mounting-space and
        component-pitch rules); protective-device coordination per Schneider
        Electric Electrical Installation Guide 2024, §H.

    Args:
        loads: The panel's load schedule.
        constraints: Enclosure size, ingress rating, and vendor preferences.

    Returns:
        The BOM with quantities, part references, and per-item sources.

    Raises:
        ValidationError: If the schedule is internally inconsistent or the
            required components cannot fit the stated enclosure.
    """
    raise NotImplementedError


def enclosure_heat_load_w(
    *,
    items: list[LoadScheduleItem],
    ambient_temp_c: Decimal,
    max_internal_temp_c: Decimal,
) -> Decimal:
    """Compute the heat the enclosure must dissipate, in watts.

    Sums component dissipation and subtracts passive surface loss over the
    effective enclosure area.

    Source:
        Rittal Handbook 36, §5 "Climate control", the effective-surface-area
        method per IEC 60890.

    Args:
        items: Components mounted in the enclosure with their dissipation.
        ambient_temp_c: Ambient temperature outside the enclosure, in °C.
        max_internal_temp_c: Permitted internal temperature, in °C.

    Returns:
        Required cooling capacity in watts; zero if passive loss suffices.

    Raises:
        ValidationError: If ``max_internal_temp_c`` is at or below ambient.
    """
    raise NotImplementedError
