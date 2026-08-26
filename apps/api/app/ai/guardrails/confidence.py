"""Confidence scoring for generated answers.

Confidence is computed from observable signals — retrieval scores, agreement
between passages, citation density — not asked of the model. Answers scoring
below the configured threshold are surfaced to the engineer as low-confidence
rather than suppressed.
"""

from __future__ import annotations

from app.models.schemas.diagnostics import ConfidenceBreakdown, VerifiedAnswer
from app.models.schemas.search import RetrievedPassage

# How the three signals combine. Retrieval carries the most because it is the
# only one measured before the model spoke: agreement and density describe an
# answer that already exists, and an answer can be densely cited and still
# rest on passages that barely matched the question.
_RETRIEVAL_WEIGHT = 0.5
_AGREEMENT_WEIGHT = 0.3
_DENSITY_WEIGHT = 0.2

# Citations beyond this add no further confidence. Four sources agreeing is a
# well-supported answer; forty is not ten times better, and rewarding it would
# reward padding the citation list.
_WELL_CITED = 4


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

        Three signals, deliberately none of them the model's own opinion of
        its answer. A model's self-reported confidence is not calibrated and
        is highest exactly when it is confidently wrong.

        ``retrieval_score`` is how well the corpus matched the question,
        ``passage_agreement`` how much of the retrieved evidence the answer
        actually rests on, and ``citation_density`` how heavily cited the
        answer is. An answer citing one passage out of ten retrieved is
        weaker than one citing six, even if both read fluently.
    """
    if not evidence:
        # Reachable only if a caller skipped the guardrail, which the
        # architecture test forbids. Scoring zero rather than raising keeps a
        # bug from becoming a 500, and zero refuses downstream.
        return ConfidenceBreakdown(
            overall=0.0, retrieval_score=0.0, passage_agreement=0.0, citation_density=0.0
        )

    retrieval_score = max(passage.score for passage in evidence)

    cited_ids = {citation.document_id for citation in answer.citations}
    supporting = [p for p in evidence if p.citation.document_id in cited_ids]
    passage_agreement = len(supporting) / len(evidence)

    # Density is measured against what was cited, not against answer length:
    # a long answer is not less trustworthy for being long, but an answer
    # resting on one passage is less trustworthy than one resting on several.
    citation_density = min(len(answer.citations) / _WELL_CITED, 1.0)

    overall = (
        _RETRIEVAL_WEIGHT * retrieval_score
        + _AGREEMENT_WEIGHT * passage_agreement
        + _DENSITY_WEIGHT * citation_density
    )
    return ConfidenceBreakdown(
        overall=min(max(overall, 0.0), 1.0),
        retrieval_score=retrieval_score,
        passage_agreement=passage_agreement,
        citation_density=citation_density,
    )


def is_publishable(breakdown: ConfidenceBreakdown, *, threshold: float | None = None) -> bool:
    """Report whether an answer clears the confidence threshold.

    Args:
        breakdown: The computed confidence breakdown.
        threshold: Override for the configured minimum.

    Returns:
        ``True`` if the answer may be shown without a low-confidence warning.

        The threshold comes from ``_resolve_threshold``, the same hardened
        resolver the cite-or-refuse gate uses, rather than from settings
        directly — it fails closed on a missing or nonsensical configuration,
        and two readers of one setting could otherwise disagree about what the
        live threshold is.
    """
    from app.ai.guardrails.cite_or_refuse import _resolve_threshold

    return breakdown.overall >= _resolve_threshold(threshold)
