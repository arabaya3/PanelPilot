"""Staging-to-production promotion service.

This module is the **only** write path into the production index. Nothing in
``app.ingestion`` or ``app.domain.ingestion`` may write there. The path is
``clear_item`` -> ``promote_chunk``: a reviewer labels a queue item correct and
the chunk is published in the same transaction, on the strength of that
recorded label and nothing else. Rationale and consequences:
docs/adr/0001-staging-vs-production-index.md.

If you are adding a feature that needs content to become live, extend this
module — do not add a second path.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval.client import IndexTarget, get_client, index_chunk, resolve_index
from app.ai.retrieval.mappings import VerificationStatus
from app.core.errors import (
    AuthorizationError,
    NotFoundError,
    NotImplementedYetError,
    PromotionError,
)
from app.domain import verification_queue as queue_domain
from app.domain.verification_queue import STATUS_LABELED
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.ingestion import (
    PromotionRequest,
    PromotionResponse,
    VerificationDecision,
    VerificationVerdict,
)
from app.models.schemas.verification import VerificationLabel
from app.models.tables.ingestion import PromotionAuditRow, VerificationItemRow


def promote_document(
    *,
    session: Session,
    reviewer: CurrentUser,
    request: PromotionRequest,
) -> PromotionResponse:
    """Copy a verified staged document into the production index.

    Preconditions, all enforced here rather than by the caller:

    1. The reviewer holds the reviewer role and is not the ingester of record.
    2. The staged document has passed automated verification checks.
    3. The document carries a resolvable source citation.

    The staged document is left in place; promotion writes a new production
    revision and records an immutable audit entry naming the reviewer.

    Args:
        session: Open database session; the audit entry and index write commit
            together.
        reviewer: The human approving the promotion.
        request: Staged document identifier and review notes.

    Returns:
        The promotion outcome, including the production revision written.

    Raises:
        AuthorizationError: If the reviewer lacks the reviewer role.
        PromotionError: If any precondition above is unmet.
        NotFoundError: If the staged document does not exist.
        NotImplementedYetError: Always, for now. Whole-document promotion is
            not built — ``promote_chunk`` is the implemented write path — and
            a 501 that says so beats an anonymous 500.
    """
    del session, reviewer, request  # Unused until built; the signature is the contract.
    raise NotImplementedYetError(
        "whole-document promotion is not available; chunks are promoted by labelling "
        "them correct: POST /api/v1/verification/items/{item_id}/label"
    )


def _as_uuid(value: str, *, field: str) -> uuid.UUID:
    """Coerce an identifier to a UUID, failing with the field that was wrong.

    Args:
        value: The raw identifier.
        field: Name of the field, for the error message.

    Returns:
        The parsed UUID.

    Raises:
        PromotionError: If the value is not a UUID. The audit row's foreign
            keys are UUIDs, so a non-UUID id would otherwise surface as an
            opaque database error at flush time.
    """
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise PromotionError(f"cannot promote: {field}={value!r} is not a UUID") from exc


def promote_chunk(
    *,
    session: Session,
    reviewer: CurrentUser,
    chunk_id: str,
    verdict: VerificationVerdict,
) -> PromotionResponse:
    """Copy one verified chunk from staging into production.

    BE-004's promotion entry point, and the only code in the system that writes
    the production index. The task spec places this in
    ``app/ingestion/promotion.py``; it lives here instead because ADR 0001 and
    ``test_architecture.py`` make ``app/ingestion/`` structurally incapable of
    referencing the production index. Putting a production write there would
    mean widening the guard that enforces the very invariant this task exists
    to provide. The substance is unchanged: exactly one write path.

    Args:
        session: Open database session. The audit row is written and flushed
            here but **not committed** — the caller owns the transaction, so it
            can commit the promotion and its own state change together. On any
            publish failure this function rolls back, discarding the row.

            **The caller must commit.** The index write is not transactional,
            so a caller that rolls back after this returns leaves content live
            in production with no committed audit row — the one thing ADR 0001
            §5 exists to prevent. It is recoverable: re-promoting the same
            chunk is a no-op on the index and writes the missing row. This
            window is inherent to a non-transactional publish under a
            caller-owned transaction; it can be bounded and documented, not
            eliminated here. Pinned by
            ``test_caller_rollback_leaves_live_content_unattributed``.
        reviewer: The human clearing the item. Must hold the reviewer role.
        chunk_id: Identifier of the staged chunk.
        verdict: The reviewer's decision, carried explicitly rather than
            re-read, so the decision that promoted a chunk is the one recorded.

    Returns:
        The promotion outcome, naming the production document written.

    Raises:
        AuthorizationError: If the reviewer lacks the reviewer role.
        PromotionError: If the verdict is not approval, if the reviewer is the
            ingester of record, or if the chunk is missing a required field.
        NotFoundError: If the staged chunk does not exist.
    """
    if not reviewer.has_role(Role.REVIEWER):
        raise AuthorizationError(f"{reviewer.email} does not hold the reviewer role")

    # Refused here, not filtered by the caller: a bug in the review UI must not
    # be able to publish content the reviewer rejected.
    if verdict.decision is not VerificationDecision.APPROVED:
        raise PromotionError(
            f"cannot promote {chunk_id!r}: decision is {verdict.decision.value}, "
            f"not {VerificationDecision.APPROVED.value}"
        )

    # The verdict argument is the caller's claim; the queue item is the record.
    # Promotion requires that this reviewer actually labelled this chunk
    # correct through the review queue, so no caller -- a script, a future
    # route, a bug -- can publish on a verdict nobody recorded. Locked, so two
    # clearances of one chunk cannot both pass this check and both publish.
    reviewer_uuid = _as_uuid(reviewer.id, field="reviewer.id")
    item = session.execute(
        select(VerificationItemRow)
        .where(VerificationItemRow.chunk_id == chunk_id)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        item is None
        or item.status != STATUS_LABELED
        or item.label != VerificationLabel.CORRECT.value
        or item.assigned_to_id != reviewer_uuid
    ):
        raise PromotionError(
            f"cannot promote {chunk_id!r}: it has not been labelled correct by this "
            "reviewer in the verification queue"
        )
    if item.staged_document_id is None:
        raise PromotionError(f"cannot promote {chunk_id!r}: it names no staged document")

    client = get_client()
    staging_index = resolve_index(IndexTarget.STAGING)
    if not client.exists(index=staging_index, id=chunk_id):
        raise NotFoundError(f"no staged chunk {chunk_id!r}")

    staged = client.get(index=staging_index, id=chunk_id)["_source"]

    # Four-eyes: whoever brought the content in cannot also bless it. Failing
    # closed: a chunk with no ingester of record used to skip the comparison
    # entirely, so anyone -- the ingester included -- could promote it.
    ingested_by = staged.get("ingested_by")
    if not ingested_by:
        raise PromotionError(
            f"cannot promote {chunk_id!r}: it has no ingester of record, so four-eyes "
            "cannot be checked"
        )
    if ingested_by == reviewer.id:
        raise PromotionError(
            f"cannot promote {chunk_id!r}: {reviewer.email} is the ingester of record"
        )

    # Never a silent overwrite. A chunk already live whose text has changed is
    # a NEW pending item, because an engineer who trusted a citation has no way
    # to know the text moved under it. Same content is a no-op, not an error:
    # re-clearing an unchanged chunk is harmless.
    production_index = resolve_index(IndexTarget.PRODUCTION)
    if client.exists(index=production_index, id=chunk_id):
        live = client.get(index=production_index, id=chunk_id)["_source"]
        if live.get("content_hash") != staged.get("content_hash"):
            raise PromotionError(
                f"cannot promote {chunk_id!r}: it is already live with different "
                "content. Re-crawled content re-enters through staging as a new "
                "pending item; production is never silently overwritten."
            )

    document = dict(staged)
    document["verification_status"] = VerificationStatus.VERIFIED.value
    revision = int(staged.get("revision", 1))

    # ADR 0001 §5: the production write and the audit row commit together, so
    # live content can never exist without a named human attached to it.
    #
    # OpenSearch is not in the database transaction, so "together" is achieved
    # by ordering: stage the audit row FIRST and flush it, so any constraint
    # violation surfaces before anything is published, then write the index.
    # The reverse order -- publish, then audit -- can leave live content with
    # no audit entry, which is the failure this invariant exists to prevent.
    #
    # The commit belongs to the CALLER, not here. BE-007's clearance handler
    # has to mark the queue item verified in the same transaction as the
    # promotion; committing here would force it to commit twice, leaving a
    # window where content is live but the queue item still reads pending. A
    # crash in that window re-reviews and re-promotes. Flushing gives the
    # ordering guarantee without taking the transaction boundary away.
    audit = PromotionAuditRow(
        # The document the chunk came from, as the queue recorded it. Chunk
        # ids are "<document>#<ordinal>-<digest>", never UUIDs: parsing one as
        # the staged-document key refused every real chunk ever crawled.
        staged_document_id=item.staged_document_id,
        reviewer_id=reviewer_uuid,
        production_document_id=chunk_id,
        revision=revision,
        notes=verdict.notes or None,
    )
    session.add(audit)
    session.flush()

    # index_chunk refuses anything with a null required field, so an incomplete
    # chunk cannot become a citation nobody can resolve.
    try:
        index_chunk(IndexTarget.PRODUCTION, chunk_id=chunk_id, document=document)
    except ValueError as exc:
        session.rollback()
        raise PromotionError(f"cannot promote {chunk_id!r}: {exc}") from exc
    except Exception:
        # Any index failure must take the audit row with it.
        session.rollback()
        raise

    return PromotionResponse(
        production_document_id=chunk_id,
        revision=revision,
        audit_id=str(audit.id),
    )


def clear_item(
    *,
    session: Session,
    reviewer: CurrentUser,
    item_id: uuid.UUID,
    label: VerificationLabel,
    note: str = "",
) -> VerificationItemRow:
    """Record a reviewer's label, and publish the chunk when it is correct.

    The clearance handler ADR 0001 describes: the label and the promotion
    happen in one transaction, so a chunk is never live with its queue item
    still pending, and never marked correct without being published. Any
    promotion failure -- four-eyes, an incomplete citation, OpenSearch --
    raises, and the caller's transaction (the label included) rolls back.

    Args:
        session: Open database session. The caller commits.
        reviewer: Who is labelling; must hold the reviewer role.
        item_id: The queue item.
        label: Their judgement.
        note: Their reasoning; required for labels that escalate.

    Returns:
        The labelled queue item.

    Raises:
        AuthorizationError: If the caller lacks the reviewer role. Labelling is
            review: the verdict it records is what promotion acts on.
        QueueError: As ``record_label``.
        PromotionError: If a correct label cannot be published.
    """
    if not reviewer.has_role(Role.REVIEWER):
        raise AuthorizationError(f"{reviewer.email} does not hold the reviewer role")

    row = queue_domain.record_label(
        session=session,
        item_id=item_id,
        verifier_id=_as_uuid(reviewer.id, field="reviewer.id"),
        label=label,
        note=note,
    )
    # Only a crawled chunk has anything to publish; a flagged answer is a
    # report about content already live, and is resolved by other means.
    if label is VerificationLabel.CORRECT and row.chunk_id is not None:
        promote_chunk(
            session=session,
            reviewer=reviewer,
            chunk_id=row.chunk_id,
            verdict=VerificationVerdict(decision=VerificationDecision.APPROVED, notes=note),
        )
    return row
