"""Search and retrieval schemas."""

from __future__ import annotations

from typing import Literal

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
    #: Fused hybrid score, normalised within this result set: it ranks, it
    #: does not say how well anything matched. See ``similarity``.
    score: float
    citation: Citation
    #: Cosine between the query's and the passage's embeddings -- an absolute
    #: measure of match, unlike ``score``. ``None`` when it could not be
    #: measured. See ``app.ai.retrieval.relevance``.
    similarity: float | None = None
    #: The passage contains a fault code or parameter number the query names.
    anchored: bool = False


class SearchFilters(BaseModel):
    """Optional restrictions applied to a search."""

    manufacturers: list[str] = []
    document_types: list[str] = []
    # Accepted for the contract but not applied: the index carries no
    # publication date to filter on. A search naming it is not refused, and is
    # not narrowed either.
    published_after: str | None = None


class SearchRequest(BaseModel):
    """A search issued by a caller."""

    # Bounds match the diagnostic question's: longer is not a search query,
    # and an unbounded top_k is a request for the whole index.
    query: str = Field(max_length=4000)
    filters: SearchFilters | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    #: Which corpus to search. ``staging`` -- content no reviewer has verified
    #: yet -- is for reviewers only; everyone else searches what answers cite.
    corpus: Literal["production", "staging"] = "production"


class SearchResponse(BaseModel):
    """Ranked search results."""

    passages: list[RetrievedPassage]
    total: int
