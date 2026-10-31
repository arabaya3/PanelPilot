"""Synthetic, inert rules for the registry tests.

Nothing here is engineering content: the scope is ``synthetic``, the names
say so, and the evaluators only copy or count what they are given. The
"units" are made-up tokens, not physical units.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.design.rules import evaluation, registry

PROVENANCE = registry.Provenance(
    author=None,
    created=None,
    sources=(),
    statement="Synthetic test rule; no engineering meaning; never shipped.",
)


def echo_rule(**overrides: Any) -> registry.RuleVersion:
    """``synthetic/echo/v1``: copies its input to its output."""
    fields: dict[str, Any] = {
        "ref": "synthetic/echo/v1",
        "title": "Synthetic echo",
        "evaluator_id": "synthetic.echo",
        "evaluator_version": "1",
        "inputs": (registry.Port("amount", "decimal", "synthetic-unit"),),
        "outputs": (registry.Port("echoed", "decimal", "synthetic-unit"),),
        "applicability": (registry.Predicate("kind", "eq", "synthetic"),),
        "provenance": PROVENANCE,
        "declared_status": "draft",
        "synthetic": True,
    }
    fields.update(overrides)
    return registry.RuleVersion(**fields)


def _echo(values: Mapping[str, evaluation.Value]) -> Mapping[str, Any]:
    return {"echoed": {"value": values["amount"], "unit": "synthetic-unit"}}


EVALUATORS = evaluation.Evaluators((evaluation.Evaluator("synthetic.echo", "1", _echo),))
