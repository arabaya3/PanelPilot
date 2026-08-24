"""Source document, chunk, and crawl schemas."""

from __future__ import annotations

from pydantic import BaseModel


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
    """A citable slice of a source document."""

    id: str
    document_id: str
    text: str
    page: int | None = None
    section: str | None = None


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
