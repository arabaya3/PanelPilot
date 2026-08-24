"""Confidence scoring for generated answers.

Confidence is computed from observable signals — retrieval scores, agreement
between passages, citation density — not asked of the model. Answers scoring
below the configured threshold are surfaced to the engineer as low-confidence
rather than suppressed.
"""

from __future__ import annotations

from app.models.schemas.diagnostics import ConfidenceBreakdown, VerifiedAnswer
from app.models.schemas.search import RetrievedPassage


def score_confidence(
    answer: VerifiedAnswer,
    *,
    evidence: list[RetrievedPassage],
) -> ConfidenceBreakdown:
    """Score how well the evidence supports an answer.

    Args:
        answer: The citation-verified answer.
        evidence: The passages it was generated from.

    Returns:
        The overall score in [0, 1] plus the per-signal components that
        produced it, so a low score is explainable to the engineer.
    """
    raise NotImplementedError


def is_publishable(breakdown: ConfidenceBreakdown, *, threshold: float | None = None) -> bool:
    """Report whether an answer clears the confidence threshold.

    Args:
        breakdown: The computed confidence breakdown.
        threshold: Override for the configured minimum.

    Returns:
        ``True`` if the answer may be shown without a low-confidence warning.
    """
    raise NotImplementedError
