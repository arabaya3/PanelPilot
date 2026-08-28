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
from app.ai.retrieval.embedding import embed_query as _embed_query
from app.ai.retrieval.mappings import VerificationStatus
from app.ai.retrieval.query_classifier import classify_query
from app.core.config import get_settings
from app.models.schemas.retrieval_config import BlendWeights, QueryType, RetrievalConfig
from app.models.schemas.search import Citation, RetrievedPassage, SearchFilters

# The fallback blend, carried by the named server-side pipeline. Every query
# issued through this module overrides it with the weights for that query's
# type (see RetrievalConfig); this pair only applies to a search that somehow
# reaches the cluster without one, so it is deliberately middle-of-the-road
# rather than tuned for any particular query type.
#
# Any weights are only meaningful because the legs are min-max normalised to
# [0, 1] BEFORE they are combined. Summing raw scores would be meaningless:
# cosine similarity is bounded at 1.0 while BM25 is unbounded, so a "0.4"
# vector weight would in practice contribute a few percent.
_DEFAULT_BLEND = BlendWeights(bm25=0.6, vector=0.4)

# Search pipeline performing that normalisation. Registered once per cluster by
# ``ensure_search_pipeline`` and referenced by name on every query.
SEARCH_PIPELINE = "panelpilot-hybrid"


def search_pipeline_body() -> dict[str, Any]:
    """Return the fallback search-pipeline definition.

    Returns:
        A body for ``PUT /_search/pipeline/<name>``, carrying the default
        blend. Per-query-type pipelines come from ``blend_pipelines``.
    """
    return blend_pipeline_body(_DEFAULT_BLEND)


def retrieval_config_from_settings() -> RetrievalConfig:
    """Build a retrieval config seeded from the environment.

    ``RETRIEVAL_TOP_K`` and ``RETRIEVAL_MIN_SCORE`` are documented environment
    variables, so they must still take effect — but the query path reads only
    the config. This is the single place the two meet, so there is one value
    in play rather than two that can silently disagree.

    Returns:
        A config carrying the configured top_k and score floor, with the
        default per-query-type blend weights.
    """
    settings = get_settings()
    return RetrievalConfig(
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
    )


def pipeline_name_for(query_type: QueryType) -> str:
    """Return the search pipeline carrying one query type's blend weights.

    A pipeline per query type, referenced by name on the request.

    The alternative — a pipeline definition inline in the request body — would
    avoid registering anything, but whether OpenSearch honours a body-level
    ``search_pipeline`` is version-dependent and was not verifiable here. If it
    were ignored, every query would silently run with no normalisation at all:
    the legs would be summed un-normalised and the vector leg would contribute
    almost nothing, while every test still passed. The named-pipeline form is
    documented and its effect is observable, so it is the one used.

    Args:
        query_type: The classified query type.

    Returns:
        The pipeline name.
    """
    return f"{SEARCH_PIPELINE}-{query_type.value}"


def blend_pipeline_body(weights: BlendWeights) -> dict[str, Any]:
    """Return a pipeline definition applying one blend.

    Args:
        weights: The blend weights.

    Returns:
        A body for ``PUT /_search/pipeline/<name>``.
    """
    return {
        "description": f"Min-max normalise each hybrid leg, then blend {weights.as_pipeline_weights()}.",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        # Order matches the `queries` list in _build_query. If
                        # these disagree each weight lands on the wrong leg,
                        # which reads as a tuning problem and is not one.
                        "parameters": {"weights": weights.as_pipeline_weights()},
                    },
                }
            }
        ],
    }


def blend_pipelines(config: RetrievalConfig) -> dict[str, dict[str, Any]]:
    """Return every pipeline a config needs, keyed by name.

    Args:
        config: The retrieval configuration.

    Returns:
        One pipeline per query type, plus the default fallback. Registered
        together at index setup so a query never references a pipeline that
        does not exist — which OpenSearch answers with an error, not with a
        silent fallback.
    """
    pipelines = {
        pipeline_name_for(query_type): blend_pipeline_body(config.weights_for(query_type))
        for query_type in QueryType
    }
    pipelines[SEARCH_PIPELINE] = blend_pipeline_body(_DEFAULT_BLEND)
    return pipelines


def _build_query(
    *,
    query: str,
    vector: list[float],
    filters: SearchFilters | None,
    top_k: int,
    verified_only: bool = True,
) -> dict[str, Any]:
    """Build the hybrid request body.

    Both legs run in one request. This function does not weight them — the
    blend is applied by the search pipeline, whose weights depend on the query
    type (see ``RetrievalConfig``). A passage strong on either signal surfaces,
    and one strong on both outranks it.

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
    config: RetrievalConfig | None = None,
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
        config: Retrieval tuning. Defaults to the tuned configuration.

    Returns:
        Ranked passages, each carrying its citation.
    """
    # RetrievalConfig is the single source of tuning values. The settings
    # fallback that used to live here is gone: two places holding top_k meant
    # a re-tune could change one and leave the other, and nothing would say so.
    resolved = config or retrieval_config_from_settings()
    resolved_top_k = top_k if top_k is not None else resolved.top_k
    resolved_min_score = min_score if min_score is not None else resolved.min_score
    # The blend depends on what is being asked: a fault code wants the lexical
    # leg, a described symptom wants the semantic one. One global weight serves
    # whichever is more common and underserves the other.
    query_type = classify_query(query)

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

    # Exactly one pipeline reference, by name, as a query parameter. Both a
    # parameter and a body-level definition would leave which applies to
    # precedence rules nobody here verified.
    response = get_client().search(
        index=resolve_index(target),
        body=body,
        params={"search_pipeline": pipeline_name_for(query_type)},
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
    config: RetrievalConfig | None = None,
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
        config: Retrieval tuning, including the per-query-type blend weights.
            Defaults to the tuned configuration.

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
        config=config,
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

    Raises:
        EmbeddingError: If no provider is configured, or embedding fails.

    A thin delegation kept in place rather than removed: every retrieval test
    monkeypatches this name, and moving the seam would rewrite those tests for
    no benefit. The width check and the refusal-over-zero-vector behaviour live
    in `app.ai.retrieval.embedding`.
    """
    return _embed_query(query)
