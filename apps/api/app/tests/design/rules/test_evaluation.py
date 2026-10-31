"""Tests for ``app/design/rules/evaluation.py``: reproducible, typed, draft kept apart.

Synthetic, inert rules only (``synthetic.py``).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from typing import Any

import pytest

from app.design.rules import evaluation, registry
from app.tests.design.rules.synthetic import EVALUATORS, echo_rule

INPUTS = {"amount": {"value": "1.50", "unit": "synthetic-unit"}}


def _draft() -> evaluation.DraftEvaluation:
    result = evaluation.evaluate(
        echo_rule(), INPUTS, status="draft", mode="draft", evaluators=EVALUATORS
    )
    assert isinstance(result, evaluation.DraftEvaluation)
    return result


def test_no_evaluator_is_shipped() -> None:
    assert evaluation.shipped_evaluators().get("synthetic.echo", "1") is None


def test_the_same_inputs_give_the_same_output_digest() -> None:
    first, second = _draft(), _draft()
    assert first.record == second.record
    assert first.outputs == {"echoed": {"value": "1.5", "unit": "synthetic-unit"}}
    # Equal decimals in another spelling are the same canonical input.
    other = evaluation.evaluate(
        echo_rule(),
        {"amount": {"value": "1.5000", "unit": "synthetic-unit"}},
        status="draft",
        mode="draft",
        evaluators=EVALUATORS,
    )
    assert other.record == first.record


_SCRIPT = """
from app.design.rules import evaluation
from app.tests.design.rules.synthetic import EVALUATORS, echo_rule
r = evaluation.evaluate(echo_rule(), {"amount": {"value": "1.50", "unit": "synthetic-unit"}},
                        status="draft", mode="draft", evaluators=EVALUATORS)
print(r.record.rule_digest, r.record.input_digest, r.record.output_digest)
"""


def test_the_output_digest_is_the_same_across_runs() -> None:
    """A fresh interpreter (another hash seed) gives the same digests."""
    here = _draft().record
    runs = {
        subprocess.run(
            [sys.executable, "-c", _SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": str(seed), "PYTHONPATH": ":".join(sys.path)},
        ).stdout.strip()
        for seed in (1, 2)
    }
    assert runs == {f"{here.rule_digest} {here.input_digest} {here.output_digest}"}


def test_a_record_reproduces() -> None:
    rules = registry.Registry([echo_rule()])
    record = _draft().record
    assert evaluation.reproduce(record, INPUTS, rules=rules, evaluators=EVALUATORS)
    other = {"amount": {"value": "2", "unit": "synthetic-unit"}}
    assert not evaluation.reproduce(record, other, rules=rules, evaluators=EVALUATORS)
    changed = registry.Registry([echo_rule(title="Synthetic echo, changed")])
    assert not evaluation.reproduce(record, INPUTS, rules=changed, evaluators=EVALUATORS)
    assert not evaluation.reproduce(
        record, INPUTS, rules=registry.Registry(), evaluators=EVALUATORS
    )


@pytest.mark.parametrize("status", ["draft", "unreviewed"])
def test_a_rule_not_approved_is_evaluated_only_as_a_draft(status: str) -> None:
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        evaluation.evaluate(
            echo_rule(), INPUTS, status=status, mode="reviewed", evaluators=EVALUATORS
        )
    assert refused.value.code == "rule_not_approved"
    draft = evaluation.evaluate(
        echo_rule(), INPUTS, status=status, mode="draft", evaluators=EVALUATORS
    )
    assert isinstance(draft, evaluation.DraftEvaluation)


def test_a_draft_result_is_labelled_flagged_and_never_a_design_value() -> None:
    draft = _draft()
    assert draft.label == evaluation.DRAFT_LABEL
    assert set(draft.flags) == {"draft_evaluation", "not_engineering_guidance"}
    assert draft.describe().startswith("DRAFT EVALUATION")
    assert "approved" not in draft.describe()
    assert draft.record.mode == "draft"
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        draft.as_design_value()
    assert refused.value.code == "draft_not_storable"


def test_a_draft_result_has_no_way_into_a_stored_design() -> None:
    """A stored v2 design has no place for rule outputs: one written in is unreadable."""
    import copy

    from app.design.model import versions
    from app.design.model.read import read_snapshot
    from app.domain import design_configuration
    from app.tests.design.model.v2_support import config_case

    v1, configuration = config_case("pe_n_distribution")
    stored = design_configuration.compose_snapshot(v1, configuration)
    read_snapshot(stored)
    for where in ("model", None):
        altered = copy.deepcopy(stored)
        target = altered["model"] if where else altered
        target["rule_evaluations"] = [_draft().record.as_json()]
        with pytest.raises(versions.SnapshotUnreadableError):
            read_snapshot(altered)


@pytest.mark.parametrize("status", ["rejected", "withdrawn"])
def test_a_rejected_or_withdrawn_rule_is_not_evaluated(status: str) -> None:
    modes: tuple[evaluation.Mode, ...] = ("draft", "reviewed")
    for mode in modes:
        with pytest.raises(evaluation.EvaluationRefusedError) as refused:
            evaluation.evaluate(
                echo_rule(), INPUTS, status=status, mode=mode, evaluators=EVALUATORS
            )
        assert refused.value.code == "rule_not_evaluable"


def test_an_approved_rule_is_evaluated_in_reviewed_mode() -> None:
    """Synthetic: 'approved' stands in for a server-recorded decision."""
    result = evaluation.evaluate(
        echo_rule(), INPUTS, status="approved", mode="reviewed", evaluators=EVALUATORS
    )
    assert isinstance(result, evaluation.Evaluation)
    assert result.record.rule_status == "approved"
    assert result.record.output_digest == _draft().record.output_digest


@pytest.mark.parametrize(
    ("inputs", "code"),
    [
        ({}, "evaluation_ports_mismatch"),
        ({**INPUTS, "extra": {"value": "1", "unit": None}}, "evaluation_ports_mismatch"),
        ({"amount": {"value": "1", "unit": "other"}}, "evaluation_unit_mismatch"),
        ({"amount": {"value": 1.5, "unit": "synthetic-unit"}}, "evaluation_value_invalid"),
        ({"amount": {"value": "x", "unit": "synthetic-unit"}}, "evaluation_value_invalid"),
        ({"amount": {"value": "NaN", "unit": "synthetic-unit"}}, "evaluation_value_invalid"),
        ({"amount": {"value": True, "unit": "synthetic-unit"}}, "evaluation_value_invalid"),
        ({"amount": "1"}, "evaluation_value_invalid"),
    ],
)
def test_inputs_are_typed_strictly(inputs: Mapping[str, Any], code: str) -> None:
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        evaluation.evaluate(
            echo_rule(), inputs, status="draft", mode="draft", evaluators=EVALUATORS
        )
    assert refused.value.code == code


def test_outputs_are_checked_against_the_rule() -> None:
    def wrong(_: Mapping[str, evaluation.Value]) -> Mapping[str, Any]:
        return {"echoed": {"value": "1", "unit": "other"}}

    evaluators = evaluation.Evaluators((evaluation.Evaluator("synthetic.echo", "1", wrong),))
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        evaluation.evaluate(
            echo_rule(), INPUTS, status="draft", mode="draft", evaluators=evaluators
        )
    assert refused.value.code == "evaluation_unit_mismatch"


def test_an_unknown_evaluator_or_mode_is_refused() -> None:
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        evaluation.evaluate(
            echo_rule(evaluator_version="9"),
            INPUTS,
            status="draft",
            mode="draft",
            evaluators=EVALUATORS,
        )
    assert refused.value.code == "evaluator_unknown"
    with pytest.raises(evaluation.EvaluationRefusedError) as refused:
        evaluation.evaluate(
            echo_rule(), INPUTS, status="draft", mode="final", evaluators=EVALUATORS  # type: ignore[arg-type]
        )
    assert refused.value.code == "evaluation_mode_unknown"
    with pytest.raises(ValueError, match="registered already"):
        evaluation.Evaluators(
            (EVALUATORS.get("synthetic.echo", "1"),) * 2  # type: ignore[arg-type]
        )
