"""ORM models for saved design projects, their revisions, and company settings.

A project is saved as what the engineer entered (the request), which is what
it is opened and edited from. Since revisions bind approvals, each also holds
the design the server built from that request when it was saved (its
snapshot, with its hash and the rules version): an approval and a revision
export are of that design, and a change of rule makes the revision stale
rather than silently re-designing what was approved.

Every save is a new revision, never an update: a drawing set issued at
revision 3 must still be reproducible after revision 7 changes the boards.
A revision's only later change is its approval, set once and never undone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
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
    # Set once, when the engineer approves this revision; never cleared. The
    # name is the one the title block prints; the account is who signed in.
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_account: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the server designed from ``request`` when the revision was saved,
    # and what binds an approval and an export to it. All NULL on a revision
    # saved before designs were stored: it opens, but must be saved again to
    # be approved or exported as a revision.
    design_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # SHA-256 of the snapshot's canonical JSON (``app.design.snapshot``).
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # SHA-256 of the company profile, as resolved, the design was made under.
    profile_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rules_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    catalogue_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The findings (``DesignFinding``) recorded when it was saved.
    findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # Set with the approval, in the same locked transaction: the snapshot
    # hash approved. An approval counts only while it equals snapshot_sha256.
    approved_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    project: Mapped[DesignProjectRow] = relationship(back_populates="revisions")


class CompanySettingsRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A tenant's company profile settings: what it does differently from the default.

    One row per tenant, kept as the settings the engineer entered (JSON), so a
    default corrected later still applies to everything they left unset.
    """

    __tablename__ = "company_settings"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False, default="")
