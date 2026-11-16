"""M9a: the composer checks every configured pattern, bound or not (SYNTHETIC data).

docs/architecture/m9a-guided-patterns.md §4. Before M9a a pattern's slot
templates and membership were checked only when a binding applied it.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from app.design.model import compose
from app.tests.design.model.v2_support import config_case


def _unbound() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The feeder case with its pattern copied as a second, unbound pattern."""
    v1, configuration = config_case("feeder_1p")
    restricted = copy.deepcopy(configuration["templates"][0])
    restricted["ref"] = "global/synth-restricted/v1"
    restricted["restricted_scope"] = ["fire_pump"]
    configuration["templates"].append(restricted)
    extra = copy.deepcopy(configuration["patterns"][0])
    extra["ref"] = "tenant/synth-unbound/v1"
    configuration["patterns"].append(extra)
    return v1, configuration, extra


def _unknown_template(p: dict[str, Any]) -> None:
    p["slots"][0]["template"] = "tenant/not-held/v1"


def _terminal_not_on_template(p: dict[str, Any]) -> None:
    p["nets"][0]["members"][0]["terminal"] = "no-such-terminal"


def _member_twice_in_a_net(p: dict[str, Any]) -> None:
    net = p["nets"][0]
    net["members"].append(copy.deepcopy(net["members"][0]))


def _member_in_two_nets(p: dict[str, Any]) -> None:
    # The line net is binding-scoped: a neutral-net member cannot also be in it.
    line = next(n for n in p["nets"] if n["role"] == "line")
    neutral = next(n for n in p["nets"] if n["role"] == "neutral")
    line["members"].append(copy.deepcopy(neutral["members"][0]))


CASES: dict[str, tuple[Callable[[dict[str, Any]], None], str, set[str]]] = {
    "unknown_template": (_unknown_template, "unknown_template", {"pattern", "slot", "ref"}),
    "terminal_not_on_template": (
        _terminal_not_on_template,
        "terminal_not_on_template",
        {"pattern", "role", "slot", "template", "terminal"},
    ),
    "restricted_template": (
        lambda p: p["slots"].__setitem__(
            0, {**p["slots"][0], "template": "global/synth-restricted/v1"}
        ),
        "restricted_scope",
        {"pattern", "slot", "ref", "scope"},
    ),
    "member_in_two_nets": (_member_in_two_nets, "contradictory_nets", {"pattern", "role"}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_unbound_pattern_is_checked_with_the_binding_codes(case: str) -> None:
    mutate, code, params = CASES[case]
    v1, configuration, extra = _unbound()
    mutate(extra)
    with pytest.raises(compose.CompositionRefusedError) as refused:
        compose.compose(v1, configuration)
    assert refused.value.code == code
    assert set(refused.value.params) == params
    assert refused.value.params["pattern"] == "tenant/synth-unbound/v1"


def test_a_consistent_unbound_pattern_composes_and_is_not_embedded() -> None:
    v1, configuration, _ = _unbound()
    model = compose.compose(v1, configuration)
    assert [p["ref"] for p in model["patterns"]] == ["global/synth-feeder-1p/v1"]


def test_two_board_scope_nets_with_one_signal_may_share_a_member() -> None:
    v1, configuration, extra = _unbound()
    neutral = next(n for n in extra["nets"] if n["role"] == "neutral")
    assert neutral["scope"] == "board"
    twin = {**copy.deepcopy(neutral), "role": "neutral-b", "connections": []}
    twin["members"] = [copy.deepcopy(neutral["members"][0])]
    extra["nets"].append(twin)
    compose.compose(v1, configuration)  # one net per board and signal: not contradictory


def test_a_slot_without_a_template_is_left_to_the_binding() -> None:
    v1, configuration, extra = _unbound()
    extra["slots"][0]["template"] = None
    extra["nets"][0]["members"][0]["terminal"] = "typed-by-the-user"
    compose.compose(v1, configuration)


@pytest.mark.parametrize("bound", [True, False])
def test_a_member_repeated_in_one_net_is_accepted_as_a_binding_always_did(bound: bool) -> None:
    # Regression (independent review): apply() has always skipped a repeated member,
    # so a stored configuration with one must still compose, bound or not.
    v1, configuration, extra = _unbound()
    target = configuration["patterns"][0] if bound else extra
    _member_twice_in_a_net(target)
    compose.compose(v1, configuration)
