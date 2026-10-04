"""Tests for `app/design/markets.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.design import markets
from app.models.schemas.design import LoadKind

_DEFAULT_DROPS = {LoadKind.LIGHTING: Decimal(3)}


def test_market_looks_up_any_case_or_refuses() -> None:
    assert markets.market("ps").name == "Palestine"
    assert markets.market(" SA ").frequency_hz == 60
    with pytest.raises(ValidationError) as caught:
        markets.market("XX")
    assert caught.value.code == "market_unknown"


def test_every_market_has_a_three_phase_supply_and_a_currency() -> None:
    for found in markets.MARKETS.values():
        assert found.voltage_v in (380, 400, 415)
        assert found.frequency_hz in (50, 60)
        assert len(found.currency) == 3
        assert found.languages


def test_israel_asks_an_rcd_on_every_final_circuit_and_3_percent() -> None:
    settings = markets.profile_settings("IL", _DEFAULT_DROPS)
    assert settings["default_max_voltage_drop_percent"] == "3"
    rules = settings["circuit_rules"]
    assert isinstance(rules, dict)
    assert rules["data"] == {"residual_current_ma": "30"}
    assert "motor" not in rules


def test_a_general_limit_never_relaxes_a_lower_one() -> None:
    settings = markets.profile_settings("SA", _DEFAULT_DROPS)
    assert settings["default_max_voltage_drop_percent"] == "4"
    assert settings["max_voltage_drop_percent"] == {"lighting": "3"}
    tighter = markets.profile_settings("KW", _DEFAULT_DROPS)
    assert tighter["max_voltage_drop_percent"] == {"lighting": "2.5"}


def test_a_market_without_stated_limits_sets_nothing() -> None:
    assert markets.profile_settings("PS", _DEFAULT_DROPS) == {}
