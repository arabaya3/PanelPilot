"""Hybrid (BM25 + kNN) retrieval over the document corpus.

The only module that builds an OpenSearch query. No route and no domain
function constructs one directly — they call ``search`` and get passages back.

**Staging is not reachable from here.** ``search`` always queries production.
Reviewing staged content goes through ``search_staging``, which is separately
named so that querying unverified content is a deliberate act that greps for
easily, never a default or a flag someone flips. See
docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from typing import Any

from app.ai.retrieval.client import IndexTarget, get_client, resolve_index
from app.ai.retrieval.mappings import VerificationStatus
from app.core.config import get_settings
from app.models.schemas.search import Citation, RetrievedPassage, SearchFilters

# Relative weight of the lexical and semantic legs. BM25 carries slightly more
# because engineers search with exact part numbers and fault codes, which
# lexical matching handles better than embeddings. AI-002 re-tunes these
# against this domain's real query patterns.
#
# These weights are only meaningful because the legs are min-max normalised to
# [0, 1] BEFORE they are combined. Summing raw scores would be meaningless:
# cosine similarity is bounded at 1.0 while BM25 is unbounded, so a "0.4"
# vector weight would in practice contribute a few percent.
BM25_WEIGHT = 0.6
VECTOR_WEIGHT = 0.4

# Search pipeline performing that normalisation. Registered once per cluster by
# ``ensure_search_pipeline`` and referenced by name on every query.
SEARCH_PIPELINE = "panelpilot-hybrid"


def search_pipeline_body() -> dict[str, Any]:
    """Return the search-pipeline definition that normalises then blends.

    Returns:
        A body for ``PUT /_search/pipeline/<name>``.
    """
    return {
        "description": "Min-max normalise each hybrid leg, then weighted arithmetic mean.",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        # Order matches the `queries` list in _build_query.
                        "parameters": {"weights": [BM25_WEIGHT, VECTOR_WEIGHT]},
                    },
                }
            }
        ],
    }


def _build_query(
    *,
    query: str,
    vector: list[float],
    filters: SearchFilters | None,
    top_k: int,
    verified_only: bool = True,
) -> dict[str, Any]:
    """Build the hybrid request body.

    Both legs run in one request and are fused by weighted score combination:
    the BM25 leg is boosted by ``BM25_WEIGHT`` and the kNN leg by
    ``VECTOR_WEIGHT``, so a passage strong on either signal surfaces while one
    strong on both outranks it.

    Args:
        query: Natural-language query text.
        vector: The embedded query.
        filters: Optional brand/model/doc-type restrictions.
        top_k: Number of passages to return.
        verified_only: Restrict to verified content. True for every answer
            path; False only for the reviewer path, whose entire job is to
            look at content that has not been verified yet.

    Returns:
        An OpenSearch request body.
    """
    # Filters restrict the candidate set without contributing to the score,
    # so narrowing by brand cannot reorder relevance within that brand.
    filter_clauses: list[dict[str, Any]] = []
    if verified_only:
        # Answer generation may only ever see verified content. Index-name
        # isolation alone is not enough: if unverified content ever reaches the
        # production index (a botched re-index, a restore), this clause is what
        # still keeps it out of an answer. See ADR 0001.
        #
        # It must NOT apply to the reviewer path: staging is unverified by
        # definition, so filtering it there returns nothing and the promotion
        # workflow stops working.
        filter_clauses.append({"term": {"verification_status": VerificationStatus.VERIFIED.value}})
    if filters:
        if filters.manufacturers:
            filter_clauses.append({"terms": {"brand": filters.manufacturers}})
        if filters.document_types:
            filter_clauses.append({"terms": {"doc_type": filters.document_types}})

    # A `hybrid` query, not two clauses in a bool. Each leg is scored
    # independently and normalised by the search pipeline before blending, so a
    # passage found only semantically still ranks and neither leg can drown the
    # other out on raw scale. The order here must match the pipeline weights.
    # The `hybrid` query takes no top-level filter, so each leg carries its
    # own copy — identical clauses, or the two legs would score different
    # candidate sets and the blend would be meaningless.
    return {
        "size": top_k,
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "bool": {
                            "must": [{"match": {"content": {"query": query}}}],
                            "filter": filter_clauses,
                        }
                    },
                    {
                        "knn": {
                            "content_vector": {
                                "vector": vector,
                                "k": top_k,
                                "filter": {"bool": {"filter": filter_clauses}},
                            }
                        }
                    },
                ],
            }
        },
    }


def _to_passages(response: dict[str, Any], *, min_score: float) -> list[RetrievedPassage]:
    """Convert a raw OpenSearch response into scored passages.

    Args:
        response: The decoded search response.
        min_score: Fused-score floor; hits below it are dropped.

    Returns:
        Passages in descending relevance order.
    """
    passages: list[RetrievedPassage] = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        score = float(hit.get("_score") or 0.0)
        if score < min_score:
            continue
        passages.append(
            RetrievedPassage(
                id=str(hit.get("_id")),
                text=source.get("content", ""),
                score=score,
                citation=Citation(
                    document_id=source.get("source_url", ""),
                    document_title=source.get("section") or source.get("model", ""),
                    manufacturer=source.get("brand", ""),
                    page=source.get("page"),
                    section=source.get("section"),
                ),
            )
        )
    return passages


def _search(
    *,
    query: str,
    target: IndexTarget,
    brand: str | None,
    model: str | None,
    filters: SearchFilters | None,
    top_k: int | None,
    min_score: float | None,
) -> list[RetrievedPassage]:
    """Run a hybrid search against one index.

    Private: the target is chosen by the two public wrappers, never by a
    caller, so production is not reachable by passing an argument.

    Args:
        query: Natural-language query text.
        target: Which index to query.
        brand: Optional brand restriction, merged into ``filters``.
        model: Optional model restriction.
        filters: Optional additional restrictions.
        top_k: Passages to return; defaults to the configured value.
        min_score: Score floor; defaults to the configured value.

    Returns:
        Ranked passages, each carrying its citation.
    """
    settings = get_settings()
    resolved_top_k = top_k if top_k is not None else settings.retrieval_top_k
    resolved_min_score = min_score if min_score is not None else settings.retrieval_min_score

    merged = filters or SearchFilters()
    if brand and brand not in merged.manufacturers:
        merged = merged.model_copy(update={"manufacturers": [*merged.manufacturers, brand]})

    body = _build_query(
        query=query,
        vector=embed_query(query),
        filters=merged,
        top_k=resolved_top_k,
        # Derived from the target, never passed in: the reviewer path is the
        # only one that may see unverified content, and it is the only caller
        # that reaches staging.
        verified_only=target is IndexTarget.PRODUCTION,
    )
    # `model` is a keyword field but not part of SearchFilters, so it is applied
    # here rather than widening that schema for one caller.
    if model:
        for leg in body["query"]["hybrid"]["queries"]:
            target_filter = (
                leg["bool"]["filter"]
                if "bool" in leg
                else leg["knn"]["content_vector"]["filter"]["bool"]["filter"]
            )
            target_filter.append({"term": {"model": model}})

    response = get_client().search(
        index=resolve_index(target), body=body, params={"search_pipeline": SEARCH_PIPELINE}
    )
    return _to_passages(response, min_score=resolved_min_score)


def search(
    query: str,
    brand: str | None = None,
    model: str | None = None,
    *,
    filters: SearchFilters | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedPassage]:
    """Search the production corpus.

    The function every answer path uses. It cannot be pointed at staging:
    there is no index argument, by design.

    Args:
        query: Natural-language query text.
        brand: Optional manufacturer restriction.
        model: Optional equipment model restriction.
        filters: Optional additional restrictions.
        top_k: Passages to return; defaults to the configured value.
        min_score: Fused-score floor; defaults to the configured value.

    Returns:
        Passages in descending relevance order, each carrying its source
        citation. Empty when nothing clears ``min_score``.
    """
    return _search(
        query=query,
        target=IndexTarget.PRODUCTION,
        brand=brand,
        model=model,
        filters=filters,
        top_k=top_k,
        min_score=min_score,
    )


def search_staging(
    query: str,
    brand: str | None = None,
    model: str | None = None,
    *,
    filters: SearchFilters | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedPassage]:
    """Search the staging corpus — reviewers only, never answer generation.

    Separately named on purpose. Making this a parameter of ``search`` would
    mean one wrong argument silently serves unverified content to an engineer,
    which is the failure mode ADR 0001 exists to prevent. Callers must also
    enforce the reviewer role; this function does not check authorisation.

    Args:
        query: Natural-language query text.
        brand: Optional manufacturer restriction.
        model: Optional equipment model restriction.
        filters: Optional additional restrictions.
        top_k: Passages to return; defaults to the configured value.
        min_score: Fused-score floor; defaults to the configured value.

    Returns:
        Passages from the staging index, in descending relevance order.
    """
    return _search(
        query=query,
        target=IndexTarget.STAGING,
        brand=brand,
        model=model,
        filters=filters,
        top_k=top_k,
        min_score=min_score,
    )


def embed_query(query: str) -> list[float]:
    """Embed a query string for the vector leg of the hybrid search.

    Args:
        query: Natural-language query text.

    Returns:
        The dense embedding vector.
    """
    raise NotImplementedError
