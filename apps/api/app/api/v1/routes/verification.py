"""Verification queue endpoints.

The three surfaces BE-007 names: a verifier's own batch, the label they apply
to one item, and the lead-only view of what escalated.

Authorisation is checked here because it is a property of the *caller*, not of
the queue: the domain functions take a verifier id and act on it, and deciding
whether this request may act as that verifier is the route's job. The domain
still enforces that a verifier only labels their own items, so a bug here
cannot let one person overwrite another's work.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import verification_queue as queue_domain
from app.models.schemas.auth import Role
from app.models.schemas.verification import (
    EscalationPage,
    LabelRequest,
    LabelResponse,
    QueueItem,
    QueuePage,
)
from app.models.tables.ingestion import VerificationItemRow

router = APIRouter()


def _to_item(row: VerificationItemRow) -> QueueItem:
    """Project a queue row onto its wire shape.

    Args:
        row: The database row.

    Returns:
        The item as the API presents it.
    """
    return QueueItem(
        id=row.id,
        chunk_id=row.chunk_id,
        status=row.status,
        assigned_at=row.assigned_at,
    )


@router.get("/queue/me", response_model=QueuePage)
def my_queue(session: SessionDep, user: CurrentUserDep) -> QueuePage:
    """Return the caller's outstanding batch."""
    rows = queue_domain.queue_for(session=session, verifier_id=UUID(user.id))
    return QueuePage(items=[_to_item(row) for row in rows])


@router.post("/items/{item_id}/label", response_model=LabelResponse)
def label_item(
    item_id: UUID,
    payload: LabelRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> LabelResponse:
    """Record the caller's label for one item.

    Raises:
        HTTPException: 404 if the item does not exist, 403 if it belongs to
            another verifier, 422 if an escalating label carries no note.
    """
    try:
        row = queue_domain.record_label(
            session=session,
            item_id=item_id,
            verifier_id=UUID(user.id),
            label=payload.label,
            note=payload.note,
        )
    except queue_domain.QueueError as exc:
        # Mapped by cause rather than collapsed into one code: "not yours" and
        # "does not exist" are different problems for whoever is debugging, and
        # a missing note is a client error the caller can fix.
        message = str(exc)
        if "no verification item" in message:
            raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
        if "not assigned" in message:
            raise HTTPException(status.HTTP_403_FORBIDDEN, message) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from exc

    session.commit()
    return LabelResponse(id=row.id, status=row.status, label=row.label)


@router.get("/escalations", response_model=EscalationPage)
def list_escalations(session: SessionDep, user: CurrentUserDep) -> EscalationPage:
    """Return every item awaiting lead review.

    Raises:
        HTTPException: 403 unless the caller holds the reviewer role.

    Gated because an escalation names content a verifier believed wrong, and
    the queue spans every verifier's work. AI-012's rule is that these are
    resolved by a lead rather than by whoever raised them, which only holds if
    the view is restricted to leads.
    """
    if not user.has_role(Role.REVIEWER):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{user.email} does not hold the reviewer role",
        )

    rows = queue_domain.escalations(session=session)
    return EscalationPage(items=[_to_item(row) for row in rows])
