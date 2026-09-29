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
    QueuePage,
    ResolveRequest,
)

router = APIRouter()


@router.get("/queue/me", response_model=QueuePage)
def my_queue(session: SessionDep, user: CurrentUserDep) -> QueuePage:
    """Return the caller's outstanding batch, with each item's text and citation."""
    return QueuePage(items=queue_domain.review_queue(session=session, verifier_id=UUID(user.id)))


@router.post("/items/{item_id}/label", response_model=LabelResponse)
def label_item(
    item_id: UUID,
    payload: LabelRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> LabelResponse:
    """Record the caller's label for one item; a ``correct`` label publishes it.

    Raises:
        HTTPException: 404 if the item does not exist, 403 if it belongs to
            another verifier, 422 if an escalating label carries no note.
            Promotion failures surface through the shared handlers: 403 for a
            caller without the reviewer role, 409 for a promotion refusal.
    """
    try:
        row = queue_domain.label_and_publish(
            session=session,
            item_id=item_id,
            verifier=user,
            label=payload.label,
            note=payload.note,
        )
    except queue_domain.QueueError as exc:
        raise _queue_error_to_http(exc) from exc

    session.commit()
    return LabelResponse(id=row.id, status=row.status, label=row.label, decision=row.decision)


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

    return EscalationPage(items=queue_domain.review_escalations(session=session))


@router.post("/escalations/{item_id}/resolve", response_model=LabelResponse)
def resolve_escalation(
    item_id: UUID,
    payload: ResolveRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> LabelResponse:
    """Decide an escalated item as a lead: publish it or keep it out.

    Raises:
        HTTPException: 404 if the item does not exist, 422 if it is not
            escalated, carries no note, or the caller escalated it themselves.
            The reviewer-role check and promotion refusals surface through the
            shared handlers (403 and 409).
    """
    try:
        row = queue_domain.resolve_escalation(
            session=session,
            item_id=item_id,
            lead=user,
            decision=payload.decision,
            note=payload.note,
        )
    except queue_domain.QueueError as exc:
        raise _queue_error_to_http(exc) from exc

    session.commit()
    return LabelResponse(id=row.id, status=row.status, label=row.label, decision=row.decision)


def _queue_error_to_http(exc: queue_domain.QueueError) -> HTTPException:
    """Map a queue refusal to a status by cause.

    Args:
        exc: The refusal.

    Returns:
        The HTTP error to raise. "Not yours" and "does not exist" are different
        problems for whoever is debugging, and a missing note is a client error
        the caller can fix — so they are not collapsed into one code.
    """
    message = str(exc)
    if "no verification item" in message:
        return HTTPException(status.HTTP_404_NOT_FOUND, message)
    if "not assigned" in message:
        return HTTPException(status.HTTP_403_FORBIDDEN, message)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)
