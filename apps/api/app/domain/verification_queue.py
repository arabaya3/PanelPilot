"""The verification queue: distributing chunks across ten verifiers, exactly once.

The operational backbone of the accuracy story. Ten engineers reviewing a
shared corpus need every chunk to land in exactly one queue — a chunk reviewed
twice wastes the scarcer of the two resources here (engineer attention), and a
chunk reviewed zero times is unverified content presented as verified, which is
the failure the whole pipeline exists to prevent.

Both halves of that are enforced by the database rather than by this module:

* **Never twice.** ``chunk_id`` is unique on ``verification_items``, so a
  second insert for the same chunk raises rather than creating a duplicate.
  The assignment job may legitimately run twice — a retry, an overlapping
  schedule, two operators — and "check whether it exists, then insert" has a
  window between the two statements. The constraint does not.
* **Never claimed twice.** Claiming is a conditional ``UPDATE`` that matches
  only rows still unassigned, and the row lock is held by the database for the
  duration. Two verifiers racing for the same item produce one winner and one
  miss, not two winners.

The alternative — reading the row, checking it in Python, then writing — is
correct only under a lock this application does not hold, and its failure mode
is silent: two people quietly labelling the same item, discovered when their
labels disagree.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.schemas.verification import VerificationLabel, escalates
from app.models.tables.ingestion import VerificationItemRow

logger = structlog.get_logger(__name__)

#: Status of an item nobody has labelled yet.
STATUS_PENDING = "pending"

#: Status of an item a verifier has labelled ``correct``, closing it.
STATUS_LABELED = "labeled"

#: Status of an item routed to lead-engineer review.
STATUS_ESCALATED = "escalated"

#: How many items one verifier is given in a day.
#:
#: A cap rather than a target. AI-013's edge case is a crawler run publishing a
#: large update and dumping thousands of chunks on ten people at once; a queue
#: that cannot be finished is one people stop opening, and the backlog is then
#: invisible rather than merely large. Unassigned chunks stay unassigned and
#: are picked up by the next day's run.
DAILY_BATCH_SIZE = 40


class QueueError(RuntimeError):
    """Raised when a queue operation cannot be completed as asked."""


def enqueue_chunks(
    *,
    session: Session,
    chunk_ids: Sequence[str],
    now: datetime | None = None,
) -> list[VerificationItemRow]:
    """Add chunks to the unassigned pool, skipping any already queued.

    Args:
        session: Open database session. The caller commits.
        chunk_ids: Chunks to queue.
        now: Injected for tests.

    Returns:
        The rows created, excluding chunks that were already present.

    Idempotent by construction, because the thing calling it is not reliably
    once-only: AI-013 wires this to the crawler's staging write, and a
    re-crawl, a retry, or a replayed event would otherwise queue the same
    chunk again. Each insert is attempted in a SAVEPOINT so a collision skips
    that chunk instead of losing the whole batch — the alternative, checking
    first, has a window between the check and the insert that a concurrent
    caller fits into.
    """
    del now  # Row creation time is `created_at`, set by the database.
    created: list[VerificationItemRow] = []

    for chunk_id in chunk_ids:
        row = VerificationItemRow(chunk_id=chunk_id, status=STATUS_PENDING)
        try:
            with session.begin_nested():
                session.add(row)
        except IntegrityError:
            # Already queued. Not an error: the caller is allowed to be
            # at-least-once, which is what makes this safe to wire to a crawler.
            logger.debug("verification_queue.already_queued", chunk_id=chunk_id)
            continue
        created.append(row)

    logger.info(
        "verification_queue.enqueued",
        requested=len(chunk_ids),
        created=len(created),
        skipped=len(chunk_ids) - len(created),
    )
    return created


def assign_daily_batches(
    *,
    session: Session,
    verifier_ids: Sequence[UUID],
    batch_size: int = DAILY_BATCH_SIZE,
    now: datetime | None = None,
) -> dict[UUID, int]:
    """Distribute unassigned items across verifiers in roughly equal batches.

    Args:
        session: Open database session. The caller commits.
        verifier_ids: The accounts to distribute across.
        batch_size: Maximum items per verifier for this run.
        now: Injected for tests.

    Returns:
        How many items each verifier was assigned.

    Raises:
        QueueError: If no verifiers were given.

    Rows are locked ``FOR UPDATE SKIP LOCKED``. To be precise about what that
    buys, since it is easy to overclaim: **correctness here comes from the row
    lock, not from SKIP LOCKED.** Two overlapping runs were measured both ways,
    with a barrier forcing genuine overlap, and neither double-assigns —
    without SKIP LOCKED the second run simply blocks until the first commits,
    then sees those rows as assigned and takes different ones. SKIP LOCKED
    makes the second run proceed immediately instead of waiting, which matters
    for a job that may be retried while a slow run is still going. It is a
    throughput choice, and no test here can distinguish it from plain
    ``FOR UPDATE`` on outcome.

    Assignment is capped per verifier rather than dividing the pool evenly: an
    even division of a large backlog gives everyone an unfinishable queue, and
    a queue nobody finishes is one nobody opens.
    """
    if not verifier_ids:
        raise QueueError("cannot assign a batch with no verifiers")

    moment = now or datetime.now(UTC)
    capacity = batch_size * len(verifier_ids)

    # SKIP LOCKED rather than a plain SELECT: two concurrent assignment runs
    # must not queue behind each other, and must not both see the same rows.
    pending = (
        session.execute(
            select(VerificationItemRow)
            .where(
                VerificationItemRow.assigned_to_id.is_(None),
                VerificationItemRow.status == STATUS_PENDING,
            )
            .order_by(VerificationItemRow.created_at)
            .limit(capacity)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )

    counts: dict[UUID, int] = dict.fromkeys(verifier_ids, 0)
    for index, row in enumerate(pending):
        verifier_id = verifier_ids[index % len(verifier_ids)]
        row.assigned_to_id = verifier_id
        row.assigned_at = moment
        counts[verifier_id] += 1

    logger.info(
        "verification_queue.assigned",
        verifiers=len(verifier_ids),
        assigned=len(pending),
        capacity=capacity,
    )
    return counts


def claim_item(
    *,
    session: Session,
    item_id: UUID,
    verifier_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Claim one unassigned item for a verifier.

    Args:
        session: Open database session. The caller commits.
        item_id: The item to claim.
        verifier_id: Who is claiming it.
        now: Injected for tests.

    Returns:
        ``True`` if this caller won the item, ``False`` if someone else held it.

    A single conditional ``UPDATE``, deliberately. The ``WHERE`` clause matches
    only rows still unassigned, so the database decides the winner while
    holding the row lock; two verifiers racing produce one ``True`` and one
    ``False``. Reading the row, checking it in Python, then writing would be
    correct only under a lock this application does not hold — and its failure
    mode is two people silently labelling the same item.
    """
    moment = now or datetime.now(UTC)
    # `session.execute` is typed as returning `Result`, which carries no
    # rowcount; an UPDATE actually returns a `CursorResult`, which does. The
    # count is the whole answer here — it is how the database reports which
    # caller won the race — so it is narrowed rather than ignored.
    result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        update(VerificationItemRow)
        .where(
            VerificationItemRow.id == item_id,
            VerificationItemRow.assigned_to_id.is_(None),
        )
        .values(assigned_to_id=verifier_id, assigned_at=moment)
    )

    won = result.rowcount == 1
    logger.info(
        "verification_queue.claim",
        item_id=str(item_id),
        verifier_id=str(verifier_id),
        won=won,
    )
    return won


def queue_for(
    *,
    session: Session,
    verifier_id: UUID,
) -> list[VerificationItemRow]:
    """List one verifier's outstanding items.

    Args:
        session: Open database session.
        verifier_id: Whose queue to read.

    Returns:
        Their unlabelled items, oldest first.
    """
    return list(
        session.execute(
            select(VerificationItemRow)
            .where(
                VerificationItemRow.assigned_to_id == verifier_id,
                VerificationItemRow.status == STATUS_PENDING,
            )
            .order_by(VerificationItemRow.assigned_at)
        )
        .scalars()
        .all()
    )


def record_label(
    *,
    session: Session,
    item_id: UUID,
    verifier_id: UUID,
    label: VerificationLabel,
    note: str = "",
) -> VerificationItemRow:
    """Record a verifier's label, escalating it when the rubric requires.

    Args:
        session: Open database session. The caller commits.
        item_id: The item being labelled.
        verifier_id: Who is labelling it.
        label: Their judgement.
        note: Their reasoning. Required for anything that escalates.

    Returns:
        The updated row.

    Raises:
        QueueError: If the item does not exist, is not assigned to this
            verifier, or escalates without a note.

    The routing rule comes from ``escalates`` rather than being restated here,
    so AI-012's rubric and this code cannot drift apart: an ``incorrect`` or
    ``uncertain`` label goes to a lead rather than closing, and the verifier
    who applied it does not get to resolve it.
    """
    row = session.get(VerificationItemRow, item_id)
    if row is None:
        raise QueueError(f"no verification item {item_id}")

    # Checked rather than assumed: the item id comes from a URL, and a verifier
    # labelling someone else's item would silently overwrite an assignment.
    if row.assigned_to_id != verifier_id:
        raise QueueError(f"item {item_id} is not assigned to {verifier_id}")

    if escalates(label) and not note.strip():
        # Refused rather than defaulted. A lead receiving "incorrect" with no
        # note has to redo the verification from scratch to find out what was
        # wrong, which is the work the label was supposed to save.
        raise QueueError(f"a {label.value} label requires a note")

    row.label = label.value
    row.notes = note
    row.status = STATUS_ESCALATED if escalates(label) else STATUS_LABELED

    logger.info(
        "verification_queue.labeled",
        item_id=str(item_id),
        label=label.value,
        status=row.status,
    )
    return row


def escalations(*, session: Session) -> list[VerificationItemRow]:
    """List every item awaiting lead-engineer review.

    Args:
        session: Open database session.

    Returns:
        Escalated items, oldest first.

    Not filtered by verifier: a lead reviews everyone's escalations, and the
    acceptance criterion is that an escalated item is visible here within one
    polling cycle — which it is, because the status change and the read hit the
    same row with no intermediate queue to lag behind.
    """
    return list(
        session.execute(
            select(VerificationItemRow)
            .where(VerificationItemRow.status == STATUS_ESCALATED)
            .order_by(VerificationItemRow.created_at)
        )
        .scalars()
        .all()
    )
