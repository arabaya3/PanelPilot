"""A tenant's subscription: the plan in force and the plan asked for.

Revision ID: f6c9d2e4a7b1
Revises: e5b8c3d1f2a9
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6c9d2e4a7b1"
down_revision: str | None = "e5b8c3d1f2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``subscriptions``."""
    op.create_table(
        "subscriptions",
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("interval", sa.String(length=10), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_ref", sa.String(length=200), nullable=True),
        sa.Column("requested_plan", sa.String(length=20), nullable=True),
        sa.Column("requested_interval", sa.String(length=10), nullable=True),
        sa.Column("requested_seats", sa.Integer(), nullable=True),
        sa.Column("requested_by", sa.String(length=320), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_subscriptions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_subscriptions_tenant_id")),
    )
    op.create_index(
        op.f("ix_subscriptions_tenant_id"), "subscriptions", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    """Drop ``subscriptions``."""
    op.drop_index(op.f("ix_subscriptions_tenant_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
