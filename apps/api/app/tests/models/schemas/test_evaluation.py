"""Tests for `app/models/schemas/evaluation.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The entry validators exist to stop an eval set from containing entries that
pass no matter what the pipeline does. That is the failure mode with no
symptom: the pass rate goes up, the set gets weaker, and nothing looks wrong.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.evaluation import (
    EvalCategory,
    EvalEntry,
    EvalResult,
    EvalRun,
    ExpectedCitation,
    FailureMode,
)

_CITATION = ExpectedCitation(document_id="abb-acs880-fw", page=88)


def _entry(**overrides: object) -> EvalEntry:
    payload: dict[str, object] = {
        "id": "e1",
        "query": "Why does the drive trip on acceleration?",
        "category": EvalCategory.STRAIGHTFORWARD,
        "expected_answer_summary": "Ramp too short.",
        "required_phrases": ["acceleration time"],
        "expected_citation": _CITATION,
    }
    payload.update(overrides)
    return EvalEntry.model_validate(payload)


# --- an entry must actually assert something -------------------------------


def test_an_answerable_entry_must_name_its_citation() -> None:
    """Without one, a right-sounding answer from the wrong manual passes."""
    with pytest.raises(ValidationError, match="must name its expected citation"):
        _entry(expected_citation=None)


def test_an_answerable_entry_must_require_a_phrase() -> None:
    """The most dangerous thing an eval set can contain.

    An entry with no required phrase passes regardless of what the pipeline
    returns, inflating the pass rate while testing nothing.
    """
    with pytest.raises(ValidationError, match="passes regardless"):
        _entry(required_phrases=[])


def test_duplicate_required_phrases_are_refused() -> None:
    """One phrase counted twice overstates how much the entry checks."""
    with pytest.raises(ValidationError, match="duplicate required phrases"):
        _entry(required_phrases=["ramp", "ramp"])


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_query_is_refused(blank: str) -> None:
    """An entry that asks nothing silently tests nothing."""
    with pytest.raises(ValidationError):
        _entry(query=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_required_phrase_is_refused(blank: str) -> None:
    """An empty phrase is a substring of every answer, so it always passes."""
    with pytest.raises(ValidationError):
        _entry(required_phrases=[blank])


def test_a_blank_phrase_alongside_a_real_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        _entry(required_phrases=["acceleration time", "  "])


def test_required_phrases_are_stripped() -> None:
    """So a stray space does not make a phrase unmatchable."""
    assert _entry(required_phrases=[" acceleration time "]).required_phrases == [
        "acceleration time"
    ]


# --- out-of-scope entries are the mirror image -----------------------------


def _out_of_scope(**overrides: object) -> EvalEntry:
    payload: dict[str, object] = {
        "id": "oos",
        "query": "Torque spec for a Corolla head bolt?",
        "category": EvalCategory.OUT_OF_SCOPE,
        "expected_answer_summary": "Not in the corpus.",
    }
    payload.update(overrides)
    return EvalEntry.model_validate(payload)


def test_an_out_of_scope_entry_needs_neither_citation_nor_phrases() -> None:
    """The correct behaviour is no answer at all, so there is nothing to assert."""
    entry = _out_of_scope()
    assert entry.expected_citation is None
    assert entry.required_phrases == []


def test_an_out_of_scope_entry_cannot_expect_a_citation() -> None:
    """Its category and content would disagree about what the runner checks."""
    with pytest.raises(ValidationError, match="must not expect a citation"):
        _out_of_scope(expected_citation=_CITATION)


def test_an_out_of_scope_entry_cannot_require_phrases() -> None:
    with pytest.raises(ValidationError, match="must not require answer phrases"):
        _out_of_scope(required_phrases=["anything"])


def test_the_categories_cover_hard_queries_not_just_easy_ones() -> None:
    """Regressions hide in the ambiguous and adversarial cases.

    Pinned so the set cannot quietly become all-straightforward, which would
    read as healthy while the cases that matter go untested.
    """
    assert {c.value for c in EvalCategory} == {
        "straightforward",
        "ambiguous",
        "edge_case",
        "out_of_scope",
        "regression",
    }


# --- citations -------------------------------------------------------------


def test_a_citation_page_is_one_indexed() -> None:
    with pytest.raises(ValidationError):
        ExpectedCitation(document_id="doc", page=0)


def test_a_citation_needs_a_document() -> None:
    with pytest.raises(ValidationError):
        ExpectedCitation(document_id="   ")


# --- results ---------------------------------------------------------------


def test_a_failing_result_must_say_how_it_failed() -> None:
    """A failure with no mode cannot be triaged."""
    with pytest.raises(ValidationError, match="must say how it failed"):
        EvalResult(entry_id="e1", passed=False)


def test_a_passing_result_cannot_carry_a_failure() -> None:
    """A pass carrying a failure mode cannot be trusted either way."""
    with pytest.raises(ValidationError, match="must not carry a failure mode"):
        EvalResult(entry_id="e1", passed=True, failure=FailureMode.WRONG_ANSWER)


# --- run summary -----------------------------------------------------------


def _result(entry_id: str, *, passed: bool, failure: FailureMode | None = None) -> EvalResult:
    return EvalResult(entry_id=entry_id, passed=passed, failure=failure)


def test_a_run_needs_at_least_one_result() -> None:
    """An empty run would report a perfect pass rate over nothing."""
    with pytest.raises(ValidationError):
        EvalRun(results=[])


def test_the_pass_rate_counts_what_passed() -> None:
    run = EvalRun(
        results=[
            _result("a", passed=True),
            _result("b", passed=True),
            _result("c", passed=False, failure=FailureMode.WRONG_ANSWER),
            _result("d", passed=False, failure=FailureMode.WRONG_CITATION),
        ]
    )
    assert run.passed == 2
    assert run.pass_rate == 0.5
    assert [r.entry_id for r in run.failed] == ["c", "d"]


def test_failures_are_broken_down_by_mode() -> None:
    """Forty wrong citations and forty wrong answers are different problems."""
    run = EvalRun(
        results=[
            _result("a", passed=False, failure=FailureMode.WRONG_CITATION),
            _result("b", passed=False, failure=FailureMode.WRONG_CITATION),
            _result("c", passed=False, failure=FailureMode.ANSWERED_OUT_OF_SCOPE),
        ]
    )
    assert run.failures_by_mode() == {
        FailureMode.WRONG_CITATION: 2,
        FailureMode.ANSWERED_OUT_OF_SCOPE: 1,
    }


def test_a_clean_run_reports_no_failure_modes() -> None:
    run = EvalRun(results=[_result("a", passed=True)])
    assert run.failures_by_mode() == {}
    assert run.pass_rate == 1.0


def test_the_failure_modes_distinguish_failing_open_from_failing_closed() -> None:
    """Keep the two opposite faults from being counted together.

    Answering an unanswerable question is the guardrail failing open;
    refusing an answerable one is it failing closed. A single "wrong"
    bucket would average them into a number that hides both.
    """
    run = EvalRun(
        results=[
            _result("a", passed=False, failure=FailureMode.ANSWERED_OUT_OF_SCOPE),
            _result("b", passed=False, failure=FailureMode.UNEXPECTED_REFUSAL),
        ]
    )
    assert run.failures_by_mode() == {
        FailureMode.ANSWERED_OUT_OF_SCOPE: 1,
        FailureMode.UNEXPECTED_REFUSAL: 1,
    }


def test_the_failure_vocabulary_is_pinned() -> None:
    """A mode added without a report line surfaces as an untriageable failure."""
    assert {m.value for m in FailureMode} == {
        "wrong_answer",
        "wrong_citation",
        "unexpected_refusal",
        "answered_out_of_scope",
        "pipeline_error",
    }
