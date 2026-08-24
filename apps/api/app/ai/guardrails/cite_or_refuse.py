"""Cite-or-refuse guardrail.

The core safety invariant: PanelPilot answers from cited documentation or it
refuses. The decision is made in code, before and after the model call — never
delegated to the model's own judgement.

Two checkpoints:

1. ``require_evidence`` runs *before* generation. No qualifying passage means
   no prompt is built at all.
2. ``verify_citations`` runs *after* generation. Any claim whose citation does
   not resolve to a supplied passage invalidates the response.
"""

from __future__ import annotations

from app.models.schemas.diagnostics import GeneratedAnswer, VerifiedAnswer
from app.models.schemas.search import RetrievedPassage


def require_evidence(
    passages: list[RetrievedPassage],
    *,
    min_score: float | None = None,
) -> list[RetrievedPassage]:
    """Return the passages good enough to answer from, or refuse.

    Args:
        passages: Candidate passages from retrieval.
        min_score: Score floor; defaults to the configured value.

    Returns:
        The passages clearing the floor, in relevance order.

    Raises:
        InsufficientEvidenceError: If no passage clears the floor.
    """
    raise NotImplementedError


def verify_citations(
    answer: GeneratedAnswer,
    *,
    evidence: list[RetrievedPassage],
) -> VerifiedAnswer:
    """Check that every cited id in a generated answer resolves to evidence.

    Args:
        answer: The model's raw answer with its inline citations.
        evidence: The passages the answer was generated from.

    Returns:
        The answer with citations resolved to source documents.

    Raises:
        InsufficientEvidenceError: If the answer cites an id that was not
            supplied, or makes a factual claim carrying no citation.
    """
    raise NotImplementedError
