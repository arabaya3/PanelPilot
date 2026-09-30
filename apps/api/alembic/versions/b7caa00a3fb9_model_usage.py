"""Count each tenant's model calls per calendar month.

``model_usage`` backs ``MODEL_CALLS_PER_MONTH``: one row per tenant per UTC
month, charged under a row lock before every model call.

Revision ID: b7caa00a3fb9
Revises: a1921e8e0e2f
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7caa00a3fb9"
down_revision: str | None = "a1921e8e0e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``model_usage``."""
    op.create_table(
        "model_usage",
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
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
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_model_usage_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage")),
        sa.UniqueConstraint("tenant_id", "period", name=op.f("uq_model_usage_tenant_id")),
    )
    op.create_index(op.f("ix_model_usage_tenant_id"), "model_usage", ["tenant_id"], unique=False)


def downgrade() -> None:
    """Drop ``model_usage``."""
    op.drop_index(op.f("ix_model_usage_tenant_id"), table_name="model_usage")
    op.drop_table("model_usage")
