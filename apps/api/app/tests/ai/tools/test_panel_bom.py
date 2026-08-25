"""Tests for `app/ai/tools/panel_bom.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Signature-pinning only; see the note in test_cable_sizing.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import panel_bom
from app.models.schemas.calculations import EnclosureConstraints, LoadScheduleItem


def _load() -> LoadScheduleItem:
    return LoadScheduleItem(
        tag="M-101",
        description="Conveyor drive motor",
        power_kw=Decimal("7.5"),
        current_a=Decimal("15"),
        dissipation_w=Decimal("120"),
    )


def _constraints() -> EnclosureConstraints:
    return EnclosureConstraints(
        width_mm=800,
        height_mm=2000,
        depth_mm=400,
        ingress_rating="IP54",
    )


def test_build_bom_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        panel_bom.build_bom(loads=[_load()], constraints=_constraints())


def test_enclosure_heat_load_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        panel_bom.enclosure_heat_load_w(
            items=[_load()],
            ambient_temp_c=Decimal("35"),
            max_internal_temp_c=Decimal("50"),
        )


def test_enclosure_constraints_defaults_are_conservative() -> None:
    """A real assertion, not a stub: these defaults feed a cooling calculation.

    Silently defaulting max internal temperature at or below ambient would make
    the heat-load result meaningless, so the gap is asserted here.
    """
    constraints = _constraints()
    assert constraints.max_internal_temp_c > constraints.ambient_temp_c
