"""AI intake proposals (M6b, ``pp-ai-proposal/1``).

Adds the tenant-scoped ``design_ai_proposals``: what a model proposed from a
designer's description, after the server's checks, with the decisions taken
on it (append-only) and, once a save applied it, the revision that did.
Additive: no existing table or row changes.

Downgrade drops the table, and with it every proposal; the records of saved
revisions keep their own copy (``requirements.ai``) and their hashes.

Revision ID: d4f8a1c3e6b2
Revises: c8e4a2d6f1b9
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4f8a1c3e6b2"
down_revision: str | None = "c8e4a2d6f1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``design_ai_proposals``."""
    op.create_table(
        "design_ai_proposals",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("server_rejected", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal_sha256", sa.String(length=64), nullable=False),
        sa.Column("used_by_revision_id", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_design_ai_proposals_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_revision_id"],
            ["design_project_revisions.id"],
            name=op.f("fk_design_ai_proposals_used_by_revision_id_design_project_revisions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_design_ai_proposals")),
    )
    op.create_index(
        op.f("ix_design_ai_proposals_tenant_id"),
        "design_ai_proposals",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``design_ai_proposals``."""
    op.drop_index(op.f("ix_design_ai_proposals_tenant_id"), table_name="design_ai_proposals")
    op.drop_table("design_ai_proposals")
