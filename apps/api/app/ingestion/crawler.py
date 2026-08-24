"""Manufacturer documentation crawler.

Fetches and parses source documents. Writes nothing to any index directly — its
output goes to the staging pipeline. See
docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from app.models.schemas.documents import CrawlResult, SourceDefinition


def crawl_source(source: SourceDefinition, *, max_documents: int | None = None) -> CrawlResult:
    """Fetch documents from one allow-listed manufacturer source.

    Respects robots.txt and the configured concurrency limit, and skips
    documents whose content hash is already staged or in production.

    Args:
        source: The source definition, including seed URLs and crawl depth.
        max_documents: Optional cap for incremental or test runs.

    Returns:
        The fetched documents and per-URL outcomes.

    Raises:
        ValidationError: If the source is not on the allow-list.
    """
    raise NotImplementedError
