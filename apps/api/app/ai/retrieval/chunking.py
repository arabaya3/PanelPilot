"""Document chunking for indexing.

Chunk boundaries determine what a citation can point at, so changes here
invalidate previously indexed content: a chunking change requires a staging
re-index and re-verification, never an in-place production edit.
"""

from __future__ import annotations

from app.models.schemas.documents import DocumentChunk, SourceDocument


def chunk_document(
    document: SourceDocument,
    *,
    target_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[DocumentChunk]:
    """Split a source document into citable, overlapping chunks.

    Splits on structural boundaries (headings, table rows) before falling back
    to token windows, so that a chunk maps to something a reader can locate in
    the original PDF.

    Args:
        document: The parsed source document.
        target_tokens: Preferred chunk size in tokens.
        overlap_tokens: Token overlap between adjacent chunks.

    Returns:
        Chunks in document order, each with page and section anchors.
    """
    raise NotImplementedError
