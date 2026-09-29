"""Tests for `app/domain/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Every calculation refuses today, because its tables have no verified source
yet. What is pinned here is that each refuses by name, with a typed error, and
never with a number — a stub returning a plausible value would end up on a
drawing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import CapabilityUnavailableError, status_for
from app.domain import calculations


@pytest.mark.parametrize(
    ("function", "names"),
    [
        (calculations.size_cable, "AI-005"),
        (calculations.select_vfd, "AI-006"),
        (calculations.build_panel_bom, "AI-007"),
    ],
    ids=["cable", "vfd", "bom"],
)
def test_each_calculation_refuses_by_name(function: Any, names: str) -> None:
    with pytest.raises(CapabilityUnavailableError, match=names):
        function(session=None, user=None, request=None)


def test_the_refusal_is_a_501_not_an_opaque_500() -> None:
    assert status_for(CapabilityUnavailableError("x")) == 501
