"""Staging pipeline: crawled documents to the staging index.

This module writes to the staging index only. It has no reference to the
production index target, deliberately — the promotion path is
``app.domain.promotion``. If you find yourself wanting to write production
content from here, read
docs/adr/0001-staging-vs-production-index.md before changing anything.
"""

from __future__ import annotations

from app.models.schemas.documents import CrawlResult, StagingBatchResult


def stage_documents(result: CrawlResult) -> StagingBatchResult:
    """Parse, chunk, embed, and index crawled documents into staging.

    Each staged document is enqueued for human verification; none becomes
    retrievable by answer generation as a result of this call.

    Args:
        result: Documents produced by a crawl run.

    Returns:
        Per-document staging outcomes, including any parse failures.
    """
    raise NotImplementedError
