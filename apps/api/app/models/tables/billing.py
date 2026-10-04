"""ORM model for a tenant's subscription.

One row per tenant, holding the plan it is on and, separately, the plan it
asked for: a request does not change what the account may do until an
operator (or, later, a payment provider) activates it. A tenant with no row,
or whose paid period has ended, is on the free plan.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class SubscriptionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """What a tenant pays for, and what it asked to pay for."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    # The plan in force, as ``app.domain.plans.PlanKey``.
    plan: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    interval: Mapped[str] = mapped_column(String(10), nullable=False, default="monthly")
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Until when the paid plan holds; ``None`` on the free plan.
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Who activated it: "manual" for an operator, else the payment provider.
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    provider_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # The plan the account asked for, waiting to be activated.
    requested_plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    requested_interval: Mapped[str | None] = mapped_column(String(10), nullable=True)
    requested_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
