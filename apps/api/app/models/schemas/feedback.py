"""Schemas for the post-launch feedback loop."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.models.schemas.search import RetrievedPassage


class FlagRequest(BaseModel):
    """A user reporting an answer as wrong.

    The retrieved passages come from the client because they are what the user
    was actually shown. Re-running retrieval server-side would return whatever
    the index holds now, which is the one thing AI-014 says must not happen.
    """

    message_id: UUID
    reason: str | None = Field(default=None, max_length=2000)
    # Bounded: this is client-supplied and written to the database. An
    # unbounded list would let one request store an arbitrary amount.
    retrieved: list[RetrievedPassage] = Field(default_factory=list, max_length=50)


class FlagResponse(BaseModel):
    """Confirmation that a flag was recorded and queued."""

    flag_id: UUID
    queued: bool
