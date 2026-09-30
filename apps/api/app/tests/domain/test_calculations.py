"""Tests for `app/domain/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The arithmetic is tested against each source's worked examples in
`tests/ai/tools/`; these check that each service returns its result with
every source it came from.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.domain import calculations
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.calculations import (
    CableSizingRequest,
    DutyClass,
    EnclosureConstraints,
    EnclosurePlacement,
    InstallationMethod,
    LoadScheduleItem,
    PanelBomRequest,
    VfdSelectionRequest,
)

_USER = CurrentUser(id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER}))


#: `size_cable` does not touch the session.
_NO_SESSION = cast(Session, object())


def _cable_request(**overrides: Any) -> CableSizingRequest:
    fields: dict[str, Any] = {
        "design_current_a": Decimal("100"),
        "length_m": Decimal("50"),
        "supply_voltage_v": Decimal("400"),
        "installation_method": InstallationMethod.E,
        "ambient_temp_c": Decimal("40"),
        "grouped_circuits": 7,
        "insulation_rating_c": 70,
    }
    fields.update(overrides)
    return CableSizingRequest(**fields)


def test_a_cable_is_sized_with_its_drop_and_every_source() -> None:
    response = calculations.size_cable(session=_NO_SESSION, user=_USER, request=_cable_request())

    # ABB's worked example selects 95 mm2; Fig. G28 gives 0.42 V/A/km for
    # 95 mm2 three-phase at cos 0.8: 0.42 x 100 x 0.05 = 2.1 V, 0.525 %.
    assert response.result.cross_section_mm2 == Decimal("95")
    assert response.voltage_drop_v == Decimal("2.1")
    assert response.voltage_drop_percent == Decimal("0.525")
    assert [s.section for s in response.sources] == [
        "Table 8",
        "Table 4",
        "Table 5",
        "Chapter G, Fig. G28",
    ]


def test_a_non_positive_supply_voltage_is_refused() -> None:
    with pytest.raises(ValidationError, match="supply_voltage_v"):
        calculations.size_cable(
            session=_NO_SESSION, user=_USER, request=_cable_request(supply_voltage_v=Decimal(0))
        )


def test_a_drive_is_selected_with_the_motor_current_and_every_source() -> None:
    # 22 kW, 400 V, η 0.93, cos 0.85: 22000 / (√3 x 400 x 0.93 x 0.85) = 40.2 A.
    # Heavy duty: 061A-3 (IHd 45 A) is the first to carry it.
    response = calculations.select_vfd(
        session=_NO_SESSION,
        user=_USER,
        request=VfdSelectionRequest(
            motor_power_kw=Decimal("22"),
            supply_voltage_v=Decimal("400"),
            motor_efficiency=Decimal("0.93"),
            motor_power_factor=Decimal("0.85"),
            duty_class=DutyClass.HEAVY,
        ),
    )
    assert response.motor_current_a.quantize(Decimal("0.1")) == Decimal("40.2")
    assert response.result.frame_reference == "ACS880-01-061A-3 (R4)"
    assert [s.manufacturer for s in response.sources] == ["ABB"] * 4


def test_a_bom_is_built_with_each_source_once() -> None:
    response = calculations.build_panel_bom(
        session=_NO_SESSION,
        user=_USER,
        request=PanelBomRequest(
            loads=[
                LoadScheduleItem(
                    tag="M-1", description="pump", current_a=Decimal(20), variable_speed=True
                ),
                LoadScheduleItem(tag="M-2", description="fan", current_a=Decimal(8)),
            ],
            constraints=EnclosureConstraints(
                width_mm=800,
                height_mm=2000,
                depth_mm=600,
                ingress_rating="IP54",
                placement=EnclosurePlacement.SINGLE_WALL,
                cable_installation_method=InstallationMethod.C,
            ),
        ),
    )
    assert response.result.lines[0].part_reference == "ACS880-01-025A-3"
    # Drive ratings and its fuses (ACS880 manual), the cables (ABB handbook),
    # both cables' terminals (one Siemens page), the enclosure (Rittal).
    assert [s.manufacturer for s in response.sources] == [
        "ABB",
        "ABB",
        "ABB",
        "Siemens",
        "Rittal",
    ]
