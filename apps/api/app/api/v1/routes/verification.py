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

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import corpus_maintenance as maintenance_domain
from app.domain import promotion as promotion_domain
from app.domain import verification_queue as queue_domain
from app.models.schemas.auth import Role
from app.models.schemas.verification import (
    DismissStaleRequest,
    EscalationPage,
    LabelRequest,
    LabelResponse,
    QueueItem,
    QueuePage,
    StaleDocument,
    StaleDocumentPage,
)
from app.models.tables.ingestion import StaleDocumentRow, VerificationItemRow

router = APIRouter()


def _to_items(rows: list[VerificationItemRow]) -> list[QueueItem]:
    """Project queue rows onto their wire shape, with the text under review.

    Args:
        rows: The database rows.

    Returns:
        The items as the API presents them. The staged chunks are read in one
        request for the whole batch, not one per item.
    """
    chunks = queue_domain.staged_chunks([row.chunk_id for row in rows if row.chunk_id])
    items = []
    for row in rows:
        chunk = chunks.get(row.chunk_id or "", {})
        page = chunk.get("page")
        items.append(
            QueueItem(
                id=row.id,
                chunk_id=row.chunk_id,
                status=row.status,
                assigned_at=row.assigned_at,
                content=chunk.get("content"),
                source_url=chunk.get("source_url"),
                page=page if isinstance(page, int) else None,
                section=chunk.get("section"),
            )
        )
    return items


@router.get("/queue/me", response_model=QueuePage)
def my_queue(session: SessionDep, user: CurrentUserDep) -> QueuePage:
    """Return the caller's outstanding batch."""
    rows = queue_domain.queue_for(session=session, verifier_id=UUID(user.id))
    return QueuePage(items=_to_items(list(rows)))


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
        # Labelling is clearance: a correct label publishes the chunk in the
        # same transaction (ADR 0001), so this commits both or neither.
        row = promotion_domain.clear_item(
            session=session,
            reviewer=user,
            item_id=item_id,
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
    return EscalationPage(items=_to_items(list(rows)))


def _to_stale(row: StaleDocumentRow) -> StaleDocument:
    """Project a stale-document row onto its wire shape.

    Args:
        row: The database row.

    Returns:
        The flag as the API presents it.
    """
    return StaleDocument(
        id=row.id,
        source_url=row.source_url,
        source_id=row.source_id,
        reason=row.reason,
        status=row.status,
        published_hashes=[h for h in row.published_hashes.split(",") if h],
        upstream_hash=row.upstream_hash,
        first_flagged_at=row.first_flagged_at,
        last_checked_at=row.last_checked_at,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
    )


@router.get("/stale-documents", response_model=StaleDocumentPage)
def list_stale_documents(
    session: SessionDep,
    user: CurrentUserDep,
    state: Annotated[Literal["open", "dismissed", "cleared"], Query(alias="status")] = "open",
) -> StaleDocumentPage:
    """Return live documents whose source changed or withdrew them.

    Raises:
        AuthorizationError: 403 unless the caller holds the reviewer role.
    """
    rows = maintenance_domain.list_stale_documents(session=session, reviewer=user, status=state)
    return StaleDocumentPage(items=[_to_stale(row) for row in rows])


@router.post("/stale-documents/{document_id}/dismiss", response_model=StaleDocument)
def dismiss_stale_document(
    document_id: UUID,
    payload: DismissStaleRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> StaleDocument:
    """Record that an upstream change is harmless, and why.

    Raises:
        AuthorizationError: 403 unless the caller holds the reviewer role.
        NotFoundError: 404 if there is no such flag.
        ValidationError: 422 if the note is blank or the flag is not open.
    """
    row = maintenance_domain.dismiss_stale_document(
        session=session, reviewer=user, document_id=document_id, note=payload.note
    )
    session.commit()
    return _to_stale(row)
