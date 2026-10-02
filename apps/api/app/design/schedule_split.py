"""Split groups of points into final circuits.

A description says "20 sockets"; a schedule needs circuits. How many points
share a circuit, and how much power one may carry, are company rules
(``max_points_per_circuit`` and ``max_kw_per_circuit`` in its profile), so
the split is done here, the same way every time, rather than
left to a model's judgement: 20 sockets at eight to a circuit is three
circuits of 7, 7 and 6, whoever asks.

Points are spread evenly rather than filled in order, so three circuits of
20 sockets carry 7, 7 and 6 rather than 8, 8 and 4.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas.design import CompanyProfile, LoadInput, LoadKind, SuggestedPoints

#: Kinds wired single-phase whatever a description says about them.
_ALWAYS_SINGLE = {LoadKind.SOCKET, LoadKind.LIGHTING, LoadKind.FAN, LoadKind.DATA}

#: Kinds single-phase below this power per unit: a 2-ton split unit is not a
#: three-phase load however a description is read.
_SINGLE_BELOW_KW = Decimal(5)
_SMALL_SINGLE = {LoadKind.AIR_CONDITIONING, LoadKind.WATER_HEATER, LoadKind.KITCHEN}


#: How a circuit's note says where its power came from, per language.
_SOURCE_LABELS: dict[str, tuple[str, str]] = {
    "English": ("given", "typical"),
    "Arabic": (
        "\u0645\u0646 \u0627\u0644\u0648\u0635\u0641",
        "\u0642\u064a\u0645\u0629 \u0646\u0645\u0648\u0630\u062c\u064a\u0629",
    ),
}


def _watts(kw: Decimal) -> str:
    return format((kw * 1000).normalize(), "f")


def split_points(
    items: list[SuggestedPoints],
    profile: CompanyProfile,
    *,
    supply_phases: int,
    language: str = "English",
) -> tuple[list[LoadInput], list[str]]:
    """Split groups of points into circuits under a company's rules.

    Args:
        items: The groups of points.
        profile: The company whose points-per-circuit rule applies.
        supply_phases: The board's supply; three-phase circuits only on a
            three-phase supply.
        language: The language the notes are written in ("English" or
            "Arabic"); anything else is written in English.

    Returns:
        The circuits, and one line per circuit saying how its power was
        reached ("Hall sockets 1: 7 x 150 W = 1.05 kW. typical").
    """
    given, typical = _SOURCE_LABELS.get(language, _SOURCE_LABELS["English"])
    loads: list[LoadInput] = []
    notes: list[str] = []
    for item in items:
        per_circuit = profile.max_points_per_circuit.get(item.load, 1)
        cap = profile.max_kw_per_circuit.get(item.load)
        if cap is not None:
            # The power cap can split further than the count: 40 high-bays at
            # 150 W are 6 kW, more than four 1.5 kW circuits at 15 a circuit.
            per_circuit = max(1, min(per_circuit, int(cap / item.unit_power_kw)))
        circuits = -(-item.quantity // per_circuit)
        base, extra = divmod(item.quantity, circuits)
        three = (
            item.three_phase
            and supply_phases == 3
            and item.load not in _ALWAYS_SINGLE
            and not (item.load in _SMALL_SINGLE and item.unit_power_kw < _SINGLE_BELOW_KW)
        )
        for index in range(circuits):
            points = base + (1 if index < extra else 0)
            power = (item.unit_power_kw * points).normalize()
            name = item.description if circuits == 1 else f"{item.description} {index + 1}"
            loads.append(
                LoadInput(
                    description=name[:120],
                    load=item.load,
                    power_kw=power,
                    phases=3 if three else 1,
                )
            )
            notes.append(
                f"{name}: {points} x {_watts(item.unit_power_kw)} W = "
                f"{format(power, 'f')} kW. {given if item.power_stated else typical}"
            )
    return loads, notes
