"""Cite-or-refuse guardrail.

The core safety invariant: PanelPilot answers from cited documentation or it
refuses. Everything else in the system — the verification queue, the
staging/production split, the calc tools — exists to give this good material.
This is what enforces "never guess" at answer time.

**One gate, called by every response path.** ``evaluate_confidence`` is the
single decision point. The alternative — each path checking for itself — fails
the first time someone adds a path and forgets, and a fabricated answer looks
exactly like a real one.

**Refusal short-circuits generation entirely.** When the decision is not
``ANSWER``, the model is never invoked and a fixed template is returned. Asking
a model to decline is not a refusal; it is another generation that happened to
decline this time, and the times it does not are exactly the times it matters.

**Confidence comes from retrieval, never from the model.** A self-reported
confidence is uncalibrated and tends to be highest when a model is confidently
wrong. The score here is the top retrieval score, which is a measurement of
whether the corpus actually contains an answer.
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.core.config import get_settings
from app.core.errors import InsufficientEvidenceError
from app.models.schemas.diagnostics import GeneratedAnswer, VerifiedAnswer
from app.models.schemas.guardrail import (
    ConfidenceDecision,
    DecisionOutcome,
    RefusalReason,
)
from app.models.schemas.search import Citation, RetrievedPassage

# Below the answer threshold but above this, the closest match is worth showing
# the engineer so they can judge it themselves. Below it, nothing is worth
# naming and pointing at a weak match would imply more support than exists.


# A threshold below this is treated as a misconfiguration rather than an
# intentional "answer everything", because that is what it would mean.
MINIMUM_USABLE_THRESHOLD = 0.05

# How far below the answer threshold a match may sit and still be worth naming.
# Expressed as a GAP rather than a ratio: a ratio widened the naming band as
# the threshold rose, so a threshold of 1.0 -- set precisely because nothing
# should be trusted -- still named a coin-flip 0.5 match as the closest one.
# A stricter threshold should narrow what gets mentioned, not widen it.
MENTION_GAP = 0.2

# Nothing below this is ever named, whatever the threshold. Without it, any
# threshold at or under MENTION_GAP pushes the naming floor to zero or below,
# and a passage scoring 0.0 gets presented to the engineer as the closest
# match. That is the mirror of the ratio bug this gap replaced.
ABSOLUTE_MENTION_FLOOR = 0.05

# Used when settings cannot be read. Deliberately the STRICTER end: a guardrail
# that fails open because configuration was missing is not a guardrail.
FALLBACK_THRESHOLD = 1.0


def _is_usable_score(score: float) -> bool:
    """Report whether a retrieval score can be reasoned about at all.

    Args:
        score: The score as supplied by retrieval.

    Returns:
        ``True`` only for a finite number within [0, 1]. NaN and infinity are
        both rejected: NaN silently breaks ordering, infinity clears every
        threshold including the fail-closed one.
    """
    return math.isfinite(score) and 0.0 <= score <= 1.0


def _resolve_threshold(threshold: float | None) -> float:
    """Return the answer threshold to judge against.

    Args:
        threshold: An explicit override, or ``None`` to read configuration.

    Returns:
        The threshold. Falls back to ``FALLBACK_THRESHOLD`` when settings are
        unavailable, which refuses everything rather than answering freely.
    """
    candidate = threshold
    if candidate is None:
        try:
            candidate = get_settings().guardrail_min_confidence
        except Exception:
            return FALLBACK_THRESHOLD

    # A threshold of 0 would make `score >= threshold` universally true and
    # disable the entire accuracy claim without failing anything. Treated as a
    # misconfiguration, not as "answer everything".
    if not _is_usable_score(candidate) or candidate < MINIMUM_USABLE_THRESHOLD:
        return FALLBACK_THRESHOLD
    return candidate


def evaluate_confidence(
    passages: list[RetrievedPassage],
    *,
    threshold: float | None = None,
) -> ConfidenceDecision:
    """Decide whether a response path may generate an answer.

    The one function every response-generating path calls before proceeding.
    Deterministic: the same retrieval result always yields the same decision,
    so a refusal can be reproduced and explained rather than being a property
    of the run.

    Args:
        passages: Retrieved passages, in descending relevance order.
        threshold: Answer threshold; defaults to the configured value.

    Returns:
        The decision, carrying the score it was taken on, the threshold it was
        taken against, and the evidence or closest match.
    """
    resolved = _resolve_threshold(threshold)

    if not passages:
        return ConfidenceDecision(
            outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
            score=0.0,
            threshold=resolved,
            reason=RefusalReason.NO_EVIDENCE,
        )

    # A score that is not a real number in [0, 1] is a scorer fault, and the
    # safe response to a fault is to refuse. Clamping was worse than useless
    # here: it turned inf and 5.0 into exactly 1.0, which then cleared even the
    # fail-closed fallback threshold. NaN was worse still — it makes sorted()
    # order-dependent, so the same passages in a different order produced
    # different verdicts, and could suppress a legitimate answer.
    unusable = [p for p in passages if not _is_usable_score(p.score)]
    if unusable:
        return ConfidenceDecision(
            outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
            score=0.0,
            threshold=resolved,
            reason=RefusalReason.UNUSABLE_SCORE,
            detail=(
                f"{len(unusable)} passage(s) carry a score that is not a real "
                "number in [0, 1]; refusing rather than guessing which to trust"
            ),
        )

    # Ranked defensively rather than trusting the caller's ordering: a decision
    # this important should not depend on an upstream sort being right.
    ranked = sorted(passages, key=lambda p: p.score, reverse=True)
    top = ranked[0]
    score = top.score

    # Strictly greater at the fallback: FALLBACK_THRESHOLD is 1.0 and scores
    # are capped at 1.0, so `>=` would let a perfect score answer with no
    # configuration at all — the exact opposite of failing closed.
    answers = score > resolved if resolved >= 1.0 else score >= resolved
    if answers:
        return ConfidenceDecision(
            outcome=DecisionOutcome.ANSWER,
            score=score,
            threshold=resolved,
            # Only the qualifying passages. Handing generation everything
            # retrieved makes a 0.01-scoring passage citable material, and
            # verify_citations would then happily resolve a citation to it.
            citations=[p.citation for p in ranked if p.score >= resolved],
        )

    # A fixed gap below the threshold, so raising the threshold NARROWS
    # the naming band instead of widening it. A ratio did the opposite:
    # at threshold 1.0 -- set precisely because nothing should be trusted
    # -- it still named a coin-flip 0.5 match as the closest one. The
    # absolute floor stops the other extreme, where a permissive threshold
    # would name a zero-scoring passage.
    if score >= max(resolved - MENTION_GAP, ABSOLUTE_MENTION_FLOOR):
        # Enough to name the closest document, not enough to answer from it.
        return ConfidenceDecision(
            outcome=DecisionOutcome.UNCERTAIN,
            score=score,
            threshold=resolved,
            reason=RefusalReason.BELOW_THRESHOLD,
            citations=[top.citation],
        )

    return ConfidenceDecision(
        outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
        score=score,
        threshold=resolved,
        reason=RefusalReason.NO_EVIDENCE,
    )


def check_validated_range(
    *,
    quantity: str,
    value: Decimal,
    valid_low: Decimal,
    valid_high: Decimal,
    threshold: float | None = None,
) -> ConfidenceDecision | None:
    """Return a refusal when a calc-tool input falls outside its validated range.

    The spec applies the same guardrail to calculations: an input outside the
    range the underlying tables cover takes the refuse path rather than being
    extrapolated. Extrapolating a derating table past its last row produces a
    number that looks like every other number the tool returns, and an engineer
    has no way to tell it was invented.

    Args:
        quantity: What was out of range, e.g. ``"ambient_temp_c"``.
        value: The value supplied.
        valid_low: Lowest value the tables cover.
        valid_high: Highest value the tables cover.
        threshold: Recorded on the decision for consistency with the retrieval
            path; defaults to the configured value.

    Returns:
        A refusal naming the quantity and the range, or ``None`` when the value
        is within range and the calculation may proceed.

    Raises:
        ValueError: If the bounds are inverted, which is a caller bug rather
            than an out-of-range input.
    """
    # A NaN Decimal makes every comparison below raise InvalidOperation, which
    # is neither the documented ValueError nor the refuse path. Caught here so
    # the contract holds: this function raises ValueError or returns.
    if any(v.is_nan() for v in (value, valid_low, valid_high)):
        raise ValueError(f"{quantity}: value and bounds must be real numbers")
    if valid_low > valid_high:
        raise ValueError(f"{quantity}: valid_low {valid_low} exceeds valid_high {valid_high}")
    # Checked here rather than by the caller. An earlier version built the
    # refusal unconditionally, so an in-range value produced a refusal stating
    # it was out of range -- and left every caller to do its own check, which
    # is the ad hoc pattern this module exists to replace.
    if valid_low <= value <= valid_high:
        return None
    return ConfidenceDecision(
        outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
        score=0.0,
        # Resolved defensively. A refusal must not itself fail because config
        # is unavailable -- the whole point of this path is that it works when
        # other things do not.
        threshold=_resolve_threshold(threshold),
        reason=RefusalReason.OUT_OF_VALIDATED_RANGE,
        detail=(
            f"{quantity}={value} is outside the validated range "
            f"{valid_low} to {valid_high}; the underlying tables do not cover it"
        ),
    )


def require_evidence(
    passages: list[RetrievedPassage],
    *,
    min_score: float | None = None,
) -> list[RetrievedPassage]:
    """Return the passages good enough to answer from, or refuse.

    A thin wrapper over ``evaluate_confidence`` for callers that want an
    exception rather than a decision object. New code should prefer
    ``evaluate_confidence``: a decision carries the score, the threshold and
    the closest match, all of which the engineer's refusal message needs.

    Args:
        passages: Candidate passages from retrieval.
        min_score: Score floor; defaults to the configured value.

    Returns:
        The passages, in relevance order, when generation is permitted.

    Raises:
        InsufficientEvidenceError: If no passage clears the threshold.
    """
    decision = evaluate_confidence(passages, threshold=min_score)
    if not decision.may_generate:
        raise InsufficientEvidenceError(
            f"{decision.reason.value if decision.reason else 'refused'}: "
            f"top score {decision.score:.3f} below threshold {decision.threshold:.3f}"
        )
    # Only the qualifying passages, matching the decision's citations. Returning
    # everything made sub-threshold passages part of the evidence set.
    qualifying = [p for p in passages if p.score >= decision.threshold]
    return sorted(qualifying, key=lambda p: p.score, reverse=True)


def verify_citations(
    answer: GeneratedAnswer,
    *,
    evidence: list[RetrievedPassage],
) -> VerifiedAnswer:
    """Check that every cited id in a generated answer resolves to evidence.

    The second checkpoint. Clearing the confidence gate means the corpus
    contained an answer; this checks the model actually used it. A model that
    cites an id it was never given has invented a source, and that is
    indistinguishable from a real citation to anyone reading the answer.

    Args:
        answer: The model's raw answer with its inline citations.
        evidence: The passages the answer was generated from.

    Returns:
        The answer with citations resolved to source documents.

    Raises:
        InsufficientEvidenceError: If the answer cites an id that was not
            supplied, or makes a claim carrying no citation at all.
    """
    if not answer.text.strip():
        raise InsufficientEvidenceError("answer has no text to verify")

    # G: a later passage must not silently overwrite an earlier one's citation
    # under the same id -- that attributes the answer to a document the model
    # may never have been shown.
    supplied: dict[str, Citation] = {}
    for passage in evidence:
        if not passage.id.strip():
            raise InsufficientEvidenceError("evidence carries a passage with a blank id")
        if passage.id in supplied and supplied[passage.id] != passage.citation:
            raise InsufficientEvidenceError(
                f"evidence contains conflicting citations for passage {passage.id!r}"
            )
        supplied[passage.id] = passage.citation

    blank = [cited for cited in answer.cited_passage_ids if not cited.strip()]
    if blank:
        raise InsufficientEvidenceError("answer cites a blank passage id")

    unknown = [cited for cited in answer.cited_passage_ids if cited not in supplied]
    if unknown:
        raise InsufficientEvidenceError(
            f"answer cites passages that were never supplied: {', '.join(sorted(unknown))}"
        )

    if not answer.cited_passage_ids:
        raise InsufficientEvidenceError("answer carries no citation at all")

    # Deduplicated but order-preserving: an answer citing one passage three
    # times is supported once, and presenting it three times would overstate
    # how much evidence there is.
    resolved: list[Citation] = []
    seen: set[str] = set()
    for cited in answer.cited_passage_ids:
        if cited not in seen:
            seen.add(cited)
            resolved.append(supplied[cited])

    return VerifiedAnswer(text=answer.text, citations=resolved)
