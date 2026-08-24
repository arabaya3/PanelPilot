"""ORM models for crawl jobs, staged documents, verification, and promotions.

``PromotionAuditRow`` is append-only: it is the record of who made a piece of
content live, and is written in the same transaction as the production index
write. See docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from app.models.tables.base import Base, TimestampMixin


class CrawlJobRow(Base, TimestampMixin):
    """A queued or completed crawl of one documentation source."""

    __tablename__ = "crawl_jobs"


class StagedDocumentRow(Base, TimestampMixin):
    """A crawled document held in staging, awaiting verification."""

    __tablename__ = "staged_documents"


class VerificationItemRow(Base, TimestampMixin):
    """A staged document's place in the human review queue."""

    __tablename__ = "verification_items"


class PromotionAuditRow(Base, TimestampMixin):
    """Append-only record of a staging-to-production promotion."""

    __tablename__ = "promotion_audits"
