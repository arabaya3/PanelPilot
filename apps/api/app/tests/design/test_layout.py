"""Tests for `app/design/layout.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import distribution, layout, profile
from app.models.schemas.design import (
    Device,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
)


def _board() -> distribution.Board:  # type: ignore[name-defined]
    loads = [
        LoadInput(description=f"Sockets {i}", load=LoadKind.SOCKET, power_kw=Decimal("1.5"))
        for i in range(3)
    ]
    loads.append(LoadInput(description="Data", load=LoadKind.DATA, power_kw=Decimal(1)))
    return distribution.design_distribution_board(
        DistributionBoardRequest(name="DB", loads=loads), profile.default_profile()
    )


def test_width_is_the_kinds_width_a_pole() -> None:
    company = profile.default_profile()
    breaker = Device(id="q", kind=DeviceKind.CIRCUIT_BREAKER, poles=3, rated_current_a=Decimal(16))
    assert layout.width(breaker, company) == Decimal("52.5")
    # Beyond the miniature range, a selected article, or an unsourced kind: none.
    big = breaker.model_copy(update={"rated_current_a": Decimal(80)})
    assert layout.width(big, company) is None
    starter = breaker.model_copy(update={"part_key": "ABB/MS132-16"})
    assert layout.width(starter, company) is None
    rcd = Device(id="f", kind=DeviceKind.RESIDUAL_CURRENT_DEVICE, poles=4)
    assert layout.width(rcd, company) is None
    sourced = company.model_copy(
        update={"rail_widths_mm": {DeviceKind.RESIDUAL_CURRENT_DEVICE: Decimal("17.5")}}
    )
    assert layout.width(rcd, sourced) == 70
    moulded = breaker.model_copy(
        update={"part_key": "ABB/XT3N 250 TMD 160", "rated_current_a": Decimal(160), "poles": 4}
    )
    assert layout.width(moulded, company) == 140


def test_rails_follow_the_single_line() -> None:
    rows = layout.rails(_board(), profile.default_profile())
    assert [r.name.split(":")[0] for r in rows] == ["incomer", "group", "busbar", "terminals"]
    group = rows[1]
    # The group breaker, its RCCB (no width given), then three socket breakers.
    assert len(group.slots) == 5
    assert group.unknown == 1
    assert rows[-1].unknown == len(rows[-1].slots)


def test_a_row_longer_than_the_rail_continues() -> None:
    company = profile.default_profile().model_copy(update={"usable_rail_mm": Decimal(40)})
    rows = layout.rails(_board(), company)
    assert any(r.name.endswith(":continued") for r in rows)
    for row in rows:
        widths = [s.width_mm for s in row.slots if s.width_mm is not None]
        assert len(row.slots) == 1 or sum(widths, Decimal(0)) <= 40
