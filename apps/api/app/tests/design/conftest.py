"""A designed, designated hall board shared by the export tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.design import designations, distribution, profile
from app.models.schemas.design import (
    DesignProject,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    Part,
    ProjectInfo,
    Revision,
    Supply,
)


@pytest.fixture
def hall_project() -> DesignProject:
    loads = [
        LoadInput(description=f"Sockets {i}", load=LoadKind.SOCKET, power_kw=Decimal("1.5"))
        for i in range(1, 7)
    ]
    loads += [
        LoadInput(description=f"Lighting {i}", load=LoadKind.LIGHTING, power_kw=Decimal("0.6"))
        for i in range(1, 5)
    ]
    loads.append(LoadInput(description="Café, east wall", load=LoadKind.DATA, power_kw=Decimal(1)))
    board = distribution.design_distribution_board(
        DistributionBoardRequest(
            name="DBG-HALL", location="HALL", loads=loads, supply=Supply(fault_level_ka=Decimal(10))
        ),
        profile.default_profile(),
    )
    # One device given a real article, so the part columns are exercised.
    board.devices[0].part_key = "incomer"
    project = DesignProject(
        info=ProjectInfo(
            name="Pocket", number="J-1", revisions=[Revision(index="01", date="2026-10-02")]
        ),
        boards=[board],
        parts=[
            Part(
                key="incomer",
                manufacturer="ETEK",
                type_number="EKM6-63X-3C63",
                order_number="EKM6-63X-3C63",
                description="MCB 3P C63",
            )
        ],
    )
    return designations.designate_project(project, profile.default_profile())
