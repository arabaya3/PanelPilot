"""ORM model for the per-user AI intake allowance and in-flight lease (M6b, S2).

One row per (tenant, account). ``calls`` counts the account's AI intake calls
in ``period`` (a UTC month, ``YYYY-MM``) and starts again at zero when a call
lands in a later month. ``lease_id``/``lease_expires_at`` mark the one call
the account may have in flight: set in the same short transaction that
charges the call, cleared when the call ends, and ignored once expired (a
crashed worker never clears it). Read and written under a row lock only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class AiIntakeUsageRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """One account's AI intake calls this month, and its call in flight, if any."""

    __tablename__ = "ai_intake_usage"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: The UTC calendar month ``calls`` counts, as ``YYYY-MM``.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
