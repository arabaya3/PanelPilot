"""ORM model for an invitation to join a tenant.

A tenant's owner invites a colleague by email; the colleague signs up with
the invitation's token and joins that tenant instead of getting one of their
own. Only the token's hash is stored: the plaintext is shown to the owner
once, to send.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class InvitationRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """An invitation to join a tenant, until it is accepted or expires."""

    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by: Mapped[str] = mapped_column(String(320), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
