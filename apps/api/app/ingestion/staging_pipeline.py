"""Staging pipeline: crawled documents to the staging index.

This module writes to **no index at all**. It turns crawled documents into
validated chunks and hands them back; the caller in ``app.domain`` decides
where they go, and ``app.domain.promotion`` remains the only path into
production. See docs/adr/0001-staging-vs-production-index.md.

That is stricter than it first looks, and deliberately so. An earlier version
of this module imported ``index_chunk`` and ``IndexTarget.STAGING`` and wrote
staging itself, which reads as safe — it never named production. The
architecture test refuses it anyway, because a module holding an ``IndexTarget``
can reach production by ordinal (``list(IndexTarget)[1]``) or by ``getattr``,
and a review defeated the earlier name-based check exactly that way. Denying
ingestion the *capability* is what makes the isolation structural rather than
trusted.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from app.ai.retrieval.chunking import chunk_document, missing_citation_fields
from app.models.schemas.documents import (
    CrawlResult,
    DocumentChunk,
    SourceDocument,
    StagingBatchResult,
)
from app.models.schemas.structure import StructureMap

#: Supplies the structural blocks for one document.
#:
#: Taken as a parameter rather than derived here, and that is a deliberate
#: refusal rather than an oversight. ``app.models.schemas.structure`` is
#: explicit that the map comes from the PDF parser's layout and heading
#: extraction, and that chunking "never re-derives structure from the text
#: itself — guessing where a table starts by counting pipes is exactly the
#: failure this design avoids".
#:
#: No PDF parser exists in this repository and no dependency provides one, so
#: there is nothing to call. Writing a text-shape heuristic here would satisfy
#: the type and violate the design it depends on: a mis-detected table
#: boundary produces half a parameter table presented as a whole one, which is
#: the precise failure the atomic-block rule was written to prevent.
#:
#: See the gap recorded against BE-005 in docs/tasks/adan-lane.md.
StructureExtractor = Callable[[SourceDocument], StructureMap]

logger = structlog.get_logger(__name__)


def chunk_body(
    chunk: DocumentChunk, *, content_vector: list[float] | None = None
) -> dict[str, object]:
    """Render a chunk as the index document body.

    Args:
        chunk: The chunk to write.
        content_vector: The chunk's dense embedding, when one has been
            computed. Passed in rather than computed here: an architecture
            rule denies this package any import from ``app.ai.retrieval``, so
            the embedder is the caller's to hold — the same shape as
            ``extract_structure``.

    Returns:
        The body ``index_chunk`` expects. Every citation field is carried
        through rather than defaulted — ``index_chunk`` refuses a null one, and
        quietly substituting a placeholder here would defeat that check by
        making the field present and meaningless.

        The vector is **omitted entirely when absent**, never written as an
        empty list or zeros. A zero vector is a legal kNN input that matches
        arbitrary neighbours, so a chunk indexed without a real embedding would
        be silently retrievable and wrong; a missing field is visibly missing.
    """
    body: dict[str, object] = {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "text": chunk.text,
        "page": chunk.page,
        "section": chunk.section,
        "brand": chunk.brand,
        "model": chunk.model,
        "doc_type": chunk.doc_type,
        "source_url": chunk.source_url,
        "is_atomic": chunk.is_atomic,
        "verification_status": "pending",
    }
    if content_vector is not None:
        body["content_vector"] = content_vector
    return body


def prepare_documents(
    result: CrawlResult,
    *,
    extract_structure: StructureExtractor,
    brand: str | None = None,
) -> tuple[StagingBatchResult, dict[str, list[dict[str, object]]]]:
    """Parse and chunk crawled documents into index-ready bodies.

    Nothing here is written anywhere. Each returned document is ready to be
    staged for human verification, and none becomes retrievable by answer
    generation as a result of this call.

    Args:
        result: Documents produced by a crawl run.
        extract_structure: Produces one document's structural blocks. Required
            rather than defaulted — see ``StructureExtractor`` for why this
            module will not derive structure from text itself.
        brand: Manufacturer to record on each chunk. Defaults to the source id
            when the caller does not supply one.

    Returns:
        The per-document outcomes, and the chunk bodies for each document that
        parsed completely, keyed by document id.
    """
    staged: list[str] = []
    failures: dict[str, str] = {}
    bodies: dict[str, list[dict[str, object]]] = {}

    for document in result.documents:
        try:
            chunks = _chunks_for(
                document,
                brand=brand or result.source_id,
                extract_structure=extract_structure,
            )
        except Exception as exc:
            # Recorded against the document rather than raised: a malformed PDF
            # in a library of hundreds should cost that document, not the
            # whole night's crawl.
            logger.warning(
                "staging.parse_failed", document_id=document.id, url=document.url, error=str(exc)
            )
            failures[document.id] = str(exc)
            continue

        if not chunks:
            failures[document.id] = "no chunks produced"
            continue

        incomplete = _first_incomplete(chunks)
        if incomplete is not None:
            # Refused before writing anything, so a document does not land
            # half-staged. A chunk missing its page or source_url is a chunk
            # that would surface later as an answer nobody can trace back.
            chunk_id, missing = incomplete
            failures[document.id] = f"chunk {chunk_id} missing citation fields: {missing}"
            logger.warning(
                "staging.incomplete_citation",
                document_id=document.id,
                chunk_id=chunk_id,
                missing=missing,
            )
            continue

        bodies[document.id] = [chunk_body(chunk) for chunk in chunks]
        staged.append(document.id)
        logger.info("staging.prepared", document_id=document.id, chunks=len(chunks))

    return StagingBatchResult(staged_document_ids=staged, failures=failures), bodies


def _chunks_for(
    document: SourceDocument,
    *,
    brand: str,
    extract_structure: StructureExtractor,
) -> list[DocumentChunk]:
    """Chunk one document along its own structure.

    Args:
        document: The crawled document.
        brand: Manufacturer to record on each chunk.
        extract_structure: Produces the document's structural blocks.

    Returns:
        The document's chunks.
    """
    return chunk_document(
        document,
        extract_structure(document),
        brand=brand,
        # Model and document type are not knowable from the crawl alone; the
        # verification queue is where a reviewer supplies them. Recorded as
        # unknown rather than guessed, because a wrong model on a chunk is a
        # citation that points at the wrong equipment.
        model="unknown",
        doc_type="manual",
    )


def _first_incomplete(chunks: list[DocumentChunk]) -> tuple[str, list[str]] | None:
    """Find the first chunk missing a citation field.

    Args:
        chunks: The document's chunks.

    Returns:
        The offending chunk id and its missing fields, or ``None``.
    """
    for chunk in chunks:
        missing = missing_citation_fields(chunk)
        if missing:
            return chunk.id, missing
    return None
