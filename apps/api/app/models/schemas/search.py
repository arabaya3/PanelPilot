"""Search and retrieval schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A resolvable pointer back into a source document."""

    document_id: str
    document_title: str
    manufacturer: str
    page: int | None = None
    section: str | None = None


class RetrievedPassage(BaseModel):
    """One passage returned by retrieval, with its citation and score."""

    id: str
    text: str
    score: float
    citation: Citation


class SearchFilters(BaseModel):
    """Optional restrictions applied to a search."""

    manufacturers: list[str] = []
    document_types: list[str] = []
    published_after: str | None = None


class SearchRequest(BaseModel):
    """A search issued by a caller."""

    # Bounds match the diagnostic question's: longer is not a search query,
    # and an unbounded top_k is a request for the whole index.
    query: str = Field(max_length=4000)
    filters: SearchFilters | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class SearchResponse(BaseModel):
    """Ranked search results."""

    passages: list[RetrievedPassage]
    total: int
