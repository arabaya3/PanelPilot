"""OpenSearch client construction and index-name resolution.

The only module that knows an OpenSearch connection exists. Index names are
resolved through ``resolve_index`` so that no call site can hard-code
``"panelpilot-production"`` — the staging/production split stays enforceable in
one place.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

import structlog
from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk, scan

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


class IndexTarget(StrEnum):
    """Which corpus a retrieval call is addressing."""

    STAGING = "staging"
    PRODUCTION = "production"


#: Seconds before an OpenSearch request is abandoned.
OPENSEARCH_TIMEOUT_S = 10


@lru_cache(maxsize=1)
def get_client() -> OpenSearch:
    """Return the process-wide OpenSearch client.

    Returns:
        A configured, connection-pooled client.
    """
    settings = get_settings()
    auth = None
    if settings.opensearch_username and settings.opensearch_password:
        auth = (settings.opensearch_username, settings.opensearch_password.get_secret_value())

    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=auth,
        # TLS verification follows the scheme of the configured URL, so a
        # deployed https:// endpoint is verified and a local http:// one is not
        # silently "trusted" — there is nothing to trust.
        use_ssl=settings.opensearch_url.startswith("https://"),
        verify_certs=settings.opensearch_url.startswith("https://"),
        # At least the request thread pool (40): with 20, concurrent searches
        # past the twentieth opened throwaway connections and logged "pool is
        # full" instead of reusing one.
        pool_maxsize=40,
        # Explicit rather than the client default. Retrieval sits on the path
        # of a question someone is waiting for, and a search slower than this
        # is failed and refused rather than left holding the request.
        timeout=OPENSEARCH_TIMEOUT_S,
        max_retries=1,
        retry_on_timeout=False,
    )


def resolve_index(target: IndexTarget) -> str:
    """Map a logical target to the configured concrete index name.

    Args:
        target: Staging or production.

    Returns:
        The index name from settings for that target.
    """
    settings = get_settings()
    if target is IndexTarget.STAGING:
        return settings.opensearch_staging_index
    return settings.opensearch_production_index


def ensure_index(target: IndexTarget, *, recreate: bool = False) -> str:
    """Create the index for a target if it does not already exist.

    Both indices are created from the same mapping, so staging and production
    can never drift into answering the same query differently.

    Args:
        target: Staging or production.
        recreate: Drop and rebuild first. Never pass ``True`` against a live
            production index — a mapping change is a re-index, not an edit.

    Returns:
        The concrete index name.
    """
    from app.ai.retrieval.hybrid_search import blend_pipelines, retrieval_config_from_settings
    from app.ai.retrieval.mappings import index_mapping

    client = get_client()
    # One pipeline per query type, plus the fallback. The hybrid query is
    # scored by whichever it names; without a pipeline the legs are summed
    # un-normalised and the vector leg contributes almost nothing.
    #
    # Registered together so a query can never name a pipeline that does not
    # exist. Re-registering is idempotent, so a re-tune is a redeploy rather
    # than a migration.
    for name, definition in blend_pipelines(retrieval_config_from_settings()).items():
        client.transport.perform_request("PUT", f"/_search/pipeline/{name}", body=definition)
    name = resolve_index(target)
    if recreate and client.indices.exists(index=name):
        client.indices.delete(index=name)
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=index_mapping())
    else:
        _add_missing_fields(client, name, index_mapping()["mappings"]["properties"])
    return name


def _add_missing_fields(client: Any, name: str, wanted: dict[str, Any]) -> None:
    """Add fields the mapping declares but an existing index lacks.

    Adding a field is the one mapping change OpenSearch allows in place, and
    the index is `dynamic: strict`: without this, a field added to the
    mapping made every write to an index created before it fail. Changing an
    existing field is not attempted -- that is a re-index.
    """
    current = client.indices.get_mapping(index=name)[name]["mappings"].get("properties", {})
    missing = {field: spec for field, spec in wanted.items() if field not in current}
    if missing:
        client.indices.put_mapping(index=name, body={"properties": missing})
        logger.info("opensearch.mapping_extended", index=name, fields=sorted(missing))


def index_chunk(target: IndexTarget, *, chunk_id: str, document: dict[str, Any]) -> None:
    """Write one chunk, refusing anything with a null required field.

    The single write path into either index. Enforcing completeness here rather
    than in the caller is what makes "no schema field left null on ingest" a
    property of the system instead of a convention: a chunk missing its page or
    source_url would surface later as an answer that cannot be traced back.

    Args:
        target: Which index to write to.
        chunk_id: Stable document id.
        document: The chunk body, including ``content_vector``.

    Raises:
        ValueError: If any required field is absent or null.
    """
    from app.ai.retrieval.mappings import missing_required_fields

    missing = missing_required_fields(document)
    if missing:
        raise ValueError(
            f"refusing to index {chunk_id!r}: required fields missing or null: {', '.join(missing)}"
        )
    get_client().index(index=resolve_index(target), id=chunk_id, body=document)


def stage_chunk(*, chunk_id: str, document: dict[str, Any]) -> None:
    """Write one chunk into the STAGING index, and only ever staging.

    Args:
        chunk_id: Stable document id.
        document: The chunk body, including ``content_vector``.

    Raises:
        ValueError: If any required field is absent or null.

    Separate from ``index_chunk`` rather than a call with a different target,
    and it is the target that is the point: this function cannot address
    production. ``index_chunk`` takes an ``IndexTarget``, so a caller holding it
    is one argument away from publishing, which is why the architecture tests
    keep its call sites down to promotion.py alone. A crawl has to write
    somewhere, and giving the ingestion path a helper with no production
    spelling available is what lets it do that without widening the guard that
    protects the live corpus.

    The completeness check is the same one, deliberately. A chunk missing its
    page or source_url is unusable as a citation whether it is staged or live,
    and catching it at the staging write means a reviewer never sees an item
    that could not have been promoted anyway.
    """
    from app.ai.retrieval.mappings import missing_required_fields

    missing = missing_required_fields(document)
    if missing:
        raise ValueError(
            f"refusing to stage {chunk_id!r}: required fields missing or null: {', '.join(missing)}"
        )
    get_client().index(index=resolve_index(IndexTarget.STAGING), id=chunk_id, body=document)


def iter_staged_contents(
    *, brand: str | None = None, batch_size: int = 128
) -> Iterator[list[tuple[str, str]]]:
    """Read the STAGING index's chunk texts, a batch at a time.

    For re-embedding in place: the text is what a vector is computed from, and
    nothing else about a chunk changes when the embedding model does.

    Args:
        brand: Only chunks from this manufacturer; every chunk when ``None``.
        batch_size: Chunks per yielded batch, and per scroll page.

    Yields:
        ``(chunk_id, content)`` pairs, at most ``batch_size`` at a time.

    Staging only, like ``stage_chunk``, and for the same reason: a helper with
    no production spelling cannot be pointed at the live corpus.
    """
    client = get_client()
    index = resolve_index(IndexTarget.STAGING)
    # Nothing staged yet is an empty corpus, not an error: a fresh deployment
    # has no index until its first crawl.
    if not client.indices.exists(index=index):
        return
    query: dict[str, Any] = {"term": {"brand": brand}} if brand is not None else {"match_all": {}}
    batch: list[tuple[str, str]] = []
    for hit in scan(
        client,
        index=index,
        query={"query": query, "_source": ["content"]},
        size=batch_size,
    ):
        batch.append((str(hit["_id"]), str(hit["_source"].get("content", ""))))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def restage_vectors(vectors: dict[str, list[float]]) -> int:
    """Replace the ``content_vector`` of staged chunks, leaving the rest as is.

    A partial update rather than a re-index of the whole body: the citation
    fields, the ingester of record and the content hash stay exactly as the
    crawl wrote them, so re-embedding cannot disturb what promotion checks.

    Args:
        vectors: New vectors keyed by chunk id.

    Returns:
        How many chunks were updated.

    Raises:
        BulkIndexError: If any update fails; a chunk left on the old model's
            vector would be scored against queries embedded by the new one.
    """
    if not vectors:
        return 0
    index = resolve_index(IndexTarget.STAGING)
    updated, _errors = bulk(
        get_client(),
        (
            {
                "_op_type": "update",
                "_index": index,
                "_id": chunk_id,
                "doc": {"content_vector": vector},
            }
            for chunk_id, vector in vectors.items()
        ),
        refresh=True,
    )
    return int(updated)


def unstage_document(*, content_hash: str) -> int:
    """Remove one staged document's chunks, so it can be staged afresh.

    Args:
        content_hash: The document's hash, which every one of its chunks
            carries.

    Returns:
        How many chunks were removed.

    Staging only, like ``stage_chunk``: live chunks leave production the one
    way anything does, a reviewed retraction.
    """
    client = get_client()
    index = resolve_index(IndexTarget.STAGING)
    if not client.indices.exists(index=index):
        return 0
    response = client.delete_by_query(
        index=index,
        body={"query": {"term": {"content_hash": content_hash}}},
        refresh=True,
        conflicts="proceed",
        request_timeout=600,
    )
    return int(response.get("deleted", 0))


def retitle_staged(titles: dict[str, str]) -> int:
    """Set ``document_title`` on staged chunks that lack one, by source URL.

    For chunks staged before titles were recorded: a curated document's title
    is known from its URL, so it can be filled in without a re-crawl -- which
    would skip the document anyway, its content being unchanged.

    Args:
        titles: Title keyed by source URL.

    Returns:
        How many chunks were updated.

    Staging only, like ``stage_chunk``. Live chunks gain their title the one
    way anything reaches production: promotion, which copies the staged body.
    """
    if not titles:
        return 0
    client = get_client()
    index = resolve_index(IndexTarget.STAGING)
    if not client.indices.exists(index=index):
        return 0
    response = client.update_by_query(
        index=index,
        body={
            "query": {
                "bool": {
                    "filter": [{"terms": {"source_url": sorted(titles)}}],
                    "must_not": [{"exists": {"field": "document_title"}}],
                }
            },
            "script": {
                "lang": "painless",
                "source": "ctx._source.document_title = params.titles[ctx._source.source_url]",
                "params": {"titles": titles},
            },
        },
        refresh=True,
        conflicts="proceed",
        # One call over a whole corpus: thousands of chunks outlast the
        # client's default ten seconds, which was found live on 5,000.
        request_timeout=600,
    )
    return int(response.get("updated", 0))


@dataclass(frozen=True)
class PublishedSource:
    """One upstream document that live answers cite.

    Attributes:
        source_url: Where the document was fetched from.
        brand: The manufacturer its chunks carry.
        content_hashes: Every document hash its live chunks were staged under.
            Usually one; more when revisions were promoted over time.
    """

    source_url: str
    brand: str
    content_hashes: frozenset[str]


def published_sources(*, page_size: int = 500) -> list[PublishedSource]:
    """List the upstream documents the PRODUCTION index cites.

    Read-only: an aggregation over what is live, so a job can ask each source
    whether it still serves the revision that was verified.

    Args:
        page_size: Documents per aggregation page.

    Returns:
        One entry per source URL, in URL order; empty when production does not
        exist yet.
    """
    client = get_client()
    index = resolve_index(IndexTarget.PRODUCTION)
    # Nothing promoted yet means nothing live to go stale; the index is created
    # by the first promotion, so its absence is an ordinary state.
    if not client.indices.exists(index=index):
        return []
    found: list[PublishedSource] = []
    after: dict[str, Any] | None = None
    while True:
        composite: dict[str, Any] = {
            "size": page_size,
            "sources": [{"url": {"terms": {"field": "source_url"}}}],
        }
        if after is not None:
            composite["after"] = after
        response = client.search(
            index=index,
            body={
                "size": 0,
                "aggs": {
                    "documents": {
                        "composite": composite,
                        "aggs": {
                            "brand": {"terms": {"field": "brand", "size": 1}},
                            "hashes": {"terms": {"field": "content_hash", "size": 100}},
                        },
                    }
                },
            },
        )
        aggregation = response["aggregations"]["documents"]
        for bucket in aggregation["buckets"]:
            brands = bucket["brand"]["buckets"]
            found.append(
                PublishedSource(
                    source_url=str(bucket["key"]["url"]),
                    brand=str(brands[0]["key"]) if brands else "",
                    content_hashes=frozenset(
                        str(entry["key"]) for entry in bucket["hashes"]["buckets"]
                    ),
                )
            )
        after = aggregation.get("after_key")
        if not aggregation["buckets"] or after is None:
            return found
