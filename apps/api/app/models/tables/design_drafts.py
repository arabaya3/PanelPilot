"""ORM models for server-backed design drafts (slice 1).

A draft is one author's unsaved work on one saved project: structurally
checked inputs, never engineering-validated, never a revision, never approved
or exported. It is private to its author (tenant and author bound on every
operation); tenant membership alone, owner or admin, never reaches another
author's draft.

``design_drafts`` keeps one row per (tenant, project, author) for its whole
life: deleting it leaves a tombstone (``state='deleted'``, no body) and every
write raises ``version``. The concurrency token is ``"<id>.<version>"``, so a
draft deleted and written again never takes a token an earlier writer holds.

``design_draft_requests`` records each write's request id and outcome, so a
retried request is answered as it was the first time and never applied again.
It never holds a draft body.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class DesignDraftRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """One author's draft of one saved project (or its tombstone)."""

    __tablename__ = "design_drafts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "project_id", "author_user_id", name="uq_design_drafts_author_project"
        ),
        CheckConstraint("state IN ('live', 'deleted')", name="state"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("(state = 'live') = (body IS NOT NULL)", name="body_when_live"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # SQL NULL (not JSON null) when deleted, as the check constraint requires.
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    body_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DesignDraftRequestRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A draft write's request id, what it asked, and how it was answered."""

    __tablename__ = "design_draft_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "author_user_id", "request_id", name="uq_design_draft_requests_request"
        ),
    )

    author_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # The answer as sent (status and response fields); never the draft body.
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
