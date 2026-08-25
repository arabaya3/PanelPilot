"""Tests for `app/ai/tools/cable_sizing.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

These are signature-pinning tests, not behaviour tests. The functions are still
stubs, so each asserts the documented keyword arguments are accepted and that
the call raises ``NotImplementedError``.

**They are designed to fail the moment a function is implemented.** That is the
point: implementing ``size_conductor`` without replacing this test with a real
one against the IEC tables cited in its docstring will turn CI red. The
coverage gate on `app/ai/tools/` exists for the same reason.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import cable_sizing
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod


def test_size_conductor_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cable_sizing.size_conductor(
            design_current_a=Decimal("63"),
            installation_method=InstallationMethod.C,
            ambient_temp_c=Decimal("35"),
            grouped_circuits=2,
            conductor_material=ConductorMaterial.COPPER,
            insulation_rating_c=90,
        )


def test_voltage_drop_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cable_sizing.voltage_drop(
            current_a=Decimal("63"),
            length_m=Decimal("45"),
            cross_section_mm2=Decimal("16"),
            conductor_material=ConductorMaterial.COPPER,
            power_factor=Decimal("0.9"),
            three_phase=True,
        )


def test_derating_factor_accepts_documented_arguments_and_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cable_sizing.derating_factor(
            ambient_temp_c=Decimal("40"),
            grouped_circuits=3,
            insulation_rating_c=70,
        )


def test_every_public_function_is_keyword_only() -> None:
    """Positional args at a call site are how the wrong number reaches a formula.

    The README makes keyword-only a convention; this makes it enforceable for
    the functions whose output ends up on a drawing.
    """
    import inspect

    for name in ("size_conductor", "voltage_drop", "derating_factor"):
        signature = inspect.signature(getattr(cable_sizing, name))
        positional = [
            p.name
            for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        assert not positional, f"{name} accepts positional arguments: {positional}"
