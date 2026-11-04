"""AI intake per-account allowance, in-flight lease and consent (M6b S2/S3).

Adds the tenant-scoped ``ai_intake_usage`` (one row per tenant and account:
the account's AI intake calls this UTC month and its one call in flight) and
two nullable columns on ``design_ai_proposals`` recording who acknowledged,
and when, that the description is sent to the model provider. Additive: no
existing row changes; proposals made before keep null consent columns.

Downgrade drops the table (the month's per-account counts start again) and
the two columns (the consent records are lost).

Revision ID: e9d3b7a2c5f4
Revises: d4f8a1c3e6b2
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9d3b7a2c5f4"
down_revision: str | None = "d4f8a1c3e6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``ai_intake_usage``; add the consent columns."""
    op.create_table(
        "ai_intake_usage",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_ai_intake_usage_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_intake_usage")),
        sa.UniqueConstraint("tenant_id", "user_id", name=op.f("uq_ai_intake_usage_tenant_id")),
    )
    op.create_index(
        op.f("ix_ai_intake_usage_tenant_id"), "ai_intake_usage", ["tenant_id"], unique=False
    )
    op.add_column(
        "design_ai_proposals", sa.Column("ai_consent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "design_ai_proposals", sa.Column("ai_consent_by_user_id", sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    """Drop the consent columns and ``ai_intake_usage``."""
    op.drop_column("design_ai_proposals", "ai_consent_by_user_id")
    op.drop_column("design_ai_proposals", "ai_consent_at")
    op.drop_index(op.f("ix_ai_intake_usage_tenant_id"), table_name="ai_intake_usage")
    op.drop_table("ai_intake_usage")
