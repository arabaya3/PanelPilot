"""Tests for `app/design/profile.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.design import profile
from app.models.schemas.design import DeviceKind, LoadKind


def test_the_default_is_unconfirmed_and_complete() -> None:
    default = profile.default_profile()
    assert default.key == profile.DEFAULT_PROFILE_KEY
    assert default.rules_confirmed_by == ""
    assert set(default.letters) == set(DeviceKind)
    assert set(default.circuit_rules) == set(LoadKind)


def test_default_profile_is_a_copy() -> None:
    first = profile.default_profile()
    first.letters[DeviceKind.CIRCUIT_BREAKER] = "Z"
    assert profile.default_profile().letters[DeviceKind.CIRCUIT_BREAKER] == "Q"


def test_a_company_states_only_what_differs() -> None:
    company = profile.load_profile(
        {
            "key": "acme",
            "name": "Acme Panels",
            "letters": {"residual_current_device": "Q"},
            "circuit_rules": {"lighting": {"breaker_a": "6"}},
            "rules_confirmed_by": "Eng. A",
        }
    )
    assert company.name == "Acme Panels"
    assert company.letters[DeviceKind.RESIDUAL_CURRENT_DEVICE] == "Q"
    # The rest of the letters, and the rest of the lighting rule, are kept.
    assert company.letters[DeviceKind.TERMINAL_STRIP] == "X"
    assert company.circuit_rules[LoadKind.LIGHTING].breaker_a == Decimal(6)
    assert company.circuit_rules[LoadKind.LIGHTING].residual_current_ma == Decimal(30)
    assert company.circuit_rules[LoadKind.SOCKET].breaker_a == Decimal(16)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "needs a key"),
        ({"key": " "}, "needs a key"),
        ({"key": "x", "colour": "red"}, "unknown profile settings"),
        ({"key": "x", "max_circuits_per_rcd": 0}, "max_circuits_per_rcd"),
        ({"key": "x", "letters": {"fuse": None}}, "letters"),
    ],
)
def test_a_bad_setting_is_named(data: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        profile.load_profile(data)


def test_a_market_lays_its_rules_under_the_companys() -> None:
    israel = profile.load_profile({"key": "acme", "market": "il"})
    assert israel.market == "IL"
    assert israel.default_max_voltage_drop_percent == 3
    assert israel.circuit_rules[LoadKind.DATA].residual_current_ma == 30
    # The company's own setting still wins over its market's.
    own = profile.load_profile(
        {"key": "acme", "market": "IL", "default_max_voltage_drop_percent": "2"}
    )
    assert own.default_max_voltage_drop_percent == 2


def test_an_unknown_market_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        profile.load_profile({"key": "acme", "market": "ZZ"})
    assert caught.value.code == "market_unknown"


def test_an_unknown_nested_setting_is_refused_by_its_path() -> None:
    with pytest.raises(ValidationError) as caught:
        profile.load_profile(
            {"key": "acme", "circuit_rules": {"lighting": {"breaker_amps": 6, "breaker_a": 6}}}
        )
    assert caught.value.code == "profile_unknown_settings"
    assert "circuit_rules.lighting.breaker_amps" in caught.value.params["settings"]
    assert "circuit_rules.lighting.breaker_amps" in str(caught.value)


def test_every_stored_shape_of_the_default_still_loads() -> None:
    """The default, in full as stored, and every market laid over it, load strictly."""
    from app.design import markets

    full = profile.default_profile().model_dump(mode="json")
    assert profile.load_profile(full) == profile.default_profile()
    for code in markets.MARKETS:
        assert profile.load_profile({"key": "acme", "market": code}).market == code
