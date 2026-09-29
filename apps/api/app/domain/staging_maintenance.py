"""Keeping the corpus current: re-embedding staging, and spotting stale live content.

Two batch operations behind the ``reindex-staging`` and
``expire-stale-sources`` worker jobs. Neither can make content live or take it
down: re-embedding writes staging only, through ``stage_chunk``, and the
staleness check reads production and reports. Both of those are ADR 0001's
reviewed operations, and a scheduled job is the last place to put them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval.client import iter_production_chunks, iter_staging_chunks, stage_chunk
from app.ai.retrieval.embedding import embed_documents
from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow

logger = structlog.get_logger(__name__)

#: Chunks embedded per provider request. The provider bills and rate-limits per
#: request, so this is as large as keeps one request comfortably inside its
#: token ceiling for chunks of the size the chunker produces.
REEMBED_BATCH_SIZE = 32


def reembed_staging(
    *,
    session: Session,
    source_id: str | None = None,
    batch_size: int = REEMBED_BATCH_SIZE,
) -> int:
    """Recompute every staging chunk's embedding with the current model.

    The step after an embedding-model change. Text, citation fields and
    verification state are left exactly as they are; only ``content_vector``
    is replaced.

    Args:
        session: Open database session, to resolve a source to its documents.
        source_id: Limit to documents crawled from this source. ``None`` for
            the whole staging index.
        batch_size: Chunks per embedding request.

    Returns:
        How many chunks were re-embedded.

    Raises:
        EmbeddingError: If the provider fails or returns unusable vectors.
            Raised rather than skipped: a chunk left on the old model's vector
            is silently mis-ranked by every query, which is worse than a job
            that stops and says so. Already-written batches stay written;
            re-running the job is safe because it is idempotent.

    **Production is not touched.** Live chunks keep the vectors they were
    promoted with until a reviewer re-promotes them; promoting unchanged
    content rewrites its production copy. A model change is therefore a
    re-verification pass, not a background job — see ADR 0001.

    **This does not re-chunk.** Changing how documents are split means cutting
    them again from the source, which is what ``crawl`` does: re-run it for the
    source and the new chunks are staged and queued. Re-chunking from staging
    alone is not possible, because staging holds chunks, not documents.
    """
    content_hashes = None if source_id is None else _hashes_for_source(session, source_id)
    chunks = iter_staging_chunks(content_hashes=content_hashes)

    written = 0
    for batch in _batched(chunks, batch_size):
        vectors = embed_documents([str(body.get("content", "")) for _, body in batch])
        for (chunk_id, body), vector in zip(batch, vectors, strict=True):
            stage_chunk(chunk_id=chunk_id, document={**body, "content_vector": vector})
            written += 1

    logger.info("staging.reembedded", source_id=source_id, chunks=written)
    return written


@dataclass(frozen=True)
class SupersededChunk:
    """A live chunk whose source document has since been re-crawled with changes.

    Attributes:
        chunk_id: The live chunk.
        source_url: The document it came from.
        live_hash: The document hash the live chunk was cut from.
        latest_hash: The hash of the newest crawl of that document.
    """

    chunk_id: str
    source_url: str
    live_hash: str
    latest_hash: str


def find_superseded(*, session: Session) -> list[SupersededChunk]:
    """List live chunks whose document has a newer, different crawl.

    Args:
        session: Open database session.

    Returns:
        One entry per stale live chunk, ordered by document then chunk. Empty
        when production is current with every source.

    A live chunk carries its document's ``content_hash``; the crawl records
    every version of a document it has seen, with its URL. When the newest
    version of that URL has a different hash, the manufacturer changed the
    document after this chunk was verified — the live text may now contradict
    the source it cites.

    A live chunk whose hash matches no crawled document is not reported: it
    was not produced by this pipeline's crawl record, and guessing at its
    source would be a flag nobody can act on.
    """
    live = list(iter_production_chunks())
    live_hashes = {
        str(body["content_hash"]) for _, body in live if body.get("content_hash") is not None
    }
    if not live_hashes:
        return []

    url_by_hash = dict(
        session.execute(
            select(StagedDocumentRow.content_hash, StagedDocumentRow.source_url).where(
                StagedDocumentRow.content_hash.in_(live_hashes)
            )
        ).all()
    )
    latest_by_url = _latest_hash_by_url(session, set(url_by_hash.values()))

    stale: list[SupersededChunk] = []
    for chunk_id, body in live:
        live_hash = body.get("content_hash")
        url = url_by_hash.get(str(live_hash)) if live_hash is not None else None
        if url is None:
            continue
        latest = latest_by_url[url]
        if latest != live_hash:
            stale.append(
                SupersededChunk(
                    chunk_id=chunk_id,
                    source_url=url,
                    live_hash=str(live_hash),
                    latest_hash=latest,
                )
            )

    stale.sort(key=lambda item: (item.source_url, item.chunk_id))
    for item in stale:
        logger.warning(
            "production.superseded",
            chunk_id=item.chunk_id,
            source_url=item.source_url,
        )
    return stale


def _latest_hash_by_url(session: Session, urls: set[str]) -> dict[str, str]:
    """Return the hash of each URL's most recent crawl.

    Args:
        session: Open database session.
        urls: The documents to look up.

    Returns:
        URL to the content hash of its newest staged version.
    """
    rows = session.execute(
        select(
            StagedDocumentRow.source_url,
            StagedDocumentRow.content_hash,
            StagedDocumentRow.created_at,
        ).where(StagedDocumentRow.source_url.in_(urls))
    ).all()
    newest: dict[str, tuple[Any, str]] = {}
    for url, content_hash, created_at in rows:
        if url not in newest or created_at > newest[url][0]:
            newest[url] = (created_at, content_hash)
    return {url: content_hash for url, (_, content_hash) in newest.items()}


def _hashes_for_source(session: Session, source_id: str) -> list[str]:
    """Return the hashes of every document crawled from one source.

    Args:
        session: Open database session.
        source_id: The source.

    Returns:
        Document content hashes, possibly empty.
    """
    return list(
        session.execute(
            select(StagedDocumentRow.content_hash)
            .join(CrawlJobRow, CrawlJobRow.id == StagedDocumentRow.crawl_job_id)
            .where(CrawlJobRow.source_id == source_id)
        )
        .scalars()
        .all()
    )


def _batched(
    items: Iterator[tuple[str, dict[str, Any]]], size: int
) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    """Group a stream into lists of at most ``size``.

    Args:
        items: The stream.
        size: Maximum batch length; must be positive.

    Yields:
        Consecutive batches, the last possibly short.

    Raises:
        ValueError: If ``size`` is not positive.
    """
    if size < 1:
        raise ValueError("batch size must be positive")
    while batch := list(islice(items, size)):
        yield batch
