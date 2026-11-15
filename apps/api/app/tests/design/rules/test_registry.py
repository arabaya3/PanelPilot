"""Tests for ``app/design/rules/registry.py``: refs, digests, applicability, status.

Synthetic, inert rules only (``synthetic.py``).
"""

from __future__ import annotations

import dataclasses

import pytest

from app.design.rules import registry
from app.tests.design.rules.synthetic import PROVENANCE, echo_rule


def test_nothing_is_shipped() -> None:
    assert registry.SHIPPED == ()
    assert registry.shipped().refs() == []


def test_a_shipped_rule_could_never_be_synthetic_or_approved() -> None:
    for rule in registry.SHIPPED:  # pragma: no cover - empty in M3
        assert not rule.synthetic
        assert rule.declared_status in registry.DECLARED_STATUSES


@pytest.mark.parametrize(
    "ref",
    [
        "synthetic/echo@v1",
        "synthetic/echo/1",
        "synthetic/echo/v0",
        "Synthetic/echo/v1",
        "synthetic//v1",
        "synthetic/echo/v1/x",
    ],
)
def test_a_malformed_ref_is_refused(ref: str) -> None:
    with pytest.raises(registry.RuleDefinitionError):
        echo_rule(ref=ref)


@pytest.mark.parametrize("status", ["approved", "rejected", "withdrawn", "in_review", ""])
def test_a_rule_cannot_declare_a_decision(status: str) -> None:
    """Approval is recorded on the server; code declares draft or unreviewed only."""
    with pytest.raises(registry.RuleDefinitionError):
        echo_rule(declared_status=status)


def test_synthetic_scope_and_flag_go_together() -> None:
    with pytest.raises(registry.RuleDefinitionError):
        echo_rule(synthetic=False)
    with pytest.raises(registry.RuleDefinitionError):
        echo_rule(ref="panelpilot/echo/v1")


@pytest.mark.parametrize(
    "change",
    [
        {"inputs": (registry.Port("a", "decimal"), registry.Port("a", "decimal"))},
        {"inputs": (registry.Port("Bad", "decimal"),)},
        {"inputs": (registry.Port("a", "float"),)},
        {"inputs": (registry.Port("a", "boolean", "x"),)},
        {"inputs": (registry.Port("a", "decimal", " "),)},
        {"outputs": ()},
        {"applicability": (registry.Predicate("a..b", "eq", 1),)},
        {"applicability": (registry.Predicate("a", "gt", 1),)},
        {"applicability": (registry.Predicate("a", "in", [1]),)},
        {"applicability": (registry.Predicate("a", "eq", 1.5),)},
        {"applicability": (registry.Predicate("a", "present", 1),)},
        {"evaluator_id": "Not An Id"},
        {"evaluator_version": " "},
        {"title": " "},
        {"provenance": dataclasses.replace(PROVENANCE, statement=" ")},
    ],
)
def test_a_malformed_rule_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(registry.RuleDefinitionError):
        echo_rule(**change)


def test_the_digest_is_stable_and_covers_every_field() -> None:
    rule = echo_rule()
    assert rule.digest == echo_rule().digest
    assert registry.is_digest(rule.digest)
    # Port order does not matter; port content does.
    two = (registry.Port("a", "decimal", "u"), registry.Port("b", "integer"))
    assert echo_rule(inputs=two).digest == echo_rule(inputs=tuple(reversed(two))).digest
    changed = [
        echo_rule(title="Synthetic echo 2"),
        echo_rule(evaluator_version="2"),
        echo_rule(inputs=(registry.Port("amount", "decimal", "other-unit"),)),
        echo_rule(applicability=()),
        echo_rule(declared_status="unreviewed"),
        echo_rule(provenance=dataclasses.replace(PROVENANCE, sources=("synthetic-doc-1",))),
        echo_rule(ref="synthetic/echo/v2"),
    ]
    digests = {rule.digest, *(c.digest for c in changed)}
    assert len(digests) == len(changed) + 1


def test_absent_provenance_stays_absent() -> None:
    dumped = registry.dump(echo_rule())
    assert dumped["provenance"] == {
        "author": None,
        "created": None,
        "sources": [],
        "statement": PROVENANCE.statement,
    }


def test_applicability_reads_model_data_only() -> None:
    rule = echo_rule(
        applicability=(
            registry.Predicate("kind", "eq", "synthetic"),
            registry.Predicate("board.count", "in", (1, 2)),
            registry.Predicate("board.flag", "absent"),
            registry.Predicate("board.name", "present"),
            registry.Predicate("board.level", "ne", True),
        )
    )
    board: dict[str, object] = {"count": 2, "name": "x", "level": 1}
    data: dict[str, object] = {"kind": "synthetic", "board": board}
    assert registry.applies(rule, data)
    assert not registry.applies(rule, {**data, "kind": "other"})
    assert not registry.applies(rule, {**data, "board": {**board, "count": 3}})
    assert not registry.applies(rule, {**data, "board": {**board, "flag": False}})
    assert not registry.applies(rule, {"kind": "synthetic", "board": [1]})
    # True is not 1.
    one = echo_rule(applicability=(registry.Predicate("n", "eq", 1),))
    assert registry.applies(one, {"n": 1})
    assert not registry.applies(one, {"n": True})
    assert registry.applies(echo_rule(applicability=()), {})


def test_effective_status_comes_from_a_decision_only() -> None:
    rule = echo_rule()
    assert registry.effective_status(rule, None) == "draft"
    for decision in registry.DECISIONS:
        assert registry.effective_status(rule, decision) == decision
    with pytest.raises(ValueError, match="unknown decision"):
        registry.effective_status(rule, "in_review")


def test_a_ref_is_registered_once() -> None:
    reg = registry.Registry([echo_rule()])
    assert reg.get("synthetic/echo/v1") is not None
    assert reg.get("synthetic/echo/v2") is None
    with pytest.raises(registry.RuleDefinitionError):
        reg.add(echo_rule())
