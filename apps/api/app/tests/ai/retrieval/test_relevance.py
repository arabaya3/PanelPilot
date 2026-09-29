"""Tests for `app/ai/retrieval/relevance.py` -- absolute relevance and its floor.

The floor's value is a measurement, not a constant, so what is pinned here is
the arithmetic that turns measurements into a recommendation and the two
signals it rests on. Its effect on a real engine is in ``test_hybrid_search``.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval.relevance import (
    MIN_IN_SCOPE,
    MIN_OUT_OF_SCOPE,
    anchors_of,
    calibrate,
    calibrate_from_eval_set,
    cosine,
    is_anchored,
)
from app.models.schemas.evaluation import EvalCategory, EvalEntry, ExpectedCitation
from app.models.schemas.search import Citation, RetrievedPassage

# --- cosine --------------------------------------------------------------------


def test_cosine_measures_direction_not_length() -> None:
    assert cosine([1.0, 0.0], [5.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("a", "b"),
    [([1.0, 0.0], [1.0]), ([], []), ([0.0, 0.0], [1.0, 0.0]), ([float("inf"), 0.0], [1.0, 0.0])],
)
def test_vectors_that_cannot_be_compared_measure_nothing(a: list[float], b: list[float]) -> None:
    """Not 0.0: "could not measure" must not read as "measured, unrelated"."""
    assert cosine(a, b) is None


# --- code anchors --------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "anchors"),
    [
        ("what does F0001 mean", {"0001"}),
        ("AL 5091 on the panel", {"5091"}),
        ("fault 2340 after restart", {"2340"}),
        ("set par 21.03 and 99.04", {"21.03", "99.04"}),
        ("E-24 on the display", set()),  # two digits: a voltage as often as a code
        ("the motor trips under load", set()),
    ],
)
def test_anchors_are_the_code_numbers_a_query_names(query: str, anchors: set[str]) -> None:
    assert anchors_of(query) == anchors


@pytest.mark.parametrize(
    ("text", "anchored"),
    [
        ("Fault F0001 OVERCURRENT", True),
        ("fault F-0001: overcurrent", True),
        ("fault 0001.", True),
        ("Fault F10001 is unrelated", False),  # inside a longer number
        ("see 0001.5 in the appendix", False),  # a different number
        ("Fault F0002 OVERVOLTAGE", False),
    ],
)
def test_an_anchor_matches_only_as_a_whole_number(text: str, anchored: bool) -> None:
    assert is_anchored(text, frozenset({"0001"})) is anchored


def test_no_anchors_anchor_nothing() -> None:
    assert not is_anchored("Fault F0001", frozenset())


# --- calibration ---------------------------------------------------------------


def _spread(n: int, low: float, high: float) -> list[float]:
    return [low + (high - low) * i / (n - 1) for i in range(n)]


def test_the_floor_keeps_the_requested_share_of_answers() -> None:
    in_scope = _spread(40, 0.40, 0.80)
    out_of_scope = [0.10, 0.20, 0.30, 0.45, 0.50]

    result = calibrate(in_scope, out_of_scope, keep=0.95)

    assert result.floor is not None
    assert result.kept is not None
    assert result.kept >= 0.95
    assert sum(s >= result.floor for s in in_scope) == 38  # 2 of 40 may be lost
    assert result.refused == pytest.approx(3 / 5)


def test_the_floor_is_rounded_down_never_past_a_question_it_keeps() -> None:
    in_scope = [0.4567] * MIN_IN_SCOPE
    result = calibrate(in_scope, [0.1] * MIN_OUT_OF_SCOPE, keep=1.0)

    assert result.floor == 0.456
    assert result.kept == 1.0


def test_too_few_questions_recommend_nothing() -> None:
    result = calibrate([0.5] * (MIN_IN_SCOPE - 1), [0.1] * MIN_OUT_OF_SCOPE)

    assert result.floor is None
    assert result.reason is not None
    assert "at least" in result.reason


def test_no_recommendation_when_similarity_does_not_separate_the_two() -> None:
    """A floor that refuses nothing off-topic would only cost answers."""
    result = calibrate(_spread(40, 0.40, 0.80), [0.60, 0.70, 0.75, 0.78, 0.80])

    assert result.floor is None
    assert result.refused == 0.0
    assert result.reason is not None
    assert "does not separate" in result.reason


# --- calibrating from an eval set ----------------------------------------------


def _answerable(entry_id: str, query: str) -> EvalEntry:
    return EvalEntry(
        id=entry_id,
        query=query,
        category=EvalCategory.STRAIGHTFORWARD,
        expected_answer_summary="An answer.",
        required_phrases=["answer"],
        expected_citation=ExpectedCitation(document_id="doc"),
    )


def _off_topic(entry_id: str) -> EvalEntry:
    return EvalEntry(
        id=entry_id,
        query="torque spec for a Corolla head bolt",
        category=EvalCategory.OUT_OF_SCOPE,
        expected_answer_summary="Not in the corpus.",
    )


def _passage(similarity: float | None, *, anchored: bool = False) -> RetrievedPassage:
    return RetrievedPassage(
        id="p",
        text="t",
        score=1.0,
        similarity=similarity,
        anchored=anchored,
        citation=Citation(document_id="doc", document_title="Doc", manufacturer="ABB"),
    )


def test_an_eval_set_is_split_into_answers_and_refusals() -> None:
    entries = [_answerable(f"a{i}", f"question {i}") for i in range(MIN_IN_SCOPE)] + [
        _off_topic(f"o{i}") for i in range(MIN_OUT_OF_SCOPE)
    ]

    def retrieve(entry: EvalEntry) -> list[RetrievedPassage]:
        # The best passage decides; a weaker one alongside must not.
        if entry.category is EvalCategory.OUT_OF_SCOPE:
            return [_passage(0.1), _passage(0.05)]
        return [_passage(0.2), _passage(0.7)]

    result = calibrate_from_eval_set(entries, retrieve)

    assert (result.in_scope, result.out_of_scope, result.skipped) == (
        MIN_IN_SCOPE,
        MIN_OUT_OF_SCOPE,
        0,
    )
    assert result.floor == 0.7
    assert result.refused == 1.0


def test_questions_the_floor_cannot_affect_are_not_measured() -> None:
    entries = [
        _answerable("anchored", "what does F0001 mean"),
        _answerable("empty", "question"),
        _answerable("blind", "question"),
    ]
    answers = {
        "anchored": [_passage(0.1, anchored=True)],
        "empty": [],
        "blind": [_passage(None)],
    }

    result = calibrate_from_eval_set(entries, lambda entry: answers[entry.id])

    assert (result.in_scope, result.skipped) == (0, 3)
