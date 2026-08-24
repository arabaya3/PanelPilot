"""Human verification queue for staged documents.

Manages the queue itself — enqueue, claim, record a verdict. Acting on an
approval (writing to production) is not done here; that is
``app.domain.promotion.promote_document``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schemas.ingestion import (
    VerificationItem,
    VerificationVerdict,
)


def enqueue(*, session: Session, staged_document_id: str) -> VerificationItem:
    """Add a staged document to the verification queue.

    Args:
        session: Open database session.
        staged_document_id: Identifier of the staged document.

    Returns:
        The created queue item.
    """
    raise NotImplementedError


def claim_next(*, session: Session, reviewer_id: str) -> VerificationItem | None:
    """Claim the next unclaimed queue item for a reviewer.

    Args:
        session: Open database session.
        reviewer_id: Identifier of the claiming reviewer.

    Returns:
        The claimed item, or ``None`` when the queue is empty.
    """
    raise NotImplementedError


def record_verdict(
    *,
    session: Session,
    item_id: str,
    verdict: VerificationVerdict,
) -> VerificationItem:
    """Record a reviewer's decision on a queue item.

    Recording an approval does not publish anything; it makes the item eligible
    for promotion.

    Args:
        session: Open database session.
        item_id: Identifier of the queue item.
        verdict: Approval or rejection with the reviewer's notes.

    Returns:
        The updated queue item.

    Raises:
        NotFoundError: If the item does not exist.
        ValidationError: If the item was not claimed by this reviewer.
    """
    raise NotImplementedError
