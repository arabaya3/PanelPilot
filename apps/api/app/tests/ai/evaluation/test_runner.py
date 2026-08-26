"""Tests for `app/ai/evaluation/runner.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The spec's requirement is that **the scorer itself is proven correct before it
is trusted to gate real changes**, against a fixture with a known-should-pass
and a known-should-fail entry. That is what most of this file is: the runner
is the one component whose failure mode is silence, so it gets checked harder
than what it checks.
"""

from __future__ import annotations

import pytest

from app.ai.evaluation.runner import (
    _citation_satisfied,
    _missing_phrases,
    _normalise,
    find_coverage_gaps,
    format_report,
    run_eval_set,
    score_entry,
)
from app.models.schemas.evaluation import (
    EvalCategory,
    EvalEntry,
    ExpectedCitation,
    FailureMode,
)

_CITATION = ExpectedCitation(document_id="abb-acs880-fw", page=88)


def _entry(**overrides: object) -> EvalEntry:
    payload: dict[str, object] = {
        "id": "e1",
        "query": "Why does the ACS880 trip on overcurrent during acceleration?",
        "category": EvalCategory.STRAIGHTFORWARD,
        "expected_answer_summary": "Ramp time too short for the load inertia.",
        "required_phrases": ["acceleration time", "load inertia"],
        "expected_citation": _CITATION,
        "brand": "ABB",
    }
    payload.update(overrides)
    return EvalEntry.model_validate(payload)


def _out_of_scope(**overrides: object) -> EvalEntry:
    payload: dict[str, object] = {
        "id": "oos1",
        "query": "What is the torque spec for a 1997 Corolla head bolt?",
        "category": EvalCategory.OUT_OF_SCOPE,
        "expected_answer_summary": "Nothing in the corpus covers automotive engines.",
    }
    payload.update(overrides)
    return EvalEntry.model_validate(payload)


_GOOD_ANSWER = (
    "Extend the acceleration time; the configured ramp is too short for the "
    "load inertia driven by this motor."
)


# --- the known-should-pass and known-should-fail fixtures -------------------


def test_a_known_good_answer_passes() -> None:
    """The baseline: right phrases, right source."""
    result = score_entry(_entry(), _GOOD_ANSWER, [_CITATION])
    assert result.passed
    assert result.failure is None


def test_a_known_bad_answer_fails() -> None:
    """The scorer must not be generous with an answer missing its substance."""
    result = score_entry(_entry(), "Check the wiring.", [_CITATION])
    assert not result.passed
    assert result.failure is FailureMode.WRONG_ANSWER
    assert result.missing_phrases == ["acceleration time", "load inertia"]


def test_the_fixtures_actually_discriminate() -> None:
    """Guard against both fixtures passing for the same trivial reason.

    A pair of fixtures that agree proves the scorer runs, not that it judges.
    """
    good = score_entry(_entry(), _GOOD_ANSWER, [_CITATION])
    bad = score_entry(_entry(), "Check the wiring.", [_CITATION])
    assert good.passed is not bad.passed


# --- a right answer on the wrong source is a failure -----------------------


def test_a_correct_answer_citing_the_wrong_document_fails() -> None:
    """The failure no prose-only check would notice.

    Every required phrase is present. The source is wrong. An engineer
    following this lands in the wrong manual.
    """
    result = score_entry(
        _entry(), _GOOD_ANSWER, [ExpectedCitation(document_id="siemens-g120", page=88)]
    )
    assert not result.passed
    assert result.failure is FailureMode.WRONG_CITATION


def test_the_right_document_at_the_wrong_page_fails() -> None:
    """Right manual, wrong procedure, is still the wrong answer."""
    result = score_entry(
        _entry(), _GOOD_ANSWER, [ExpectedCitation(document_id="abb-acs880-fw", page=12)]
    )
    assert not result.passed
    assert result.failure is FailureMode.WRONG_CITATION


def test_an_entry_that_does_not_assert_a_page_accepts_any_page() -> None:
    """The author chose not to assert one; the scorer must not invent it."""
    entry = _entry(expected_citation=ExpectedCitation(document_id="abb-acs880-fw"))
    result = score_entry(
        entry, _GOOD_ANSWER, [ExpectedCitation(document_id="abb-acs880-fw", page=999)]
    )
    assert result.passed


def test_the_expected_citation_may_be_one_of_several() -> None:
    """An answer resting on three sources including the right one is correct."""
    result = score_entry(
        _entry(),
        _GOOD_ANSWER,
        [
            ExpectedCitation(document_id="other-doc", page=1),
            _CITATION,
            ExpectedCitation(document_id="third-doc", page=5),
        ],
    )
    assert result.passed


def test_citation_is_checked_before_wording() -> None:
    """A wrong-sourced answer is reported as such, not as a wording problem.

    Otherwise the most misleading outcome — right words, wrong source — gets
    filed under the least alarming failure mode.
    """
    result = score_entry(_entry(), "Check the wiring.", [ExpectedCitation(document_id="wrong-doc")])
    assert result.failure is FailureMode.WRONG_CITATION


# --- refusals are outcomes, not errors -------------------------------------


def test_refusing_an_out_of_scope_query_passes() -> None:
    """The refusal is the correct behaviour, so it is the pass condition."""
    result = score_entry(_out_of_scope(), None, [])
    assert result.passed


def test_answering_an_out_of_scope_query_is_the_worst_failure() -> None:
    """The guardrail failing open — an unsourced answer to an unanswerable question."""
    result = score_entry(_out_of_scope(), "Torque it to 60 Nm.", [])
    assert not result.passed
    assert result.failure is FailureMode.ANSWERED_OUT_OF_SCOPE
    assert result.detail is not None
    assert "failed open" in result.detail


def test_refusing_an_answerable_query_fails() -> None:
    """A refusal is not a safe default when the corpus does answer."""
    result = score_entry(_entry(), None, [])
    assert not result.passed
    assert result.failure is FailureMode.UNEXPECTED_REFUSAL


# --- the scorer is deliberately strict -------------------------------------


def test_phrase_matching_ignores_case_and_spacing() -> None:
    """Formatting is not a regression."""
    assert _missing_phrases("The  ACCELERATION\nTIME is too short.", ["acceleration time"]) == []


def test_phrase_matching_folds_unicode_width_and_spacing() -> None:
    """A non-breaking space must not read as a missing phrase."""
    assert _missing_phrases("acceleration\u00a0time", ["acceleration time"]) == []


def test_phrase_matching_does_not_stem_or_infer() -> None:
    """Deliberately dumb.

    Stemming would let "do not de-energise" satisfy a phrase requiring
    "de-energise" — a negation the scorer must never paper over.
    """
    assert _missing_phrases("Do not de-energise the panel.", ["energise the panel"]) == []
    assert _missing_phrases("accelerating", ["acceleration time"]) == ["acceleration time"]


def test_every_required_phrase_must_appear() -> None:
    """Partial credit would let half a right answer count as a pass."""
    result = score_entry(_entry(), "Extend the acceleration time.", [_CITATION])
    assert not result.passed
    assert result.missing_phrases == ["load inertia"]


def test_normalise_is_idempotent() -> None:
    once = _normalise("  Foo\u00a0 BAR  ")
    assert _normalise(once) == once


def test_citation_satisfied_rejects_an_empty_citation_list() -> None:
    assert not _citation_satisfied(_CITATION, [])


# --- running the whole set -------------------------------------------------


def test_a_run_scores_every_entry() -> None:
    entries = [_entry(), _entry(id="e2"), _out_of_scope()]

    def pipeline(entry: EvalEntry) -> tuple[str | None, list[ExpectedCitation]]:
        if entry.category is EvalCategory.OUT_OF_SCOPE:
            return None, []
        return _GOOD_ANSWER, [_CITATION]

    run = run_eval_set(entries, pipeline)
    assert len(run.results) == 3
    assert run.pass_rate == 1.0


def test_one_entry_crashing_does_not_abandon_the_run() -> None:
    """The point of a run is a complete picture of what regressed.

    Stopping at the first exception hides every entry after it, which is how
    one broken query masks nine real regressions.
    """
    entries = [_entry(id="ok1"), _entry(id="boom"), _entry(id="ok2")]

    def pipeline(entry: EvalEntry) -> tuple[str | None, list[ExpectedCitation]]:
        if entry.id == "boom":
            raise RuntimeError("opensearch unreachable")
        return _GOOD_ANSWER, [_CITATION]

    run = run_eval_set(entries, pipeline)
    assert len(run.results) == 3
    assert run.passed == 2
    crashed = next(r for r in run.results if r.entry_id == "boom")
    assert crashed.failure is FailureMode.PIPELINE_ERROR
    assert crashed.detail is not None
    assert "opensearch unreachable" in crashed.detail


def test_an_empty_set_is_refused() -> None:
    """It would report a 100% pass rate over nothing, which reads as success."""
    with pytest.raises(ValueError, match="perfect pass rate over nothing"):
        run_eval_set([], lambda _entry_arg: (None, []))


def test_duplicate_entry_ids_are_refused() -> None:
    """Otherwise a result cannot be attributed to an entry."""
    with pytest.raises(ValueError, match="duplicate eval entry ids"):
        run_eval_set([_entry(), _entry()], lambda _entry_arg: (_GOOD_ANSWER, [_CITATION]))


def test_the_pipeline_receives_the_entry_it_is_scoring() -> None:
    """A runner that passed the wrong query would score noise."""
    seen: list[str] = []

    def pipeline(entry: EvalEntry) -> tuple[str | None, list[ExpectedCitation]]:
        seen.append(entry.query)
        return _GOOD_ANSWER, [_CITATION]

    entries = [_entry(id="a", query="first"), _entry(id="b", query="second")]
    run_eval_set(entries, pipeline)
    assert seen == ["first", "second"]


# --- coverage --------------------------------------------------------------


def test_a_brand_with_no_entry_is_reported() -> None:
    """A brand with zero coverage is one where a regression ships silently."""
    gaps = find_coverage_gaps([_entry(brand="ABB")], ["ABB", "Siemens", "Schneider"])
    assert gaps == ["Schneider", "Siemens"]


def test_coverage_matching_ignores_case() -> None:
    assert find_coverage_gaps([_entry(brand="abb")], ["ABB"]) == []


def test_an_entry_with_no_brand_covers_nothing() -> None:
    """It cannot be credited to a brand it does not name."""
    assert find_coverage_gaps([_entry(brand=None)], ["ABB"]) == ["ABB"]


# --- the report ------------------------------------------------------------


def test_the_report_leads_with_failures_not_the_pass_rate() -> None:
    """Lead with what failed.

    "94% passing" invites skimming past the 6% the run exists for.
    """
    entries = [_entry(id=f"e{n}") for n in range(4)]

    def pipeline(entry: EvalEntry) -> tuple[str | None, list[ExpectedCitation]]:
        if entry.id == "e0":
            return "Check the wiring.", [_CITATION]
        return _GOOD_ANSWER, [_CITATION]

    report = format_report(run_eval_set(entries, pipeline))
    assert report.startswith("FAILED 1 of 4")
    assert "e0" in report
    assert "acceleration time" in report


def test_the_report_breaks_failures_down_by_mode() -> None:
    """Forty wrong citations and forty wrong answers are different problems."""
    entries = [_entry(id="wrong-cite"), _entry(id="wrong-words")]

    def pipeline(entry: EvalEntry) -> tuple[str | None, list[ExpectedCitation]]:
        if entry.id == "wrong-cite":
            return _GOOD_ANSWER, [ExpectedCitation(document_id="nope")]
        return "Check the wiring.", [_CITATION]

    report = format_report(run_eval_set(entries, pipeline))
    assert "wrong_citation=1" in report
    assert "wrong_answer=1" in report


def test_a_clean_run_says_so() -> None:
    report = format_report(run_eval_set([_entry()], lambda _entry_arg: (_GOOD_ANSWER, [_CITATION])))
    assert report.startswith("PASSED all 1")


def test_the_report_names_uncovered_brands() -> None:
    run = run_eval_set(
        [_entry(brand="ABB")],
        lambda _entry_arg: (_GOOD_ANSWER, [_CITATION]),
        corpus_brands=["ABB", "Siemens"],
    )
    assert "Siemens" in format_report(run)
