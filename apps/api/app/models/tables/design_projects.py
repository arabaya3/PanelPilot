"""ORM models for saved design projects and their revisions.

A project is saved as what the engineer entered (the request), not as the
design it produced: the design is computed from the request, so saving the
request keeps a project openable after a rule or table is corrected, and
re-designs it under the corrected rule rather than serving a stale result.

Every save is a new revision, never an update: a drawing set issued at
revision 3 must still be reproducible after revision 7 changes the boards.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class DesignProjectRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A saved project. ``updated_at`` moves with each new revision."""

    __tablename__ = "design_projects"
    # The project list is one tenant's projects, most recently saved first.
    __table_args__ = (Index("ix_design_projects_tenant_id_updated_at", "tenant_id", "updated_at"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    revisions: Mapped[list[DesignRevisionRow]] = relationship(
        back_populates="project",
        order_by="DesignRevisionRow.number",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DesignRevisionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """One saved revision of a project: its request as entered, never changed.

    Tenant-scoped in its own right, not only through its project, so loading
    a revision by its project's id cannot reach another tenant's.
    """

    __tablename__ = "design_project_revisions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    # CASCADE: deleting a project is the engineer's deliberate act on their
    # own work, and a revision has no meaning without its project.
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    author: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    project: Mapped[DesignProjectRow] = relationship(back_populates="revisions")
