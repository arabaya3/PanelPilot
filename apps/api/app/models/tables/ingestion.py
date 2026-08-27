"""ORM models for crawl jobs, staged documents, verification, and promotions.

``PromotionAuditRow`` is append-only: it is the record of who made a piece of
content live, and is written in the same transaction as the production index
write. See docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey


class CrawlJobRow(UUIDPrimaryKey, TimestampMixin, Base):
    """A queued or completed crawl of one documentation source."""

    __tablename__ = "crawl_jobs"

    source_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class StagedDocumentRow(UUIDPrimaryKey, TimestampMixin, Base):
    """A crawled document held in staging, awaiting verification.

    ``content_hash`` is unique so a re-crawl that finds unchanged content does
    not queue the same document for review twice.
    """

    __tablename__ = "staged_documents"

    crawl_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The account that brought this content in. Compared against the reviewer
    # at promotion time to enforce four-eyes.
    ingested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class VerificationItemRow(UUIDPrimaryKey, TimestampMixin, Base):
    """One chunk awaiting, holding, or escalated from a verifier's judgement.

    A chunk rather than a document: a verifier labels a passage against its
    citation, and a document-sized unit is neither reviewable in one sitting
    nor citable to a page.

    **Assignment is enforced by the database, not by the assigning code.**
    ``chunk_id`` is unique, so an item exists at most once and no chunk can
    sit in two verifiers' queues. Ten people work this queue concurrently and
    the assignment job may run twice — from a retry, an overlapping schedule,
    or two operators — and application-level "check then insert" has a window
    between the check and the insert. The constraint has no window.
    """

    __tablename__ = "verification_items"
    __table_args__ = (
        # A row must identify what is being verified. Both columns are
        # nullable so the two review paths can coexist, and without this a row
        # with neither would be accepted — an item in the queue referring to
        # nothing, which no code would produce but no code would catch either.
        CheckConstraint(
            "chunk_id IS NOT NULL OR staged_document_id IS NOT NULL",
            name="target_present",
        ),
    )

    # Nullable: chunk-level items are not tied to a single staged document
    # once chunking splits one document into many pieces. Kept for the
    # document-level review path that predates this table's chunk columns.
    staged_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staged_documents.id", ondelete="CASCADE"),
        index=True,
    )
    # The unit of review, and the uniqueness that makes double-assignment
    # impossible. Not a foreign key: chunks live in the staging index, not in
    # this database, so there is no table to reference.
    chunk_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    # Whose queue this sits in. Set by the assignment job; distinct from
    # `claimed_by_id`, which the document-level path uses for opportunistic
    # claiming. An assigned item belongs to one person until relabelled.
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending -> labeled -> escalated. Indexed because the queue view and the
    # lead-review view are both filters on it.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # The verifier's judgement, from `VerificationLabel`. Distinct from
    # `decision`, which is the document-level approve/reject vocabulary.
    label: Mapped[str | None] = mapped_column(String(20), index=True)
    claimed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decision: Mapped[str | None] = mapped_column(String(20), index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class PromotionAuditRow(UUIDPrimaryKey, TimestampMixin, Base):
    """Append-only record of a staging-to-production promotion.

    Never updated and never deleted. If content must come out of production,
    that is a separate retraction event with its own row — not an edit here.

    ``reviewer_id`` uses ``ON DELETE RESTRICT``: a user who has approved live
    content cannot be deleted out from under the audit trail.
    """

    __tablename__ = "promotion_audits"

    staged_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staged_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    production_document_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
