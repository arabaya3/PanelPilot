"""Saved design projects, and their revisions.

``design_projects`` holds one row per saved project; each save adds an
immutable ``design_project_revisions`` row with the project as entered
(JSONB). Both are tenant-scoped; revisions go with their project.

Revision ID: c8d2e5f1a7b3
Revises: b7caa00a3fb9
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8d2e5f1a7b3"
down_revision: str | None = "b7caa00a3fb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``design_projects`` and ``design_project_revisions``."""
    op.create_table(
        "design_projects",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_design_projects_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_design_projects")),
    )
    op.create_index(
        op.f("ix_design_projects_tenant_id"), "design_projects", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_design_projects_tenant_id_updated_at",
        "design_projects",
        ["tenant_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "design_project_revisions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=320), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["design_projects.id"],
            name=op.f("fk_design_project_revisions_project_id_design_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_design_project_revisions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_design_project_revisions")),
        sa.UniqueConstraint(
            "project_id", "number", name=op.f("uq_design_project_revisions_project_id")
        ),
    )
    op.create_index(
        op.f("ix_design_project_revisions_project_id"),
        "design_project_revisions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_design_project_revisions_tenant_id"),
        "design_project_revisions",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop both tables, revisions first."""
    op.drop_index(
        op.f("ix_design_project_revisions_tenant_id"), table_name="design_project_revisions"
    )
    op.drop_index(
        op.f("ix_design_project_revisions_project_id"), table_name="design_project_revisions"
    )
    op.drop_table("design_project_revisions")
    op.drop_index("ix_design_projects_tenant_id_updated_at", table_name="design_projects")
    op.drop_index(op.f("ix_design_projects_tenant_id"), table_name="design_projects")
    op.drop_table("design_projects")
