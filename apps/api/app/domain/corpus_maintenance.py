"""Keeping the corpus current: re-embedding staging, and noticing stale sources.

Two scheduled jobs that ADR 0001 relies on and nothing did:

- **Re-embedding staging.** An embedding model change leaves every staged
  vector scored in a space that queries no longer use. ``reindex_staging``
  recomputes them in place from each chunk's own text. Production is not
  touched: its chunks are re-embedded on their way through promotion, the one
  write path into it.
- **Stale sources.** A manufacturer can replace a manual under the same URL,
  or withdraw it. ``expire_stale_sources`` asks each source whether it still
  serves the revision production was verified against and records the answer
  in ``stale_documents``. It flags; it never retracts. Pulling a verified
  answer on a hash change alone would trade a possibly-stale answer for a
  certain refusal, and retraction is a reviewed operation.

Re-chunking is deliberately not here. It needs the original file, which is
not kept -- only the text of each chunk is -- so a chunking change means a
fresh crawl of the source, which stages the new chunks for review like any
other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval.client import (
    PublishedSource,
    iter_staged_contents,
    published_sources,
    restage_vectors,
)
from app.ai.retrieval.embedding import embed_documents
from app.core.errors import ValidationError
from app.domain.ingestion import EMBEDDING_BATCH_SIZE
from app.ingestion.crawler import DocumentCheck, check_documents
from app.ingestion.sources import CRAWLERS, crawler_for
from app.models.tables.ingestion import StaleDocumentRow

logger = structlog.get_logger(__name__)

#: ``stale_documents.status`` values.
OPEN = "open"
CLEARED = "cleared"


def reindex_staging(
    *,
    source_id: str | None = None,
    embed: Callable[[Sequence[str]], list[list[float]]] = embed_documents,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> int:
    """Re-embed every staged chunk from its own text, in place.

    Args:
        source_id: Only this allow-listed source's chunks; all when ``None``.
        embed: The embedding function; the configured provider by default.
        batch_size: Chunks per embedding request, the same bound a crawl uses.

    Returns:
        How many chunks were re-embedded.

    Raises:
        ValidationError: If ``source_id`` is not on the allow-list.
        EmbeddingError: If the provider fails. Fatal rather than skipped: a
            staging index half on the old model's vectors and half on the new
            one ranks chunks by which batch they were in.
    """
    brand: str | None = None
    if source_id is not None:
        crawler = crawler_for(source_id)
        if crawler is None:
            raise ValidationError(f"source {source_id!r} is not on the allow-list")
        brand = crawler.manufacturer

    total = 0
    for batch in iter_staged_contents(brand=brand, batch_size=batch_size):
        vectors = embed([content for _chunk_id, content in batch])
        total += restage_vectors(
            {chunk_id: vector for (chunk_id, _content), vector in zip(batch, vectors, strict=True)}
        )
        logger.info("reindex_staging.batch", source_id=source_id, reembedded=total)
    return total


@dataclass
class ExpiryReport:
    """What one ``expire_stale_sources`` run found.

    Attributes:
        checked: Live documents whose source answered.
        flagged: Source URLs newly flagged or still stale, with the reason.
        cleared: Source URLs flagged before that serve a verified revision again.
        unchecked: Source URLs that could not be asked, with why. Not stale --
            a source that is down says nothing about whether it changed.
    """

    checked: int = 0
    flagged: dict[str, str] = field(default_factory=dict)
    cleared: list[str] = field(default_factory=list)
    unchecked: dict[str, str] = field(default_factory=dict)


def expire_stale_sources(
    *,
    session: Session,
    check: Callable[[str, Iterable[str]], list[DocumentCheck]] = check_documents,
    sources: Callable[[], list[PublishedSource]] = published_sources,
    now: datetime | None = None,
) -> ExpiryReport:
    """Flag live documents whose upstream source changed or was withdrawn.

    Args:
        session: Open database session. The caller commits.
        check: Fetches URLs again; the crawler's ``check_documents`` by default.
        sources: Lists what production cites; injected by tests.
        now: The run's timestamp; the current time by default.

    Returns:
        What was checked, flagged, cleared, and left unchecked.
    """
    moment = now or datetime.now(UTC)
    report = ExpiryReport()

    by_brand = {crawler.manufacturer: source_id for source_id, crawler in CRAWLERS.items()}
    grouped: dict[str, list[PublishedSource]] = {}
    for published in sources():
        source_id = by_brand.get(published.brand)
        if source_id is None:
            # Live content from a source that is no longer allow-listed cannot
            # be fetched again -- the allow-list is what permits the request.
            report.unchecked[published.source_url] = "not-allow-listed"
            continue
        grouped.setdefault(source_id, []).append(published)

    existing = {
        row.source_url: row
        for row in session.scalars(
            select(StaleDocumentRow).where(
                StaleDocumentRow.source_url.in_(
                    [p.source_url for group in grouped.values() for p in group]
                )
            )
        )
    }

    for source_id, group in grouped.items():
        live = {p.source_url: p.content_hashes for p in group}
        for result in check(source_id, list(live)):
            hashes = live[result.url]
            if result.status == "fetched" and result.content_hash in hashes:
                report.checked += 1
                row = existing.get(result.url)
                if row is not None and row.status == OPEN:
                    row.status = CLEARED
                    row.last_checked_at = moment
                    report.cleared.append(result.url)
                continue
            if result.status == "fetched":
                reason = "superseded"
            elif result.status == "gone":
                reason = "withdrawn"
            else:
                report.unchecked[result.url] = result.status
                continue

            report.checked += 1
            report.flagged[result.url] = reason
            _flag(
                session,
                existing.get(result.url),
                source_id=source_id,
                source_url=result.url,
                reason=reason,
                published_hashes=hashes,
                upstream_hash=result.content_hash,
                moment=moment,
            )

    logger.info(
        "expire_stale_sources.done",
        checked=report.checked,
        flagged=len(report.flagged),
        cleared=len(report.cleared),
        unchecked=len(report.unchecked),
    )
    return report


def _flag(
    session: Session,
    row: StaleDocumentRow | None,
    *,
    source_id: str,
    source_url: str,
    reason: str,
    published_hashes: frozenset[str],
    upstream_hash: str | None,
    moment: datetime,
) -> None:
    """Record one stale source URL, updating its row if it has one.

    Args:
        session: Open database session.
        row: The URL's existing row, if any.
        source_id: The allow-listed source.
        source_url: The document URL.
        reason: ``superseded`` or ``withdrawn``.
        published_hashes: The hashes production was verified against.
        upstream_hash: What the source serves now; ``None`` when withdrawn.
        moment: The run's timestamp.

    A row that was cleared and goes stale again is re-opened with a fresh
    ``first_flagged_at``: it is a new change upstream, not the old one.
    """
    published = ",".join(sorted(published_hashes))
    if row is None:
        session.add(
            StaleDocumentRow(
                source_url=source_url,
                source_id=source_id,
                reason=reason,
                status=OPEN,
                published_hashes=published,
                upstream_hash=upstream_hash,
                first_flagged_at=moment,
                last_checked_at=moment,
            )
        )
        return
    if row.status != OPEN or row.upstream_hash != upstream_hash:
        row.first_flagged_at = moment
    row.status = OPEN
    row.reason = reason
    row.published_hashes = published
    row.upstream_hash = upstream_hash
    row.last_checked_at = moment
