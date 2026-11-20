"""Add server-backed design drafts (slice 1): ``design_drafts`` and ``design_draft_requests``.

Additive: two new tables, nothing existing is changed, and no revision,
snapshot, hash or approval is read or written. A draft row is one author's
unsaved work on one saved project (or its tombstone once deleted); a request
row records a draft write's request id and outcome (never a body). Both
cascade with their project.

Rolling the application back does NOT require this downgrade: the tables are
simply unused by older code. Downgrade drops both tables and every server
draft in them: destructive, documented here, and never run automatically.

Revision ID: a7d3e9c1b5f2
Revises: d2b6f4a8c1e3
Create Date: 2026-11-19 23:34:04.976659
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7d3e9c1b5f2"
down_revision: str | None = "d2b6f4a8c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_draft_requests",
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            name=op.f("fk_design_draft_requests_project_id_design_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_design_draft_requests_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_design_draft_requests")),
        sa.UniqueConstraint(
            "tenant_id", "author_user_id", "request_id", name="uq_design_draft_requests_request"
        ),
    )
    op.create_index(
        op.f("ix_design_draft_requests_project_id"),
        "design_draft_requests",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_design_draft_requests_tenant_id"),
        "design_draft_requests",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "design_drafts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint(
            "(state = 'live') = (body IS NOT NULL)", name=op.f("ck_design_drafts_body_when_live")
        ),
        sa.CheckConstraint("state IN ('live', 'deleted')", name=op.f("ck_design_drafts_state")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_design_drafts_version")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["design_projects.id"],
            name=op.f("fk_design_drafts_project_id_design_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_design_drafts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_design_drafts")),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "author_user_id", name="uq_design_drafts_author_project"
        ),
    )
    op.create_index(
        op.f("ix_design_drafts_project_id"), "design_drafts", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_design_drafts_tenant_id"), "design_drafts", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_design_drafts_tenant_id"), table_name="design_drafts")
    op.drop_index(op.f("ix_design_drafts_project_id"), table_name="design_drafts")
    op.drop_table("design_drafts")
    op.drop_index(op.f("ix_design_draft_requests_tenant_id"), table_name="design_draft_requests")
    op.drop_index(op.f("ix_design_draft_requests_project_id"), table_name="design_draft_requests")
    op.drop_table("design_draft_requests")
