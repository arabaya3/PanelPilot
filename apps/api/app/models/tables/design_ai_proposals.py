"""ORM model for AI intake proposals (M6b, ``pp-ai-proposal/1``).

A proposal is what a model proposed from a designer's description, after the
server's checks: the description, who asked and with which provider, model
and prompt, the kept items and the refused ones, all fixed at creation and
covered by ``proposal_sha256``. Only ``decisions`` grows (append-only), and
``used_at``/``used_by_revision_id`` are set once, when a save applies it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class DesignAiProposalRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """One AI proposal of a tenant; never shared across tenants."""

    __tablename__ = "design_ai_proposals"

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    server_rejected: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    decisions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    proposal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # SET NULL: deleting a project deletes its revisions; the proposal stays
    # used (``used_at``), so it is never applied a second time.
    used_by_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("design_project_revisions.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The caller's acknowledgement that the description is sent to the model
    # provider (S3): when, and which account. Null on proposals made before
    # it was asked for.
    ai_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_consent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
