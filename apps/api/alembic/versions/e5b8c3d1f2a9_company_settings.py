"""A tenant's company profile settings.

One row per tenant: the settings it changed from the default profile (JSONB),
and who last saved them.

Revision ID: e5b8c3d1f2a9
Revises: d4a7f2c9e1b6
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5b8c3d1f2a9"
down_revision: str | None = "d4a7f2c9e1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``company_settings``."""
    op.create_table(
        "company_settings",
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.String(length=320), nullable=False),
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
            name=op.f("fk_company_settings_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_company_settings_tenant_id")),
    )
    op.create_index(
        op.f("ix_company_settings_tenant_id"), "company_settings", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    """Drop ``company_settings``."""
    op.drop_index(op.f("ix_company_settings_tenant_id"), table_name="company_settings")
    op.drop_table("company_settings")
