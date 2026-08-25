"""Document chunking for indexing.

Chunk boundaries determine what a citation can point at, so changes here
invalidate previously indexed content: a chunking change requires a staging
re-index and re-verification, never an in-place production edit. See
docs/adr/0001-staging-vs-production-index.md.

**Structure, not character counts.** Chunks are built from the source PDF's own
headings, tables and numbered procedures, supplied by the parser as a
``StructureMap``. A fixed-size sliding window would split a parameter table
between two chunks, and an engineer retrieving half a derating table has no
signal that the other half exists — they would size a cable from it. Nothing
here re-derives structure from the raw text.
"""

from __future__ import annotations

import hashlib

from app.models.schemas.documents import DocumentChunk, SourceDocument
from app.models.schemas.structure import BlockKind, StructuralBlock, StructureMap

# Target band, in approximate tokens. Small enough that a retrieved chunk is
# mostly relevant to the query, large enough to keep a procedure's context.
TARGET_MAX_TOKENS = 500

# Rough token estimate. Deliberately not a real tokeniser — the band is a soft
# target, and taking a model dependency here would tie chunking to whichever
# embedding model is current. AI-002 revisits the band, not the method.
#
# Two rates, because one is badly wrong for tables. English technical prose runs
# close to four characters per token, but a numeric table tokenises near
# character-by-character: digits, decimal points and separators each cost a
# token. Measured against a real BPE tokeniser, a dense table came out roughly
# 3-4x higher than the prose rate predicted — which silently disabled the
# oversize reporting on exactly the content it exists to explain.
_CHARS_PER_TOKEN_PROSE = 4
_CHARS_PER_TOKEN_DENSE = 1.4

# Above this share of digits and punctuation, text is treated as dense.
_DENSE_RATIO = 0.25

# Chunks below this are poor retrieval units — a lone heading matches on
# embedding noise and carries no answer. They are merged forward instead.
TARGET_MIN_TOKENS = 200


def estimate_tokens(text: str) -> int:
    """Approximate the token count of a string.

    Args:
        text: The text to measure.

    Returns:
        An estimate in tokens, never below 1 for non-empty text.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    dense = sum(1 for c in stripped if c.isdigit() or c in "|,.;:/-()%")
    rate = (
        _CHARS_PER_TOKEN_DENSE if dense / len(stripped) >= _DENSE_RATIO else _CHARS_PER_TOKEN_PROSE
    )
    return max(1, int(len(stripped) / rate))


def _chunk_id(document_id: str, ordinal: int, text: str) -> str:
    """Build a stable identifier for a chunk.

    Derived from the content rather than a counter alone, so re-chunking an
    unchanged document produces the same ids and promotion can recognise it as
    unchanged instead of treating every chunk as new.

    Args:
        document_id: The parent document.
        ordinal: Position within the document.
        text: The chunk's text.

    Returns:
        A deterministic chunk id.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{document_id}#{ordinal:04d}-{digest}"


def _section_of(blocks: list[StructuralBlock]) -> str:
    """Return the section a group of blocks belongs to.

    Args:
        blocks: The blocks forming one chunk.

    Returns:
        The first non-empty section path among them.
    """
    for block in blocks:
        if block.section:
            return block.section
    return ""


def _flush(
    pending: list[StructuralBlock],
    *,
    document: SourceDocument,
    brand: str,
    model: str,
    doc_type: str,
    ordinal: int,
) -> DocumentChunk | None:
    """Turn accumulated blocks into a chunk.

    Args:
        pending: Blocks to emit.
        document: The parent document.
        brand: Manufacturer, carried onto every chunk.
        model: Equipment model.
        doc_type: Kind of source document.
        ordinal: Position within the document.

    Returns:
        The chunk, or ``None`` when there is nothing to emit.
    """
    if not pending:
        return None

    text = "\n\n".join(block.text.strip() for block in pending)
    tokens = estimate_tokens(text)
    atomic = [b for b in pending if b.kind.is_atomic]

    reason: str | None = None
    if tokens > TARGET_MAX_TOKENS and atomic:
        # Recorded rather than silently tolerated: a reviewer seeing a 900-token
        # chunk should be able to tell "this is an indivisible table" from
        # "the chunker is broken".
        kinds = sorted({b.kind.value for b in atomic})
        reason = f"contains atomic {', '.join(kinds)} that must not be split"

    chunk = DocumentChunk(
        id=_chunk_id(document.id, ordinal, text),
        document_id=document.id,
        text=text,
        # The page a citation points at is where the chunk STARTS. A chunk
        # spanning a page break cites its first page, which is where a reader
        # begins looking.
        page=pending[0].page,
        section=_section_of(pending),
        brand=brand,
        model=model,
        doc_type=doc_type,
        source_url=document.url,
        is_atomic=bool(atomic),
        oversized_reason=reason,
    )
    # Enforced here, not merely offered as a helper. This is the last point
    # before a chunk becomes a citation, and a blank field passed straight
    # through from the caller would otherwise reach the index unnoticed.
    blank = missing_citation_fields(chunk)
    if blank:
        raise ValueError(
            f"refusing to emit chunk {chunk.id}: blank citation fields: {', '.join(blank)}"
        )
    return chunk


def chunk_document(
    document: SourceDocument,
    structure_map: StructureMap,
    *,
    brand: str,
    model: str,
    doc_type: str,
    target_max_tokens: int = TARGET_MAX_TOKENS,
) -> list[DocumentChunk]:
    """Split a source document into citable chunks along its own structure.

    Blocks accumulate until adding the next would exceed the band, then a chunk
    is emitted. Two rules override the band:

    * **An atomic block is never split.** A table or numbered procedure becomes
      its own chunk whole, even at three times the target size. Half a table is
      not a smaller citation, it is a wrong one.
    * **A heading starts a new chunk.** The heading is what the section path is
      derived from, so keeping it with the content beneath it is what makes the
      citation resolvable. A heading directly preceding an atomic block travels
      with it instead of being orphaned into a one-token chunk.

    **On overlap.** The spec asks for "overlap only at true structural
    continuations". There is exactly one such continuation here: a section long
    enough that the band forces a split mid-section. Those chunks carry
    ``continues_from``/``continues_into`` links rather than duplicated text —
    the reader needs to know the passage continues, and a retrieval hit needs
    to be able to fetch its neighbour, but copying tokens into two chunks makes
    the same text match twice and inflates its apparent support. No overlap is
    produced anywhere else, because no other boundary is a continuation.

    Args:
        document: The parsed source document.
        structure_map: Structural blocks from the PDF parser, in reading order.
        brand: Manufacturer, carried onto every chunk.
        model: Equipment model the document covers.
        doc_type: Kind of source document.
        target_max_tokens: Upper end of the size band.

    Returns:
        Chunks in document order, each carrying a complete citation. Empty when
        the structure map holds no blocks.
    """
    chunks: list[DocumentChunk] = []
    pending: list[StructuralBlock] = []
    pending_tokens = 0

    def emit() -> None:
        nonlocal pending, pending_tokens
        chunk = _flush(
            pending,
            document=document,
            brand=brand,
            model=model,
            doc_type=doc_type,
            ordinal=len(chunks),
        )
        if chunk is not None:
            chunks.append(chunk)
        pending = []
        pending_tokens = 0

    for block in structure_map.blocks:
        block_tokens = estimate_tokens(block.text)

        if block.kind.is_atomic:
            # A heading immediately before an atomic block is DROPPED rather
            # than bundled into it. Bundling contaminated the atomic chunk with
            # text that is not part of the table or procedure, which defeats
            # the point of isolating it. The heading is not lost: it is where
            # block.section comes from, so the citation still names it.
            if pending and all(b.kind is BlockKind.HEADING for b in pending):
                pending = []
                pending_tokens = 0
            # Emitted alone so nothing else can push it over a boundary, and so
            # its oversized_reason describes only itself.
            emit()
            pending = [block]
            pending_tokens = block_tokens
            emit()
            continue

        # A heading starts a new chunk — and so does any change of section,
        # even without an intervening HEADING block. Parsers often assign a
        # subsection path without emitting every heading as its own block, and
        # grouping across that boundary produces a chunk whose section
        # describes only part of its own text.
        if pending and (block.kind is BlockKind.HEADING or block.section != pending[-1].section):
            emit()

        if pending and pending_tokens + block_tokens > target_max_tokens:
            emit()

        pending.append(block)
        pending_tokens += block_tokens

    emit()
    return _link_continuations(_merge_undersized(chunks))


def _merge_undersized(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    """Fold a chunk below the band into its neighbour where that is safe.

    A one-token heading chunk is a useless retrieval unit: it carries no
    answer and can still match on embedding noise. It is merged forward into
    the next chunk of the same section.

    **Atomic chunks are never merged, in either direction.** Tested on
    ``is_atomic`` rather than on size: a six-row table is as indivisible as a
    sixty-row one, and using ``oversized_reason`` as the proxy silently fused
    every small table and procedure into whatever sat next to it.

    Args:
        chunks: Chunks in document order.

    Returns:
        Chunks with undersized ones folded forward where possible.
    """
    merged: list[DocumentChunk] = []
    for chunk in chunks:
        # Merging only ever folds a heading-sized fragment into the chunk that
        # follows it IN THE SAME SECTION. Crossing a section boundary would
        # produce a chunk whose citation names one section while its text spans
        # two, which is the defect this merge was added next to, not one it may
        # introduce.
        if (
            merged
            and estimate_tokens(merged[-1].text) < TARGET_MIN_TOKENS
            # Guarded on is_atomic, NOT oversized_reason. A table under the
            # band has no oversized_reason, so the old check treated it as
            # prose and fused it with its neighbours -- two tables from
            # different pages became one chunk citing a third.
            and not merged[-1].is_atomic
            and not chunk.is_atomic
            and merged[-1].section == chunk.section
        ):
            previous = merged.pop()
            merged.append(
                chunk.model_copy(
                    update={
                        "text": previous.text + chr(10) + chr(10) + chunk.text,
                        # The citation follows the earlier page: that is where
                        # a reader starts looking.
                        "page": previous.page,
                        "id": previous.id,
                    }
                )
            )
            continue
        merged.append(chunk)
    return merged


def _link_continuations(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    """Link consecutive chunks that the band split mid-section.

    This is the spec's "overlap only at true structural continuations", done
    with links rather than duplicated text.

    Args:
        chunks: Chunks in document order.

    Returns:
        Chunks with continuation links set where a section was split.
    """
    linked = list(chunks)
    for index in range(len(linked) - 1):
        current, following = linked[index], linked[index + 1]
        if current.section and current.section == following.section:
            linked[index] = current.model_copy(update={"continues_into": following.id})
            linked[index + 1] = following.model_copy(update={"continues_from": current.id})
    return linked


def missing_citation_fields(chunk: DocumentChunk) -> list[str]:
    """Return the citation fields a chunk leaves blank.

    ``DocumentChunk`` makes these required at construction, so this catches the
    subtler case: a field present but empty, which validates fine and cites
    nothing.

    Args:
        chunk: The chunk to check.

    Returns:
        The offending field names. Empty when the chunk is fully citable.
    """
    required = ("section", "brand", "model", "doc_type", "source_url")
    return [name for name in required if not str(getattr(chunk, name)).strip()]
