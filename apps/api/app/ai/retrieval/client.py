"""OpenSearch client construction and index-name resolution.

The only module that knows an OpenSearch connection exists. Index names are
resolved through ``resolve_index`` so that no call site can hard-code
``"panelpilot-production"`` — the staging/production split stays enforceable in
one place.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

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
