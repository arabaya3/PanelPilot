"""OpenSearch client construction and index-name resolution.

The only module that knows an OpenSearch connection exists. Index names are
resolved through ``resolve_index`` so that no call site can hard-code
``"panelpilot-production"`` — the staging/production split stays enforceable in
one place.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

from opensearchpy import OpenSearch

from app.core.config import get_settings


class IndexTarget(StrEnum):
    """Which corpus a retrieval call is addressing."""

    STAGING = "staging"
    PRODUCTION = "production"


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
        pool_maxsize=20,
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
    return name


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
