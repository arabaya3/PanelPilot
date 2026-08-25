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

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey


class TenantRow(UUIDPrimaryKey, TimestampMixin, Base):
    """A customer organisation. The root of every ownership chain."""

    __tablename__ = "tenants"

    # Human-readable identifier used in URLs and support conversations.
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TenantScopedMixin:
    """Adds the tenant foreign key to a table whose rows belong to a customer.

    ``ON DELETE RESTRICT`` rather than ``CASCADE``: deleting a tenant must be a
    deliberate, ordered teardown, not something that silently removes an audit
    trail as a side effect of one row going away.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
