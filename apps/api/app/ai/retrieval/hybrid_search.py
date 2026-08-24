"""Hybrid (BM25 + vector) retrieval over the document corpus."""

from __future__ import annotations

from app.ai.retrieval.client import IndexTarget
from app.models.schemas.search import RetrievedPassage, SearchFilters


def hybrid_search(
    *,
    query: str,
    target: IndexTarget,
    filters: SearchFilters | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedPassage]:
    """Retrieve passages ranked by fused lexical and semantic relevance.

    Args:
        query: Natural-language query text.
        target: Which index to search. Answer generation always passes
            the production target.
        filters: Optional manufacturer, document-type, or date restrictions.
        top_k: Number of passages to return; defaults to the configured value.
        min_score: Fused-score floor below which passages are dropped; defaults
            to the configured value.

    Returns:
        Passages in descending relevance order, each carrying its source
        citation. Empty when nothing clears ``min_score``.
    """
    raise NotImplementedError


def embed_query(query: str) -> list[float]:
    """Embed a query string for the vector leg of the hybrid search.

    Args:
        query: Natural-language query text.

    Returns:
        The dense embedding vector.
    """
    raise NotImplementedError
