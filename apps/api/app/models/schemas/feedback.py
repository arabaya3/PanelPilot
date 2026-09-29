"""Schemas for the post-launch feedback loop."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from app.models.schemas.search import RetrievedPassage

# Retrieval chunks are a few hundred words; ten thousand characters is several
# times the largest one. Anything longer is not a passage the user was shown.
MAX_FLAGGED_PASSAGE_CHARS = 10_000


def _bounded_passage(passage: RetrievedPassage) -> RetrievedPassage:
    """Refuse a client-echoed passage whose text is implausibly long.

    Args:
        passage: One passage as the client sent it back.

    Returns:
        The passage, unchanged.

    Raises:
        ValueError: If its text exceeds ``MAX_FLAGGED_PASSAGE_CHARS``.

    A validator here rather than a bound on ``RetrievedPassage.text``: that
    model is also what retrieval *produces*, and a limit there would make an
    unusually long chunk crash the answer path instead of being refused at
    the one place a client supplies the text.
    """
    if len(passage.text) > MAX_FLAGGED_PASSAGE_CHARS:
        raise ValueError(
            f"passage text is {len(passage.text)} characters; "
            f"the limit is {MAX_FLAGGED_PASSAGE_CHARS}"
        )
    return passage


class FlagRequest(BaseModel):
    """A user reporting an answer as wrong.

    The retrieved passages come from the client because they are what the user
    was actually shown. Re-running retrieval server-side would return whatever
    the index holds now, which is the one thing AI-014 says must not happen.
    """

    message_id: UUID
    reason: str | None = Field(default=None, max_length=2000)
    # Bounded: this is client-supplied and written to the database. An
    # unbounded list would let one request store an arbitrary amount. Each
    # passage's text is bounded too, or one passage could carry it instead.
    retrieved: list[Annotated[RetrievedPassage, AfterValidator(_bounded_passage)]] = Field(
        default_factory=list, max_length=50
    )


class FlagResponse(BaseModel):
    """Confirmation that a flag was recorded and queued."""

    flag_id: UUID
    queued: bool
