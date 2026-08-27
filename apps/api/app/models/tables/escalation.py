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


class FlaggedAnswerRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """An answer a user reported as wrong, with what they were shown.

    The context is **copied here at flag time, not referenced**. Retrieval over
    a growing index does not return the same passages for the same question a
    month later, so re-running the query would show a reviewer something the
    user never saw — and the reviewer would then judge an answer that was never
    given. Storing the question, the answer, and the passages as they stood
    makes the record reconstructible however much the corpus moves underneath
    it.

    Tenant-scoped: a flagged answer contains a customer's question and the
    content they were shown, which is as confidential as any other turn.
    """

    __tablename__ = "flagged_answers"

    # The turn the user flagged. SET NULL rather than CASCADE: a session may be
    # pruned on retention long before the accuracy problem it exposed is fixed,
    # and losing the flag with it would discard exactly the post-launch signal
    # this table exists to keep.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("diagnostic_turns.id", ondelete="SET NULL"), index=True
    )
    flagged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Copied, not joined. See the class docstring.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # The retrieved passages as JSON: an immutable record of what backed the
    # answer, not queryable state. Same reasoning as the citations on a turn.
    retrieved_context: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional free text from the user. Most people flag without explaining,
    # and demanding a reason would cost more signal than it gathers.
    reason: Mapped[str | None] = mapped_column(Text)


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
