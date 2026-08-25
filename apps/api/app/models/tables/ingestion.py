"""ORM models for crawl jobs, staged documents, verification, and promotions.

``PromotionAuditRow`` is append-only: it is the record of who made a piece of
content live, and is written in the same transaction as the production index
write. See docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
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
    """A staged document's place in the human review queue."""

    __tablename__ = "verification_items"

    staged_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("staged_documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
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
