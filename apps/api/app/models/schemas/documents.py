"""Source document, chunk, and crawl schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceDefinition(BaseModel):
    """An allow-listed documentation source to crawl."""

    id: str
    manufacturer: str
    seed_urls: list[str]
    max_depth: int = 2


class SourceDocument(BaseModel):
    """A fetched and parsed document, before chunking."""

    id: str
    source_id: str
    title: str
    url: str
    content_hash: str
    text: str


class DocumentChunk(BaseModel):
    """A citable slice of a source document.

    Every field BE-003's index mapping marks required is non-optional here, so
    an incomplete chunk cannot be constructed in the first place. ``page`` and
    ``section`` were previously optional; a chunk that cannot name where it
    came from is not citable, which is the one thing a chunk exists to be.
    """

    id: str
    document_id: str
    text: str
    # Citation anchors. Required, not optional.
    page: int = Field(ge=1)
    section: str
    brand: str
    model: str
    doc_type: str
    source_url: str
    # True when this chunk holds a table or numbered procedure. Independent of
    # size: a SMALL table is just as indivisible as a large one, and inferring
    # atomicity from oversized_reason meant every table under the band merged
    # into its neighbours like prose.
    is_atomic: bool = False
    # Set when the chunk exceeds the target band BECAUSE it holds an atomic
    # structure. A reporting field, never a correctness signal.
    oversized_reason: str | None = None
    # Set when the band forced a split mid-section -- the one "true structural
    # continuation" there is. Linked rather than overlapped: duplicating text
    # into both chunks makes the same passage match twice and inflates how well
    # supported an answer looks.
    continues_from: str | None = None
    continues_into: str | None = None


class CrawlOutcome(BaseModel):
    """Per-URL result of a crawl."""

    url: str
    fetched: bool
    skipped_reason: str | None = None


class CrawlResult(BaseModel):
    """Everything one crawl run produced."""

    source_id: str
    documents: list[SourceDocument]
    outcomes: list[CrawlOutcome]


class StagingBatchResult(BaseModel):
    """Per-document outcome of a staging run."""

    staged_document_ids: list[str]
    failures: dict[str, str] = {}
