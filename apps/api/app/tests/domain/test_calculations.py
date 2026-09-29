"""Tests for `app/domain/calculations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The three calculations are blocked on manufacturer guides that are not in this
repository (see the README). Until they land, what is tested is that each one
says so — a NotImplementedYetError the API answers with 501 — rather than an
anonymous NotImplementedError that surfaced as a 500.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.core.errors import NotImplementedYetError
from app.domain import calculations
from app.models.schemas.auth import CurrentUser, Role

_USER = CurrentUser(id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER}))


@pytest.mark.parametrize(
    "calculate",
    [calculations.size_cable, calculations.select_vfd, calculations.build_panel_bom],
)
def test_each_calculation_says_it_is_blocked_on_its_sources(
    calculate: Callable[..., Any],
) -> None:
    with pytest.raises(NotImplementedYetError, match="engineering guides"):
        calculate(session=object(), user=_USER, request=object())
