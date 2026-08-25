"""ORM model for tenants — the multi-tenancy boundary.

Every row that belongs to a customer carries ``tenant_id``. That column is the
only thing standing between one customer's diagnostic history and another's, so
it is non-nullable everywhere it appears and indexed on every table that will
be filtered by it.

The isolation itself is enforced in ``app.domain``: queries filter by the
caller's tenant. This module makes the column impossible to omit, which is the
half that belongs in the schema.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey


class TenantRow(UUIDPrimaryKey, TimestampMixin, Base):
    """A customer organisation. The root of every ownership chain."""

    __tablename__ = "tenants"

    # Human-readable identifier used in URLs and support conversations.
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Free-tier usage, counted server-side. A client-reported count is a number
    # the client can choose, so this is the only figure the quota check reads.
    # Incremented on a COMPLETED diagnosis, not on request: a failed or refused
    # answer must not burn a question the engineer never received.
    free_questions_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    free_question_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    def has_free_questions_remaining(self) -> bool:
        """Report whether this tenant may ask another free question.

        Returns:
            ``True`` while usage is below the limit.
        """
        return self.free_questions_used < self.free_question_limit


class TenantScopedMixin:
    """Adds the tenant foreign key to a table whose rows belong to a customer.

    ``ON DELETE RESTRICT`` rather than ``CASCADE``: deleting a tenant must be a
    deliberate, ordered teardown, not something that silently removes an audit
    trail as a side effect of one row going away.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
