"""Tests for `app/domain/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

VFD selection and the panel BOM are blocked on manufacturer guides that are
not in this repository (see the README). Until they land, what is tested is
that each one says so -- a NotImplementedYetError the API answers with 501 --
rather than an anonymous NotImplementedError that surfaced as a 500.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotImplementedYetError, ValidationError
from app.domain import calculations
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.calculations import CableSizingRequest, InstallationMethod

_USER = CurrentUser(id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER}))


@pytest.mark.parametrize(
    "calculate",
    [calculations.select_vfd, calculations.build_panel_bom],
)
def test_each_calculation_says_it_is_blocked_on_its_sources(
    calculate: Callable[..., Any],
) -> None:
    with pytest.raises(NotImplementedYetError, match="engineering guides"):
        calculate(session=object(), user=_USER, request=object())


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
