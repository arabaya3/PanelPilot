"""Connecting the crawler's staging write to the verification queue.

Without this, "continuously learning" and "always verified" are two systems
that happen to sit next to each other: chunks land in staging, and somebody
has to notice and ask for them to be reviewed. Nobody reliably does, so the
corpus grows a tail of unverified content that looks exactly like the verified
kind.

This module is the integration point, and it lives in ``app/domain`` rather
than in either of the two modules it joins. ``app/ingestion`` deliberately
holds no capability to write anywhere — an architecture rule enforces that it
cannot even reach an index-capable symbol — and the queue has no business
knowing what a crawl is. Having either import the other would put the seam
inside one of them, where it becomes an implicit dependency rather than a
stated one.

The hook is the seam AI-013 asks for: ``on_staging_chunk_created`` is a plain
callable the pipeline's caller invokes with the chunk ids it produced. What
happens next is this module's decision, not the crawler's.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.verification_queue import enqueue_chunks
from app.models.tables.ingestion import VerificationItemRow

logger = structlog.get_logger(__name__)

#: Most chunks one crawl run may add to the unassigned pool at once.
#:
#: AI-013's edge case: a manufacturer republishes a large catalogue and one run
#: produces thousands of chunks. Queueing all of them does not overload the
#: *queue* — the daily assignment already caps what each verifier receives —
#: but it does make the backlog unreadable, and it lets one noisy source crowd
#: out every other source's chunks for weeks, because assignment takes the
#: oldest first.
#:
#: So a run's contribution is bounded and the remainder is reported rather than
#: silently dropped. The next run re-presents the same chunks, and
#: ``enqueue_chunks`` is idempotent, so nothing is lost by deferring it.
MAX_CHUNKS_PER_RUN = 500

#: Called with the chunk ids one staging write produced.
StagingChunkHook = Callable[[Sequence[str]], None]


class QueuePopulationResult:
    """What one run contributed to the verification queue.

    Attributes:
        queued: Chunk ids newly added to the unassigned pool.
        already_present: How many were already queued, so this run skipped
            them. Expected, not exceptional — a re-crawl of unchanged content
            produces the same chunk ids.
        deferred: Chunk ids left for a later run because this one hit the cap.
    """

    __slots__ = ("already_present", "deferred", "queued")

    def __init__(
        self,
        *,
        queued: list[str],
        already_present: int,
        deferred: list[str],
    ) -> None:
        """Record one run's contribution.

        Args:
            queued: Newly queued chunk ids.
            already_present: How many were already queued.
            deferred: Chunk ids left for a later run.
        """
        self.queued = queued
        self.already_present = already_present
        self.deferred = deferred


def populate_queue_from_staging(
    *,
    session: Session,
    chunk_ids: Sequence[str],
    max_per_run: int = MAX_CHUNKS_PER_RUN,
    now: datetime | None = None,
) -> QueuePopulationResult:
    """Add a staging run's chunks to the verification queue.

    Args:
        session: Open database session. The caller commits.
        chunk_ids: Every chunk the run produced, in the order produced.
        max_per_run: Most chunks this run may contribute.
        now: Injected for tests.

    Returns:
        What was queued, what was already there, and what was deferred.

    Safe to call more than once with the same chunks. That is the property
    that makes it safe to wire to a crawler at all: a re-crawl, a retry, or a
    replayed event presents the same chunk ids again, and ``enqueue_chunks``
    relies on a unique constraint rather than a prior check, so a repeat is a
    no-op instead of a duplicate.
    """
    # The cap applies to what this run ADDS, not to what it is offered. Slicing
    # the input instead would take the same first `max_per_run` ids every time:
    # a later run re-presents them, they are already queued, the idempotent
    # skip absorbs them, and the tail beyond the cap is never reached. The
    # overflow would then be permanently deferred rather than deferred until
    # the next run, which is silent data loss with a reassuring name.
    already_queued = _already_queued(session=session, chunk_ids=chunk_ids)
    outstanding = [chunk_id for chunk_id in chunk_ids if chunk_id not in already_queued]

    accepted = outstanding[:max_per_run]
    deferred = outstanding[max_per_run:]

    created = enqueue_chunks(session=session, chunk_ids=accepted, now=now)
    created_ids = [row.chunk_id for row in created if row.chunk_id is not None]

    if deferred:
        # Logged at warning, not info. A run that produced more than the cap is
        # not an error, but a run that keeps hitting it every night means the
        # corpus is growing faster than ten people can verify it — which is a
        # staffing fact somebody needs to see, and it is invisible if the
        # overflow is dropped quietly.
        logger.warning(
            "ingestion_wiring.deferred",
            deferred=len(deferred),
            cap=max_per_run,
            produced=len(chunk_ids),
        )

    logger.info(
        "ingestion_wiring.populated",
        produced=len(chunk_ids),
        queued=len(created_ids),
        already_present=len(already_queued),
        deferred=len(deferred),
    )
    return QueuePopulationResult(
        queued=created_ids,
        already_present=len(already_queued),
        deferred=deferred,
    )


def _already_queued(*, session: Session, chunk_ids: Sequence[str]) -> set[str]:
    """Return which of these chunks are already in the queue.

    Args:
        session: Open database session.
        chunk_ids: Chunks to check.

    Returns:
        The subset already present.

    Read in one query rather than per chunk: a large run checking several
    hundred ids one at a time would spend longer on round trips than on the
    work. This is an optimisation of *which* chunks to offer, not a
    correctness check — `enqueue_chunks` still relies on the unique
    constraint, so a chunk queued between this read and that insert is
    absorbed rather than duplicated.
    """
    if not chunk_ids:
        return set()

    rows = session.execute(
        select(VerificationItemRow.chunk_id).where(
            VerificationItemRow.chunk_id.in_(list(chunk_ids))
        )
    ).scalars()
    return {chunk_id for chunk_id in rows if chunk_id is not None}


def make_staging_hook(
    *,
    session: Session,
    max_per_run: int = MAX_CHUNKS_PER_RUN,
) -> StagingChunkHook:
    """Build the hook a staging run calls when it has produced chunks.

    Args:
        session: Open database session. The caller commits.
        max_per_run: Most chunks one run may contribute.

    Returns:
        A callable taking the run's chunk ids.

    The seam AI-013 names. The crawler's caller holds one of these and invokes
    it; it does not know a verification queue exists, and the queue does not
    know a crawler does. Replacing this with a message publisher later changes
    this function and nothing upstream of it.
    """

    def hook(chunk_ids: Sequence[str]) -> None:
        """Queue one staging run's chunks for verification.

        Args:
            chunk_ids: Every chunk the run produced.
        """
        populate_queue_from_staging(
            session=session,
            chunk_ids=chunk_ids,
            max_per_run=max_per_run,
        )

    return hook


def chunk_ids_from_bodies(bodies: dict[str, list[dict[str, object]]]) -> list[str]:
    """Extract chunk ids from a staging run's prepared bodies.

    Args:
        bodies: Chunk bodies keyed by document id, as ``prepare_documents``
            returns them.

    Returns:
        Every chunk id, in document order.

    Raises:
        ValueError: If a body carries no usable ``chunk_id``.

    Refused rather than skipped. A body without an id is a chunk that cannot be
    queued, and dropping it silently produces exactly the failure this whole
    module exists to prevent: content in staging that no verifier will ever be
    shown, indistinguishable afterwards from content that was reviewed and
    passed.
    """
    ids: list[str] = []
    for document_id, chunks in bodies.items():
        for position, body in enumerate(chunks):
            raw = body.get("chunk_id")
            if not isinstance(raw, str) or not raw:
                raise ValueError(
                    f"document {document_id} chunk {position} has no chunk_id; "
                    "it could not be queued for verification"
                )
            ids.append(raw)
    return ids
