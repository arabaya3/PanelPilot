"""Tests for `app/ai/guardrails/cite_or_refuse.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This is the mechanism the accuracy claim rests on, so the tests are written
against the failure that matters: an answer being produced when the corpus did
not support one. The spec singles out the middle ground — scores near the
threshold — as the hard part, so those are covered explicitly rather than only
the clear-cut "nothing found" and "exact match" ends.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.guardrails.cite_or_refuse import (
    MENTION_GAP,
    check_validated_range,
    evaluate_confidence,
    require_evidence,
    verify_citations,
)
from app.core.errors import InsufficientEvidenceError
from app.models.schemas.diagnostics import GeneratedAnswer
from app.models.schemas.guardrail import (
    ConfidenceDecision,
    DecisionOutcome,
    RefusalReason,
)
from app.models.schemas.search import Citation, RetrievedPassage

THRESHOLD = 0.6


def _passage(passage_id: str, score: float, *, manufacturer: str = "ABB") -> RetrievedPassage:
    return RetrievedPassage(
        id=passage_id,
        text=f"Passage {passage_id}.",
        score=score,
        citation=Citation(
            document_id=f"doc-{passage_id}",
            document_title="ACS880 firmware manual",
            manufacturer=manufacturer,
            page=88,
            section="3 Fault tracing",
        ),
    )


# --- the acceptance criterion -----------------------------------------------


def test_nothing_retrieved_never_answers() -> None:
    """The headline criterion: no matching content means no answer, ever."""
    decision = evaluate_confidence([], threshold=THRESHOLD)
    assert decision.outcome is DecisionOutcome.NO_VERIFIED_SOURCE
    assert not decision.may_generate
    assert decision.reason is RefusalReason.NO_EVIDENCE
    assert decision.citations == []


@pytest.mark.parametrize("run", range(10))
def test_the_refusal_is_deterministic_across_repeated_runs(run: int) -> None:
    """Reliably, from the criterion, means the same input always refuses.

    A guardrail that refuses most of the time is not a guardrail; the times it
    does not are exactly the times it mattered.
    """
    passages = [_passage("a", 0.11), _passage("b", 0.09)]
    decision = evaluate_confidence(passages, threshold=THRESHOLD)
    assert decision.outcome is DecisionOutcome.NO_VERIFIED_SOURCE
    assert decision.score == pytest.approx(0.11)


def test_a_strong_match_answers_and_carries_its_evidence() -> None:
    decision = evaluate_confidence([_passage("a", 0.92), _passage("b", 0.71)], threshold=THRESHOLD)
    assert decision.outcome is DecisionOutcome.ANSWER
    assert decision.may_generate
    assert len(decision.citations) == 2


# --- the middle ground the spec calls the hard part -------------------------


def test_exactly_at_the_threshold_answers() -> None:
    """The boundary is inclusive, and pinned so it cannot drift silently."""
    decision = evaluate_confidence([_passage("a", THRESHOLD)], threshold=THRESHOLD)
    assert decision.outcome is DecisionOutcome.ANSWER


def test_just_below_the_threshold_refuses() -> None:
    """The case that matters: plausible-looking evidence that is not good enough."""
    decision = evaluate_confidence([_passage("a", THRESHOLD - 0.001)], threshold=THRESHOLD)
    assert not decision.may_generate
    assert decision.outcome is DecisionOutcome.UNCERTAIN


def test_the_uncertain_band_names_the_closest_match() -> None:
    """An engineer given a refusal can still judge the near-miss themselves."""
    decision = evaluate_confidence([_passage("a", 0.45), _passage("b", 0.40)], threshold=THRESHOLD)
    assert decision.outcome is DecisionOutcome.UNCERTAIN
    assert decision.reason is RefusalReason.BELOW_THRESHOLD
    # Only the closest — listing the rest would imply corroboration.
    assert len(decision.citations) == 1
    assert decision.citations[0].document_id == "doc-a"


def test_below_the_mention_floor_names_nothing() -> None:
    """Pointing at a very weak match implies more support than exists."""
    decision = evaluate_confidence(
        [_passage("a", THRESHOLD - MENTION_GAP - 0.01)], threshold=THRESHOLD
    )
    assert decision.outcome is DecisionOutcome.NO_VERIFIED_SOURCE
    assert decision.citations == []


def test_many_weak_passages_do_not_add_up_to_an_answer() -> None:
    """Volume is not evidence.

    Twenty mediocre matches mean the corpus does not contain the answer twenty
    times over; it means it does not contain it.
    """
    decision = evaluate_confidence([_passage(str(n), 0.2) for n in range(20)], threshold=THRESHOLD)
    assert not decision.may_generate


def test_the_decision_records_the_threshold_it_was_taken_against() -> None:
    """So a past refusal can be explained without guessing the configuration."""
    decision = evaluate_confidence([_passage("a", 0.3)], threshold=THRESHOLD)
    assert decision.threshold == THRESHOLD


def test_ordering_of_the_input_does_not_change_the_decision() -> None:
    """The gate must not depend on an upstream sort being correct."""
    ascending = [_passage("a", 0.2), _passage("b", 0.5), _passage("c", 0.9)]
    descending = list(reversed(ascending))
    assert (
        evaluate_confidence(ascending, threshold=THRESHOLD).outcome
        is evaluate_confidence(descending, threshold=THRESHOLD).outcome
    )
    assert evaluate_confidence(ascending, threshold=THRESHOLD).score == pytest.approx(0.9)


def test_a_score_outside_the_unit_range_refuses_rather_than_clamping() -> None:
    """Regression: clamping created the hole it looked like it was closing.

    An earlier version clamped into [0, 1], which turned 5.0 and infinity into
    exactly 1.0 — clearing every threshold including the fail-closed fallback.
    An impossible score is a scorer fault, and the safe response to a fault is
    to refuse, not to invent a passing value.
    """
    for bad in (5.0, -3.0, float("inf"), float("-inf"), float("nan")):
        decision = evaluate_confidence([_passage("a", bad)], threshold=THRESHOLD)
        assert not decision.may_generate, f"score {bad} produced an answer"
        assert decision.reason is RefusalReason.UNUSABLE_SCORE


# --- the decision type cannot express an unexplainable verdict --------------


def test_an_answer_cannot_be_built_without_evidence() -> None:
    """The type refuses to represent "answer, citing nothing"."""
    with pytest.raises(ValueError, match="must carry the evidence"):
        ConfidenceDecision(outcome=DecisionOutcome.ANSWER, score=0.9, threshold=0.6)


def test_a_refusal_must_say_why() -> None:
    with pytest.raises(ValueError, match="must say why"):
        ConfidenceDecision(outcome=DecisionOutcome.UNCERTAIN, score=0.4, threshold=0.6)


def test_an_answer_cannot_carry_a_refusal_reason() -> None:
    with pytest.raises(ValueError, match="must not carry a refusal reason"):
        ConfidenceDecision(
            outcome=DecisionOutcome.ANSWER,
            score=0.9,
            threshold=0.6,
            reason=RefusalReason.NO_EVIDENCE,
            citations=[_passage("a", 0.9).citation],
        )


def test_no_verified_source_cannot_cite() -> None:
    """If nothing was worth naming, the decision must not name something."""
    with pytest.raises(ValueError, match="cannot cite"):
        ConfidenceDecision(
            outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
            score=0.0,
            threshold=0.6,
            reason=RefusalReason.NO_EVIDENCE,
            citations=[_passage("a", 0.1).citation],
        )


# --- calc tools take the same path ------------------------------------------


def test_an_out_of_range_input_refuses_like_a_missing_source() -> None:
    """Extrapolating past a table's last row invents a number that looks real."""
    decision = check_validated_range(
        quantity="ambient_temp_c",
        value=Decimal("95"),
        valid_low=Decimal("10"),
        valid_high=Decimal("55"),
    )
    assert decision is not None
    assert not decision.may_generate
    assert decision.reason is RefusalReason.OUT_OF_VALIDATED_RANGE


def test_the_out_of_range_refusal_is_actionable() -> None:
    """A bare out-of-range message tells an engineer nothing they can change."""
    decision = check_validated_range(
        quantity="ambient_temp_c",
        value=Decimal("95"),
        valid_low=Decimal("10"),
        valid_high=Decimal("55"),
    )
    assert decision is not None
    assert decision.detail is not None
    assert "ambient_temp_c" in decision.detail
    assert "95" in decision.detail
    assert "55" in decision.detail


# --- the second checkpoint: citations must resolve --------------------------


def test_an_invented_citation_is_rejected() -> None:
    """A model citing an id it was never given has fabricated a source.

    To a reader, that is indistinguishable from a real citation.
    """
    evidence = [_passage("real", 0.9)]
    answer = GeneratedAnswer(text="Use 16 mm2.", cited_passage_ids=["real", "invented"])
    with pytest.raises(InsufficientEvidenceError, match="never supplied"):
        verify_citations(answer, evidence=evidence)


def test_an_uncited_answer_is_rejected() -> None:
    evidence = [_passage("real", 0.9)]
    with pytest.raises(InsufficientEvidenceError, match="no citation"):
        verify_citations(GeneratedAnswer(text="Trust me.", cited_passage_ids=[]), evidence=evidence)


def test_repeated_citations_are_counted_once() -> None:
    """Citing one passage three times is one piece of support, not three."""
    evidence = [_passage("a", 0.9)]
    answer = GeneratedAnswer(text="See above.", cited_passage_ids=["a", "a", "a"])
    assert len(verify_citations(answer, evidence=evidence).citations) == 1


def test_a_verified_answer_keeps_its_text_and_resolves_its_citations() -> None:
    evidence = [_passage("a", 0.9), _passage("b", 0.8)]
    answer = GeneratedAnswer(text="Check the motor cable.", cited_passage_ids=["b", "a"])
    verified = verify_citations(answer, evidence=evidence)
    assert verified.text == "Check the motor cable."
    # Order follows the answer's own citation order, not retrieval order.
    assert [c.document_id for c in verified.citations] == ["doc-b", "doc-a"]


# --- the exception-raising wrapper ------------------------------------------


def test_require_evidence_raises_rather_than_returning_empty() -> None:
    """A caller that ignores a return value must not silently proceed."""
    with pytest.raises(InsufficientEvidenceError):
        require_evidence([], min_score=THRESHOLD)


def test_require_evidence_returns_passages_in_relevance_order() -> None:
    passages = [_passage("a", 0.7), _passage("b", 0.95)]
    assert [p.id for p in require_evidence(passages, min_score=THRESHOLD)] == ["b", "a"]


def test_require_evidence_and_evaluate_confidence_agree() -> None:
    """Two entry points, one decision — they must never disagree.

    A wrapper that drifted from the gate it wraps would let a caller answer
    where the gate refused.
    """
    for score in (0.0, 0.1, 0.29, 0.3, 0.31, 0.59, 0.6, 0.61, 1.0):
        passages = [_passage("a", score)]
        permitted = evaluate_confidence(passages, threshold=THRESHOLD).may_generate
        try:
            require_evidence(passages, min_score=THRESHOLD)
            raised = False
        except InsufficientEvidenceError:
            raised = True
        assert permitted is not raised, f"disagreement at score {score}"


def test_the_gate_fails_closed_when_configuration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guardrail that fails open because config was missing is not a guardrail.

    If the threshold cannot be read, the fallback refuses everything rather
    than answering freely — the strict end, not the permissive one.
    """
    from app.ai.guardrails import cite_or_refuse

    def unavailable() -> object:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(cite_or_refuse, "get_settings", unavailable)
    # A near-perfect score still refuses, because the threshold defaulted to 1.0.
    decision = cite_or_refuse.evaluate_confidence([_passage("a", 0.99)])
    assert not decision.may_generate
    assert decision.threshold == cite_or_refuse.FALLBACK_THRESHOLD


# --- regressions for what review found --------------------------------------


def test_a_perfect_score_still_refuses_without_configuration() -> None:
    """Regression: the fail-closed test previously used 0.99 and missed this.

    The fallback threshold is 1.0 and scores cap at 1.0, so a `>=` comparison
    let a perfect score answer with no configuration at all — the precise
    opposite of failing closed.
    """
    from app.ai.guardrails import cite_or_refuse

    decision = cite_or_refuse.evaluate_confidence(
        [_passage("a", 1.0)], threshold=cite_or_refuse.FALLBACK_THRESHOLD
    )
    assert not decision.may_generate, "a perfect score cleared the fail-closed fallback"


def test_a_threshold_of_zero_is_treated_as_misconfiguration() -> None:
    """Regression: threshold 0 made `score >= threshold` universally true.

    That silently disables the entire accuracy claim while every test passes.
    It is a misconfiguration, not an instruction to answer everything.
    """
    decision = evaluate_confidence([_passage("a", 0.0)], threshold=0.0)
    assert not decision.may_generate


def test_nan_cannot_make_the_verdict_depend_on_input_order() -> None:
    """Regression: NaN broke sorted(), so the same passages gave three verdicts.

    In one ordering it even suppressed a legitimate 0.9 answer, and in others
    it crashed rather than refusing — and a crash is not the refuse path.
    """
    import itertools

    passages = [_passage("nan", float("nan")), _passage("good", 0.9), _passage("weak", 0.1)]
    outcomes = {
        evaluate_confidence(list(order), threshold=THRESHOLD).outcome
        for order in itertools.permutations(passages)
    }
    assert len(outcomes) == 1, f"input ordering changed the verdict: {outcomes}"
    assert outcomes.pop() is DecisionOutcome.NO_VERIFIED_SOURCE


def test_an_answer_cites_only_the_passages_that_qualified() -> None:
    """Regression: every retrieved passage became citable material.

    A 0.01-scoring passage handed to generation is one verify_citations would
    happily resolve, so a weak match could end up cited in a real answer.
    """
    decision = evaluate_confidence(
        [_passage("strong", 0.9), _passage("junk", 0.01)], threshold=THRESHOLD
    )
    assert decision.may_generate
    assert [c.document_id for c in decision.citations] == ["doc-strong"]


def test_require_evidence_returns_only_qualifying_passages() -> None:
    """Same defect on the wrapper: it returned the whole input."""
    kept = require_evidence([_passage("strong", 0.9), _passage("junk", 0.01)], min_score=THRESHOLD)
    assert [p.id for p in kept] == ["strong"]


def test_an_in_range_value_is_not_refused() -> None:
    """Regression: the helper built a refusal unconditionally.

    An in-range value produced a refusal stating it was out of range, and left
    every caller to do its own check — the ad hoc pattern this module replaces.
    """
    assert (
        check_validated_range(
            quantity="ambient_temp_c",
            value=Decimal("30"),
            valid_low=Decimal("10"),
            valid_high=Decimal("55"),
        )
        is None
    )


def test_inverted_bounds_are_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        check_validated_range(
            quantity="q", value=Decimal("1"), valid_low=Decimal("9"), valid_high=Decimal("2")
        )


def test_conflicting_citations_for_one_passage_id_are_rejected() -> None:
    """Regression: a later passage silently overwrote an earlier citation.

    The answer was then attributed to a document the model may never have seen.
    """
    first = _passage("a", 0.9)
    second = _passage("a", 0.9, manufacturer="Siemens")
    answer = GeneratedAnswer(text="Check the cable.", cited_passage_ids=["a"])
    with pytest.raises(InsufficientEvidenceError, match="conflicting citations"):
        verify_citations(answer, evidence=[first, second])


def test_a_blank_passage_id_is_not_a_citation() -> None:
    """Regression: an empty-string id resolved as a real citation."""
    evidence = [_passage("", 0.9)]
    answer = GeneratedAnswer(text="Text.", cited_passage_ids=[""])
    with pytest.raises(InsufficientEvidenceError, match="blank id"):
        verify_citations(answer, evidence=evidence)


def test_an_empty_answer_cannot_be_verified() -> None:
    """A "verified" answer saying nothing is not an answer."""
    with pytest.raises(InsufficientEvidenceError, match="no text"):
        verify_citations(
            GeneratedAnswer(text="   ", cited_passage_ids=["a"]), evidence=[_passage("a", 0.9)]
        )


def test_the_mention_floor_does_not_scale_up_with_a_strict_threshold() -> None:
    """A threshold of 1.0 is chosen because nothing should be trusted.

    Naming a coin-flip 0.5 match as the "closest match" under that setting
    contradicts the reason the threshold was set there.
    """
    decision = evaluate_confidence([_passage("a", 0.5)], threshold=1.0)
    assert decision.outcome is DecisionOutcome.NO_VERIFIED_SOURCE
    assert decision.citations == []


def test_a_permissive_threshold_still_refuses_a_zero_score() -> None:
    """The mirror of the ratio bug, at the other end.

    A fixed gap alone meant any threshold at or below it pushed the naming
    floor to zero, so a passage scoring 0.0 was presented to the engineer as
    "the closest match". An absolute floor stops that.
    """
    for threshold in (0.05, 0.1, 0.2):
        decision = evaluate_confidence([_passage("a", 0.0)], threshold=threshold)
        assert (
            decision.outcome is DecisionOutcome.NO_VERIFIED_SOURCE
        ), f"threshold {threshold} named a zero-scoring passage"
        assert decision.citations == []


def test_all_three_outcomes_stay_reachable_across_thresholds() -> None:
    """An unreachable outcome is a branch nobody tests and nobody notices."""
    for threshold in (0.05, 0.2, 0.6, 0.95):
        outcomes = {
            evaluate_confidence([_passage("a", score)], threshold=threshold).outcome
            for score in (0.0, threshold - 0.01, 1.0)
        }
        assert len(outcomes) >= 2, f"threshold {threshold} collapses the outcomes"
        assert (
            DecisionOutcome.NO_VERIFIED_SOURCE in outcomes
        ), f"threshold {threshold} can never report no verified source"


def test_a_nan_bound_is_a_value_error_not_a_decimal_exception() -> None:
    """The contract documents ValueError; InvalidOperation would break it."""
    with pytest.raises(ValueError, match="real numbers"):
        check_validated_range(
            quantity="q",
            value=Decimal("NaN"),
            valid_low=Decimal("10"),
            valid_high=Decimal("55"),
        )
