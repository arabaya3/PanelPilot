"""Tests for `app/ai/retrieval/tuning.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The spec names overfitting as the risk, and per-category reporting as the
defence against an aggregate hiding a broken category. Both are asserted here
rather than assumed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.retrieval.tuning import (
    CategoryMetrics,
    PrecisionShortfallError,
    TuningReport,
    TuningSplit,
    assert_meets_threshold,
    format_tuning_report,
    measure,
    split_eval_set,
    tune,
)
from app.models.schemas.evaluation import EvalCategory, EvalEntry, ExpectedCitation
from app.models.schemas.retrieval_config import BlendWeights, QueryType, RetrievalConfig


def _entry(entry_id: str, query: str, document_id: str = "doc-a") -> EvalEntry:
    return EvalEntry(
        id=entry_id,
        query=query,
        category=EvalCategory.STRAIGHTFORWARD,
        expected_answer_summary="An answer.",
        required_phrases=["answer"],
        expected_citation=ExpectedCitation(document_id=document_id),
    )


def _out_of_scope(entry_id: str) -> EvalEntry:
    return EvalEntry(
        id=entry_id,
        query="torque spec for a Corolla head bolt",
        category=EvalCategory.OUT_OF_SCOPE,
        expected_answer_summary="Not in the corpus.",
    )


def _perfect_retriever(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
    """Always retrieves exactly the expected document."""
    assert entry.expected_citation is not None
    return [entry.expected_citation.document_id]


def _useless_retriever(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
    """Never retrieves the expected document."""
    return ["wrong-doc"]


# --- the held-out split ----------------------------------------------------


def _many(n: int = 40) -> list[EvalEntry]:
    return [_entry(f"e{i}", "the drive trips when starting") for i in range(n)]


def test_a_split_holds_some_entries_back() -> None:
    split = split_eval_set(_many())
    assert split.tuning
    assert split.holdout


def test_the_halves_are_disjoint() -> None:
    """Refuse any overlap between the halves.

    One leaked entry turns the holdout score into a partly-memorised score,
    and nothing downstream could detect it.
    """
    split = split_eval_set(_many())
    assert not ({e.id for e in split.tuning} & {e.id for e in split.holdout})


def test_the_split_covers_every_entry() -> None:
    """An entry in neither half is one nothing ever measures."""
    entries = _many()
    split = split_eval_set(entries)
    assert len(split.tuning) + len(split.holdout) == len(entries)


def test_the_split_is_deterministic() -> None:
    """Put the same entries in the same half on every run.

    A split that moves between runs makes two results incomparable, and
    "it improved" unfalsifiable.
    """
    first = split_eval_set(_many())
    second = split_eval_set(list(reversed(_many())))
    assert {e.id for e in first.holdout} == {e.id for e in second.holdout}


def test_the_split_does_not_depend_on_input_order() -> None:
    """Otherwise re-ordering the eval file would silently re-split it."""
    entries = _many()
    forward = split_eval_set(entries)
    backward = split_eval_set(list(reversed(entries)))
    assert {e.id for e in forward.tuning} == {e.id for e in backward.tuning}


def test_a_leaking_split_is_refused() -> None:
    """Constructed directly, since split_eval_set cannot produce one."""
    shared = _entry("shared", "q")
    with pytest.raises(ValidationError, match="entries in both halves"):
        TuningSplit(tuning=[shared], holdout=[shared])


def test_a_set_too_small_to_split_is_refused() -> None:
    """An empty holdout means the final number measures nothing."""
    with pytest.raises(ValueError, match="too small to split"):
        split_eval_set([_entry("only", "q")])


@pytest.mark.parametrize("percent", [0, 100, -1, 101])
def test_an_absurd_holdout_percentage_is_refused(percent: int) -> None:
    with pytest.raises(ValueError, match="holdout_percent"):
        split_eval_set(_many(), holdout_percent=percent)


# --- measurement is per category -------------------------------------------


def test_metrics_are_reported_per_query_type() -> None:
    """An aggregate can look healthy while one category is broken."""
    entries = [
        _entry("code", "F0001"),
        _entry("param", "par 21.03"),
        _entry("symptom", "it trips when starting"),
    ]
    report = measure(entries, RetrievalConfig(), _perfect_retriever)
    assert {m.query_type for m in report.by_category} == set(QueryType)


def test_every_query_type_appears_even_when_untested() -> None:
    """An absent category reads as "fine" when it means "never tested"."""
    report = measure([_entry("code", "F0001")], RetrievalConfig(), _perfect_retriever)
    assert set(report.untested_categories) == {
        QueryType.PARAMETER_LOOKUP,
        QueryType.SYMPTOM_DESCRIPTION,
    }


def test_a_perfect_retriever_scores_one() -> None:
    report = measure([_entry("code", "F0001")], RetrievalConfig(), _perfect_retriever)
    metric = next(m for m in report.by_category if m.query_type is QueryType.FAULT_CODE)
    assert metric.precision == 1.0
    assert metric.recall == 1.0


def test_a_useless_retriever_scores_zero() -> None:
    report = measure([_entry("code", "F0001")], RetrievalConfig(), _useless_retriever)
    metric = next(m for m in report.by_category if m.query_type is QueryType.FAULT_CODE)
    assert metric.precision == 0.0
    assert metric.recall == 0.0


def test_precision_and_recall_move_independently() -> None:
    """They fail in opposite directions, so one must not stand in for the other.

    Retrieving the right document buried among nine wrong ones is full recall
    and poor precision — the answer is reachable, but generation is being
    handed nine irrelevant passages it might cite.
    """

    def noisy(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        return [entry.expected_citation.document_id] + [f"junk-{n}" for n in range(9)]

    report = measure([_entry("code", "F0001")], RetrievalConfig(), noisy)
    metric = next(m for m in report.by_category if m.query_type is QueryType.FAULT_CODE)
    assert metric.recall == 1.0
    assert metric.precision == pytest.approx(0.1)


def test_out_of_scope_entries_do_not_distort_precision() -> None:
    """They assert nothing should be found, so they have no expected document."""
    report = measure(
        [_entry("code", "F0001"), _out_of_scope("oos")], RetrievalConfig(), _perfect_retriever
    )
    total = sum(m.queries for m in report.by_category)
    assert total == 1


def test_the_weakest_category_is_identified() -> None:
    """The number to read first, ahead of any average."""

    def selective(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        if entry.id == "symptom":
            return ["wrong-doc"]
        return [entry.expected_citation.document_id]

    entries = [_entry("code", "F0001"), _entry("symptom", "it trips when starting")]
    report = measure(entries, RetrievalConfig(), selective)
    weakest = report.weakest_category
    assert weakest is not None
    assert weakest.query_type is QueryType.SYMPTOM_DESCRIPTION


def test_an_untested_category_is_never_the_weakest() -> None:
    """Zero-of-zero is not a bad score; it is the absence of one."""
    report = measure([_entry("code", "F0001")], RetrievalConfig(), _perfect_retriever)
    weakest = report.weakest_category
    assert weakest is not None
    assert weakest.query_type is QueryType.FAULT_CODE


def test_metrics_cannot_report_more_hits_than_queries() -> None:
    with pytest.raises(ValidationError, match="hits from"):
        CategoryMetrics(
            query_type=QueryType.FAULT_CODE, queries=2, hits=3, precision=1.0, recall=1.0
        )


# --- tuning ----------------------------------------------------------------


def test_tuning_returns_a_valid_config() -> None:
    split = split_eval_set(_many())
    config = tune(split.tuning, _perfect_retriever)
    for query_type in QueryType:
        assert config.weights_for(query_type)


def test_tuning_leaves_untested_categories_at_their_defaults() -> None:
    """Tuning a category against nothing produces a number with no evidence."""
    entries = [_entry(f"c{n}", "F0001") for n in range(10)]
    tuned = tune(entries, _perfect_retriever)
    default = RetrievalConfig()
    assert tuned.weights_for(QueryType.SYMPTOM_DESCRIPTION) == default.weights_for(
        QueryType.SYMPTOM_DESCRIPTION
    )


def test_tuning_picks_the_weight_that_retrieves_best() -> None:
    """The loop must actually respond to measurement, not just return defaults."""

    def weight_sensitive(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        # Only a heavily lexical blend finds it.
        if config.weights_for(QueryType.FAULT_CODE).bm25 >= 0.85:
            return [entry.expected_citation.document_id]
        return ["wrong-doc"]

    entries = [_entry(f"c{n}", "F0001") for n in range(6)]
    tuned = tune(entries, weight_sensitive)
    assert tuned.weights_for(QueryType.FAULT_CODE).bm25 >= 0.85


def test_tuning_each_category_independently() -> None:
    """A weight that helps fault codes can hurt symptoms.

    Tuning them together picks whichever category has more entries.
    """

    def per_type(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        from app.ai.retrieval.query_classifier import classify_query

        query_type = classify_query(entry.query)
        weights = config.weights_for(query_type)
        wants_lexical = query_type is QueryType.FAULT_CODE
        good = weights.bm25 >= 0.85 if wants_lexical else weights.vector >= 0.85
        return [entry.expected_citation.document_id] if good else ["wrong-doc"]

    entries = [_entry(f"c{n}", "F0001") for n in range(4)] + [
        _entry(f"s{n}", "it trips when starting") for n in range(4)
    ]
    tuned = tune(entries, per_type)
    assert tuned.weights_for(QueryType.FAULT_CODE).bm25 >= 0.85
    assert tuned.weights_for(QueryType.SYMPTOM_DESCRIPTION).vector >= 0.85


def test_tuning_only_ever_produces_valid_blends() -> None:
    """Every candidate must still sum to 1 with no zero leg."""
    entries = [_entry(f"c{n}", "F0001") for n in range(4)]
    tuned = tune(entries, _perfect_retriever, candidate_weights=(0.15, 0.5, 0.85))
    weights = tuned.weights_for(QueryType.FAULT_CODE)
    assert pytest.approx(1.0) == weights.bm25 + weights.vector
    assert weights.bm25 > 0
    assert weights.vector > 0


# --- the report ------------------------------------------------------------


def test_the_report_distinguishes_holdout_from_tuning_numbers() -> None:
    """Only a holdout number is a claim about retrieval quality.

    A tuning-split number describes the loop's progress against data it was
    allowed to fit, and reading it as generalisation is the overfitting the
    split exists to prevent.
    """
    entries = [_entry("code", "F0001")]
    tuning_report = format_tuning_report(measure(entries, RetrievalConfig(), _perfect_retriever))
    holdout_report = format_tuning_report(
        measure(entries, RetrievalConfig(), _perfect_retriever, holdout=True)
    )
    assert "not a generalisation claim" in tuning_report
    assert "HOLDOUT" in holdout_report
    assert "HOLDOUT" not in tuning_report
    assert "tuning split" in tuning_report
    assert "not a generalisation claim" not in holdout_report


def test_the_report_names_untested_categories() -> None:
    report = format_tuning_report(
        measure([_entry("code", "F0001")], RetrievalConfig(), _perfect_retriever)
    )
    assert "no eval entries for" in report
    assert QueryType.SYMPTOM_DESCRIPTION.value in report


def test_the_report_orders_weakest_first() -> None:
    """So the category that needs attention is the one read first."""

    def selective(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        if entry.id == "symptom":
            return ["wrong-doc"]
        return [entry.expected_citation.document_id]

    entries = [_entry("code", "F0001"), _entry("symptom", "it trips when starting")]
    report = format_tuning_report(measure(entries, RetrievalConfig(), selective))
    lines = [line for line in report.splitlines() if "precision=" in line]
    assert QueryType.SYMPTOM_DESCRIPTION.value in lines[0]


def test_the_report_has_no_aggregate_score() -> None:
    """A single number across query types is exactly what hides a broken one."""
    entries = [_entry("code", "F0001"), _entry("symptom", "it trips when starting")]
    report = format_tuning_report(measure(entries, RetrievalConfig(), _perfect_retriever))
    assert "overall" not in report.casefold()
    assert "average" not in report.casefold()


def test_the_report_shows_the_weights_each_category_was_measured_under() -> None:
    """A precision number is meaningless without the weights behind it."""
    report = format_tuning_report(
        measure([_entry("code", "F0001")], RetrievalConfig(), _perfect_retriever)
    )
    assert "bm25=" in report
    assert "vector=" in report


def test_a_report_carries_its_config() -> None:
    report = TuningReport(config=RetrievalConfig(), by_category=[])
    assert report.config.weights_for(QueryType.FAULT_CODE) == BlendWeights(bm25=0.85, vector=0.15)


# --- the tie-break actually breaks ties ------------------------------------


def test_recall_breaks_a_precision_tie() -> None:
    """The bug this replaced chose by list position, not by recall.

    Holding only the precision in `best_score` made the comparison's
    right-hand side `(best_precision, -1.0)`, which every equal-precision
    candidate beat — so tuning silently preferred the last candidate tried,
    which for symptom descriptions is the most lexical blend. That inverts
    the premise of the whole task.
    """

    def tie(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        weights = config.weights_for(QueryType.FAULT_CODE)
        # Every blend is perfectly precise; only recall separates them.
        found = {0.5: 2, 0.85: 1, 0.15: 1}[round(weights.bm25, 2)]
        index = int(entry.id[1:])
        return [entry.expected_citation.document_id] if index < found else []

    entries = [_entry("c0", "F0001"), _entry("c1", "F0002")]
    # The best-recall candidate sits in the MIDDLE of the list, so neither
    # "first wins" nor "last wins" can pass by accident — only a comparison
    # that genuinely compares recall picks it.
    tuned = tune(entries, tie, candidate_weights=(0.85, 0.5, 0.15))
    assert tuned.weights_for(QueryType.FAULT_CODE).bm25 == 0.5


def test_higher_precision_beats_higher_recall() -> None:
    """Precision still decides; recall only breaks a tie.

    An irrelevant passage reaching generation is worse than a missed one,
    because the miss refuses safely and the irrelevant one gets cited.
    """

    def trade_off(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        doc = entry.expected_citation.document_id
        if config.weights_for(QueryType.FAULT_CODE).bm25 <= 0.2:
            # Full recall, poor precision.
            return [doc] + [f"junk-{n}" for n in range(9)]
        # Perfect precision on one of the two.
        return [doc] if entry.id == "c0" else []

    entries = [_entry("c0", "F0001"), _entry("c1", "F0002")]
    tuned = tune(entries, trade_off, candidate_weights=(0.15, 0.85))
    assert tuned.weights_for(QueryType.FAULT_CODE).bm25 == 0.85


# --- duplicates do not inflate precision ------------------------------------


def test_repeated_passages_from_one_document_count_once() -> None:
    """A passage-level retriever legitimately returns several chunks per doc.

    Counting each as a separate relevant result would score near-perfect
    precision no matter how much noise sat alongside them.
    """

    def chunky(entry: EvalEntry, config: RetrievalConfig) -> list[str]:
        assert entry.expected_citation is not None
        return [entry.expected_citation.document_id] * 5 + ["junk-a", "junk-b"]

    report = measure([_entry("code", "F0001")], RetrievalConfig(), chunky)
    metric = next(m for m in report.by_category if m.query_type is QueryType.FAULT_CODE)
    # One relevant document out of three distinct ones, not five out of seven.
    assert metric.precision == pytest.approx(1 / 3)


# --- the holdout split is not biased ----------------------------------------


def test_the_holdout_is_close_to_the_requested_size() -> None:
    """`digest[0] % 100` over a 0-255 byte skews buckets 0-55 by 50%.

    A nominal 25% holdout was really 29%, which makes the parameter mean
    something other than what it says.
    """
    entries = [_entry(f"e{n}", "the drive trips when starting") for n in range(2000)]
    split = split_eval_set(entries, holdout_percent=25)
    fraction = len(split.holdout) / len(entries)
    assert 0.22 < fraction < 0.28


# --- the acceptance gate ----------------------------------------------------


def _report_at(precision: float, *, floor: float = 0.7) -> TuningReport:
    return TuningReport(
        config=RetrievalConfig(min_precision=floor),
        by_category=[
            CategoryMetrics(
                query_type=QueryType.FAULT_CODE,
                queries=4,
                hits=4,
                precision=precision,
                recall=1.0,
            )
        ],
        holdout=True,
    )


def test_a_category_below_the_floor_fails_the_gate() -> None:
    """The acceptance criterion is a refusal, not a printed number."""
    with pytest.raises(PrecisionShortfallError, match=r"below the 0\.70 floor"):
        assert_meets_threshold(_report_at(0.5))


def test_a_category_at_the_floor_passes() -> None:
    """The bar is met, not merely approached — `>=`, not `>`."""
    assert_meets_threshold(_report_at(0.7))


def test_the_gate_checks_every_category_not_an_average() -> None:
    """Check every category, never an average.

    An aggregate clearing the bar while one category sits at 0.2 is the exact
    failure per-category reporting exists to surface.
    """
    report = TuningReport(
        config=RetrievalConfig(min_precision=0.7),
        by_category=[
            CategoryMetrics(
                query_type=QueryType.FAULT_CODE, queries=8, hits=8, precision=1.0, recall=1.0
            ),
            CategoryMetrics(
                query_type=QueryType.SYMPTOM_DESCRIPTION,
                queries=2,
                hits=1,
                precision=0.2,
                recall=0.5,
            ),
        ],
        holdout=True,
    )
    # The mean of 1.0 and 0.2 weighted by query count is 0.84 — comfortably
    # above the floor, and completely wrong to accept.
    with pytest.raises(PrecisionShortfallError, match="symptom_description"):
        assert_meets_threshold(report)


def test_a_run_that_measured_nothing_fails_the_gate() -> None:
    """Otherwise an empty eval set passes by checking nothing."""
    report = TuningReport(
        config=RetrievalConfig(),
        by_category=[
            CategoryMetrics(
                query_type=QueryType.FAULT_CODE, queries=0, hits=0, precision=0.0, recall=0.0
            )
        ],
        holdout=True,
    )
    with pytest.raises(PrecisionShortfallError, match="checked nothing"):
        assert_meets_threshold(report)


def test_the_floor_is_configurable() -> None:
    """It is a product decision about acceptable wrong-passage risk."""
    assert_meets_threshold(_report_at(0.5, floor=0.4))
    with pytest.raises(PrecisionShortfallError):
        assert_meets_threshold(_report_at(0.5, floor=0.6))


def test_the_report_marks_failing_categories() -> None:
    """So a number can be read against the bar without doing the comparison."""
    rendered = format_tuning_report(_report_at(0.2))
    assert "FAIL" in rendered
    assert "precision floor 0.70" in rendered
