"""OpenSearch client construction and index-name resolution.

The only module that knows an OpenSearch connection exists. Index names are
resolved through ``resolve_index`` so that no call site can hard-code
``"panelpilot-production"`` — the staging/production split stays enforceable in
one place.
"""

from __future__ import annotations

from enum import StrEnum

from opensearchpy import OpenSearch


class IndexTarget(StrEnum):
    """Which corpus a retrieval call is addressing."""

    STAGING = "staging"
    PRODUCTION = "production"


def get_client() -> OpenSearch:
    """Return the process-wide OpenSearch client.

    Returns:
        A configured, connection-pooled client.
    """
    raise NotImplementedError


def resolve_index(target: IndexTarget) -> str:
    """Map a logical target to the configured concrete index name.

    Args:
        target: Staging or production.

    Returns:
        The index name from settings for that target.
    """
    raise NotImplementedError
