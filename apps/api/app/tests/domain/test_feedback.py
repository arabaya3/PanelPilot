"""Tests for the post-launch feedback loop.

The acceptance criterion: a flagged answer reliably appears in the
verification queue with the original question, answer, and context attached.

The word doing the work is *original*. AI-014's edge case is explicit — the
context must be captured at flag time, not re-derived later, because retrieval
over a growing index returns different passages for the same query as the
corpus changes. So the central test here changes what retrieval would return
*after* the flag is recorded, and asserts the stored context did not move.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.feedback import (
    ORIGIN_CRAWL,
    ORIGIN_USER_FLAG,
    FeedbackError,
    context_for,
    flag_answer,
    flagged_items,
)
from app.models.schemas.search import Citation, RetrievedPassage
from app.models.tables.base import Base
from app.models.tables.diagnostics import DiagnosticSessionRow, DiagnosticTurnRow
from app.models.tables.escalation import FlaggedAnswerRow
from app.models.tables.ingestion import VerificationItemRow
from app.models.tables.tenant import TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: the flag and its queue entry are written in one transaction",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    """A Postgres engine with the schema created."""
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="session")
def _session(engine: Engine) -> Iterator[Session]:
    """A clean slate for one test."""
    with sessionmaker(bind=engine)() as session:
        session.execute(
            text(
                "TRUNCATE verification_items, flagged_answers, "
                "diagnostic_turns, diagnostic_sessions CASCADE"
            )
        )
        session.commit()
        yield session
        session.rollback()


def _passage(chunk_id: str, body: str, *, score: float = 0.9) -> RetrievedPassage:
    """Build a retrieved passage.

    Args:
        chunk_id: Its id.
        body: Its text.
        score: Its retrieval score.

    Returns:
        The passage.
    """
    return RetrievedPassage(
        id=chunk_id,
        text=body,
        score=score,
        citation=Citation(
            document_id="doc-1",
            document_title="ABB S200 catalogue",
            manufacturer="abb",
            page=27,
            section="4.2.1",
        ),
    )


def _a_turn(session: Session, *, tenant_slug: str = "feedback-tests") -> DiagnosticTurnRow:
    """Create a diagnostic turn to flag.

    Args:
        session: Open session.
        tenant_slug: Which tenant owns it.

    Returns:
        The turn.
    """
    tenant = session.execute(
        text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": tenant_slug}
    ).scalar_one_or_none()
    if tenant is None:
        row = TenantRow(slug=tenant_slug, name=tenant_slug)
        session.add(row)
        session.flush()
        tenant = row.id

    diag = DiagnosticSessionRow(tenant_id=tenant)
    session.add(diag)
    session.flush()

    turn = DiagnosticTurnRow(
        session_id=diag.id,
        position=0,
        question="What is the 40 C rating of an S201 B16?",
        answer="16 A at 40 C ambient.",
        refused=False,
        confidence=0.82,
    )
    session.add(turn)
    session.flush()
    return turn


def _tenant_of(session: Session, turn: DiagnosticTurnRow) -> uuid.UUID:
    """Return the tenant owning a turn.

    Args:
        session: Open session.
        turn: The turn.

    Returns:
        Its tenant id.
    """
    del session
    return turn.session.tenant_id


# --- the acceptance criterion -------------------------------------------------


@requires_postgres
def test_a_flag_reaches_the_queue_with_its_context(session: Session) -> None:
    turn = _a_turn(session)
    passages = [_passage("c1", "Rated 16 A at 40 C."), _passage("c2", "See table 6.")]

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=passages,
        reason="the rating is for 30 C, not 40 C",
    )
    session.commit()

    assert flag.question == turn.question
    assert flag.answer == turn.answer
    assert [p.id for p in context_for(flag)] == ["c1", "c2"]

    item = session.query(VerificationItemRow).one()
    assert item.flagged_answer_id == flag.id
    assert item.origin == ORIGIN_USER_FLAG
    assert item.status == "pending"


@requires_postgres
def test_the_context_is_captured_not_re_derived(session: Session) -> None:
    # AI-014's edge case, and the reason this table stores text rather than a
    # join. Retrieval for the same question returns different passages as the
    # index grows; a reviewer handed a fresh retrieval would be judging an
    # answer the user was never given, and would have no way to tell.
    turn = _a_turn(session)
    at_flag_time = [_passage("original-1", "What the user actually saw.")]

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=at_flag_time,
    )
    session.commit()

    # The corpus moves on: the same query would now return something else
    # entirely. Nothing about the stored flag may change as a result.
    later = [_passage("newly-indexed-9", "A passage that did not exist yet.")]
    assert later[0].id != at_flag_time[0].id

    session.expire_all()
    reloaded = session.get(FlaggedAnswerRow, flag.id)
    assert reloaded is not None
    stored = context_for(reloaded)

    assert [p.id for p in stored] == ["original-1"]
    assert stored[0].text == "What the user actually saw."


@requires_postgres
def test_the_answer_text_survives_the_turn_being_pruned(session: Session) -> None:
    # The question and answer are copied onto the flag, not read through
    # `turn_id`. Retention prunes sessions long before an accuracy problem is
    # fixed, and a flag whose text lived only on the turn would be emptied by
    # exactly that — losing the signal this whole table exists to keep.
    turn = _a_turn(session)
    original_question = turn.question
    original_answer = turn.answer

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=[_passage("c1", "text")],
    )
    session.commit()
    flag_id = flag.id

    session.execute(text("DELETE FROM diagnostic_sessions"))
    session.commit()
    session.expire_all()

    survivor = session.get(FlaggedAnswerRow, flag_id)
    assert survivor is not None
    assert survivor.question == original_question
    assert survivor.answer == original_answer
    # The link is gone, the evidence is not.
    assert survivor.turn_id is None


@requires_postgres
def test_the_full_passage_text_is_kept_not_just_the_citation(session: Session) -> None:
    # A citation alone sends the reviewer back to a document that may itself
    # have been re-crawled since. The passage is the evidence.
    turn = _a_turn(session)

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=[_passage("c1", "The exact sentence the answer came from.")],
    )
    session.commit()

    stored = context_for(flag)
    assert stored[0].text == "The exact sentence the answer came from."
    assert stored[0].score == 0.9
    assert stored[0].citation.page == 27


# --- distinguishable from pre-launch content ----------------------------------


@requires_postgres
def test_flagged_items_are_distinguishable_from_crawled_ones(session: Session) -> None:
    # "Tagged distinctly so the team can track post-launch accuracy trends
    # separately from initial-launch coverage." A rising flag rate and a large
    # crawl both grow the queue and mean opposite things.
    turn = _a_turn(session)
    session.add(VerificationItemRow(chunk_id="crawled-1", status="pending"))
    session.flush()

    flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=[_passage("c1", "text")],
    )
    session.commit()

    from_flags = flagged_items(session=session)

    assert len(from_flags) == 1
    assert from_flags[0].origin == ORIGIN_USER_FLAG
    assert session.query(VerificationItemRow).count() == 2


@requires_postgres
def test_a_crawled_item_defaults_to_the_crawl_origin(session: Session) -> None:
    # Every item carries an origin, including ones written by code that
    # predates this feature and never sets it.
    session.add(VerificationItemRow(chunk_id="c1", status="pending"))
    session.commit()

    row = session.query(VerificationItemRow).one()
    assert row.origin == ORIGIN_CRAWL


# --- refusals -----------------------------------------------------------------


@requires_postgres
def test_flagging_a_missing_turn_is_refused(session: Session) -> None:
    with pytest.raises(FeedbackError, match="no diagnostic turn"):
        flag_answer(
            session=session,
            turn_id=uuid.UUID(int=999),
            tenant_id=uuid.UUID(int=1),
            flagged_by_id=None,
            retrieved=[],
        )


@requires_postgres
def test_one_tenant_cannot_flag_anothers_answer(session: Session) -> None:
    # The turn id arrives from a client. Without this check, flagging would be
    # a read primitive for another tenant's questions and answers.
    turn = _a_turn(session, tenant_slug="tenant-a")
    other = TenantRow(slug="tenant-b", name="Other")
    session.add(other)
    session.flush()

    with pytest.raises(FeedbackError, match="does not belong"):
        flag_answer(
            session=session,
            turn_id=turn.id,
            tenant_id=other.id,
            flagged_by_id=None,
            retrieved=[],
        )


@requires_postgres
def test_a_flag_with_no_reason_is_still_recorded(session: Session) -> None:
    # Most people flag without explaining. Demanding a reason would cost more
    # signal than it gathers.
    turn = _a_turn(session)

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=[_passage("c1", "text")],
    )
    session.commit()

    assert flag.reason is None
    assert session.query(VerificationItemRow).count() == 1


@requires_postgres
def test_a_flag_on_an_answer_with_no_retrieval_is_recorded(session: Session) -> None:
    # A refusal has no retrieved passages, and is exactly the kind of answer
    # worth flagging. Storing an empty list is different from storing nothing.
    turn = _a_turn(session)

    flag = flag_answer(
        session=session,
        turn_id=turn.id,
        tenant_id=_tenant_of(session, turn),
        flagged_by_id=None,
        retrieved=[],
    )
    session.commit()

    assert context_for(flag) == []


@requires_postgres
def test_a_queue_item_must_still_identify_something(session: Session) -> None:
    # The check constraint was widened to admit a flag-sourced item. It must
    # not have been widened into accepting a row that identifies nothing.
    session.add(VerificationItemRow(status="pending", origin=ORIGIN_USER_FLAG))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- reading the context back -------------------------------------------------


def test_unreadable_context_is_refused_not_reported_as_empty() -> None:
    # A reviewer shown "no context" would conclude the answer was unsupported —
    # a specific and serious finding — when the truth is that the record is
    # corrupt. Those must not look the same.
    broken = FlaggedAnswerRow(
        tenant_id=uuid.UUID(int=1),
        question="q",
        answer="a",
        retrieved_context="{not json",
    )

    with pytest.raises(FeedbackError, match="unreadable context"):
        context_for(broken)


def test_context_that_is_not_a_list_is_refused() -> None:
    broken = FlaggedAnswerRow(
        tenant_id=uuid.UUID(int=1),
        question="q",
        answer="a",
        retrieved_context=json.dumps({"passages": []}),
    )

    with pytest.raises(FeedbackError, match="unreadable context"):
        context_for(broken)


def test_an_empty_context_reads_back_as_an_empty_list() -> None:
    # Distinct from unreadable: an answer genuinely built on no passages.
    empty = FlaggedAnswerRow(
        tenant_id=uuid.UUID(int=1),
        question="q",
        answer="a",
        retrieved_context="[]",
    )

    assert context_for(empty) == []


def test_the_two_origins_are_different_strings() -> None:
    # Pinned because the whole separate-trend requirement rests on them not
    # collapsing into one value after a refactor.
    assert ORIGIN_CRAWL != ORIGIN_USER_FLAG
