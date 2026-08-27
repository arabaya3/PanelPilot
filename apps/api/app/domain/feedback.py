"""Capturing answers users report as wrong, and routing them to verification.

Pre-launch verification covers what the team thought to check. Real usage
surfaces the rest — the question nobody anticipated, the manual whose table
was misread, the answer that is plausible and wrong. This is how that signal
is kept instead of being lost to a shrug.

The whole design rests on one property: **the context is copied at flag time,
never re-derived.** Retrieval over a growing index does not return the same
passages for the same question a month later. A reviewer handed a freshly-run
retrieval would be judging an answer the user was never given, reach a verdict
about content that was never shown, and — worst of the three — have no way to
tell that is what happened. So the question, the answer, and the passages are
written down as they stood, and nothing later reconstructs them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schemas.search import RetrievedPassage
from app.models.tables.diagnostics import DiagnosticTurnRow
from app.models.tables.escalation import FlaggedAnswerRow
from app.models.tables.ingestion import VerificationItemRow

logger = structlog.get_logger(__name__)

#: Origin recorded on queue items produced by the ingestion pipeline.
ORIGIN_CRAWL = "crawl"

#: Origin recorded on queue items produced by a user flagging an answer.
ORIGIN_USER_FLAG = "user-flag"


class FeedbackError(RuntimeError):
    """Raised when a flag cannot be recorded as asked."""


def flag_answer(
    *,
    session: Session,
    turn_id: UUID,
    tenant_id: UUID,
    flagged_by_id: UUID | None,
    retrieved: Sequence[RetrievedPassage],
    reason: str | None = None,
    now: datetime | None = None,
) -> FlaggedAnswerRow:
    """Record a flagged answer and queue it for verification.

    Args:
        session: Open database session. The caller commits.
        turn_id: The turn the user flagged.
        tenant_id: The flagging user's tenant.
        flagged_by_id: Who flagged it, when known.
        retrieved: The passages that backed the answer, **as they were shown**.
            Supplied by the caller rather than fetched here, because by the
            time this runs the index may no longer return them.
        reason: Optional free text from the user.
        now: Injected for tests.

    Returns:
        The stored flag.

    Raises:
        FeedbackError: If the turn does not exist, or belongs to another
            tenant.

    The turn's question and answer are copied onto the flag rather than
    referenced through ``turn_id``. The foreign key is ``SET NULL`` so a
    session pruned on retention does not take the flag with it, and a flag
    whose text lived only on the turn would be emptied by exactly that.
    """
    del now  # `created_at` is the flag time, set by the database.

    turn = session.get(DiagnosticTurnRow, turn_id)
    if turn is None:
        raise FeedbackError(f"no diagnostic turn {turn_id}")

    # Checked rather than trusted: the turn id arrives from a client, and
    # without this one tenant could flag — and thereby read — another's answer.
    if turn.session.tenant_id != tenant_id:
        raise FeedbackError(f"turn {turn_id} does not belong to this tenant")

    flag = FlaggedAnswerRow(
        tenant_id=tenant_id,
        turn_id=turn_id,
        flagged_by_id=flagged_by_id,
        question=turn.question,
        answer=turn.answer,
        retrieved_context=_serialise(retrieved),
        reason=reason,
    )
    session.add(flag)
    session.flush()

    # Queued in the same transaction as the flag. A flag that failed to reach
    # the queue would be a report nobody sees, which from the user's side is
    # indistinguishable from not having a flag button at all.
    session.add(
        VerificationItemRow(
            flagged_answer_id=flag.id,
            origin=ORIGIN_USER_FLAG,
            status="pending",
        )
    )

    logger.info(
        "feedback.flagged",
        flag_id=str(flag.id),
        turn_id=str(turn_id),
        passages=len(retrieved),
        has_reason=bool(reason),
    )
    return flag


def _serialise(passages: Sequence[RetrievedPassage]) -> str:
    """Render retrieved passages for storage.

    Args:
        passages: The passages that backed the answer.

    Returns:
        JSON text.

    Stored whole, including each passage's text and score. Storing only the
    citations would save space and lose the point: a reviewer needs to see the
    passage the answer was drawn from, and a citation alone sends them back to
    a document that may itself have been re-crawled since.
    """
    return json.dumps([passage.model_dump(mode="json") for passage in passages])


def context_for(flag: FlaggedAnswerRow) -> list[RetrievedPassage]:
    """Return the passages a flagged answer was built from.

    Args:
        flag: The stored flag.

    Returns:
        The passages, exactly as captured at flag time.

    Raises:
        FeedbackError: If the stored context cannot be read.

    Refused rather than returning an empty list. A reviewer shown "no context"
    would conclude the answer was unsupported — a specific and serious finding —
    when in fact the record is unreadable, which is a bug.
    """
    try:
        raw = json.loads(flag.retrieved_context)
    except json.JSONDecodeError as exc:
        raise FeedbackError(f"flag {flag.id} has unreadable context") from exc

    if not isinstance(raw, list):
        raise FeedbackError(f"flag {flag.id} has unreadable context")

    return [RetrievedPassage.model_validate(item) for item in raw]


def flagged_items(*, session: Session) -> list[VerificationItemRow]:
    """List queue items that came from user flags.

    Args:
        session: Open database session.

    Returns:
        Flag-sourced items, oldest first.

    Separate from the crawl-sourced queue so post-launch accuracy can be
    tracked as its own trend. A rising flag rate and a large crawl both grow
    the queue, and they mean opposite things.
    """
    return list(
        session.execute(
            select(VerificationItemRow)
            .where(VerificationItemRow.origin == ORIGIN_USER_FLAG)
            .order_by(VerificationItemRow.created_at)
        )
        .scalars()
        .all()
    )
