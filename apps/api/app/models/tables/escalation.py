"""ORM models for escalation and source health.

``escalation_items`` records a question the assistant refused or answered with
low confidence, so the gap becomes a work item rather than a dead end for the
engineer who hit it. ``source_health`` tracks whether each documentation source
is still reachable and parsing, because silent crawl decay is how a corpus goes
stale without anyone noticing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class EscalationItemRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A question that could not be answered from verified documentation.

    Created by the cite-or-refuse path. Tenant-scoped: one customer's unanswered
    questions are as confidential as their answered ones.
    """

    __tablename__ = "escalation_items"

    # Nullable: an escalation can outlive the session it came from, and the
    # session may be pruned before the gap is closed.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="SET NULL"), index=True
    )
    raised_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Why the assistant declined: no evidence, low confidence, out of scope.
    reason: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class SourceHealthRow(UUIDPrimaryKey, TimestampMixin, Base):
    """Reachability and parse health of one documentation source.

    Deliberately NOT tenant-scoped: the corpus is shared infrastructure, not
    customer data. Adding a tenant column here would imply per-customer
    documentation, which is not the model.
    """

    __tablename__ = "source_health"

    source_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Consecutive failures, not a total: a source that failed twice last year
    # and works now is healthy, and a raw count would never recover.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    documents_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
