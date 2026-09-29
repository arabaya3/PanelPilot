"""Scope diagnostic turns by tenant.

Turns were the one piece of customer data without a ``tenant_id``: listed as
"reached only through its tenant-scoped session". That held only while every
query reached them through a session. ``flag_answer`` loads a turn by id and
then checks its session's tenant by hand — per-query discipline, the thing
ADR 0003 replaces.

With the column, the tenant filter covers turns like every other scoped
table, so a turn of another tenant loaded by id is simply not found.

Backfilled from each turn's session, which is where the tenant came from all
along, then made non-nullable, indexed, and ``RESTRICT`` on tenant deletion
like every other scoped table.

Revision ID: f3a9c2d4e5b1
Revises: a7c31f95d2b8
Create Date: 2026-09-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9c2d4e5b1"
down_revision: str | None = "a7c31f95d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add, backfill and constrain ``diagnostic_turns.tenant_id``."""
    op.add_column("diagnostic_turns", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.execute("""
        UPDATE diagnostic_turns AS t
        SET tenant_id = s.tenant_id
        FROM diagnostic_sessions AS s
        WHERE t.session_id = s.id
        """)
    op.alter_column("diagnostic_turns", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_diagnostic_turns_tenant_id_tenants",
        "diagnostic_turns",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_diagnostic_turns_tenant_id", "diagnostic_turns", ["tenant_id"])


def downgrade() -> None:
    """Drop the column again; turns fall back to their session's tenant."""
    op.drop_index("ix_diagnostic_turns_tenant_id", table_name="diagnostic_turns")
    op.drop_constraint(
        "fk_diagnostic_turns_tenant_id_tenants", "diagnostic_turns", type_="foreignkey"
    )
    op.drop_column("diagnostic_turns", "tenant_id")
