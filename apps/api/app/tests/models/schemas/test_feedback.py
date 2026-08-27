"""Tests for `app/models/schemas/feedback.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The bounds matter here rather than being routine validation: this payload is
client-supplied and written straight to the database, so an unbounded field is
an unbounded write.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.schemas.feedback import FlagRequest, FlagResponse
from app.models.schemas.search import Citation, RetrievedPassage


def _passage(chunk_id: str = "c1") -> RetrievedPassage:
    """Build a retrieved passage.

    Args:
        chunk_id: Its id.

    Returns:
        The passage.
    """
    return RetrievedPassage(
        id=chunk_id,
        text="Rated 16 A at 40 C.",
        score=0.9,
        citation=Citation(
            document_id="doc-1",
            document_title="ABB S200",
            manufacturer="abb",
            page=27,
            section="4.2.1",
        ),
    )


def test_a_minimal_flag_needs_only_a_message_id() -> None:
    # Most people flag without explaining, and without the client having to
    # reconstruct anything. Requiring more would cost signal.
    request = FlagRequest(message_id=uuid.UUID(int=1))

    assert request.reason is None
    assert request.retrieved == []


def test_the_retrieved_context_is_carried_whole() -> None:
    # Not just citations: a reviewer needs the passage the answer was drawn
    # from, since the cited document may have been re-crawled since.
    request = FlagRequest(message_id=uuid.UUID(int=1), retrieved=[_passage()])

    assert request.retrieved[0].text == "Rated 16 A at 40 C."
    assert request.retrieved[0].score == 0.9


def test_the_context_list_is_bounded() -> None:
    # Client-supplied and written to the database. Unbounded here means one
    # request can store an arbitrary amount.
    with pytest.raises(ValidationError):
        FlagRequest(message_id=uuid.UUID(int=1), retrieved=[_passage()] * 51)


def test_the_reason_is_bounded() -> None:
    with pytest.raises(ValidationError):
        FlagRequest(message_id=uuid.UUID(int=1), reason="x" * 2001)


def test_a_reason_at_the_limit_is_accepted() -> None:
    # The boundary itself, so a later tightening is a deliberate change rather
    # than an accident.
    request = FlagRequest(message_id=uuid.UUID(int=1), reason="x" * 2000)

    assert request.reason is not None
    assert len(request.reason) == 2000


def test_a_message_id_must_be_a_uuid() -> None:
    # A UUID-shaped string is accepted and coerced, which is the useful
    # behaviour for a JSON client. Anything that is not a UUID is refused.
    coerced = FlagRequest(message_id=str(uuid.UUID(int=5)))
    assert coerced.message_id == uuid.UUID(int=5)

    with pytest.raises(ValidationError):
        FlagRequest(message_id="not-a-uuid")


def test_the_response_reports_the_flag_and_that_it_was_queued() -> None:
    # `queued` is stated rather than assumed: a flag recorded but not queued is
    # a report nobody sees, which the user cannot distinguish from success.
    response = FlagResponse(flag_id=uuid.UUID(int=9), queued=True)

    assert response.flag_id == uuid.UUID(int=9)
    assert response.queued
