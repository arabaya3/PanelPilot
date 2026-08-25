"""Tests for `app/ai/tools/vfd_selection.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Signature-pinning only; see the note in test_cable_sizing.py. These fail as
soon as a function is implemented, which is when a real test against the ABB
ratings tables cited in the docstrings has to replace them.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.ai.tools import vfd_selection
from app.models.schemas.calculations import DutyClass


def test_required_drive_current_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        vfd_selection.required_drive_current_a(
            motor_power_kw=Decimal("22"),
            supply_voltage_v=Decimal("400"),
            motor_efficiency=Decimal("0.94"),
            motor_power_factor=Decimal("0.86"),
            duty_class=DutyClass.NORMAL,
        )


def test_altitude_derate_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        vfd_selection.altitude_derate(altitude_m=Decimal("1500"))


def test_select_frame_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        vfd_selection.select_frame(
            required_current_a=Decimal("45"),
            supply_voltage_v=Decimal("400"),
            duty_class=DutyClass.HEAVY,
            altitude_m=Decimal("0"),
            ambient_temp_c=Decimal("40"),
        )


def test_every_public_function_is_keyword_only() -> None:
    for name in ("required_drive_current_a", "altitude_derate", "select_frame"):
        signature = inspect.signature(getattr(vfd_selection, name))
        positional = [
            p.name
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, f"{name} accepts positional arguments: {positional}"
