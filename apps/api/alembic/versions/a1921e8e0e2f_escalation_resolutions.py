"""Record how a lead resolved an escalated item.

An escalated item had a list and no way out of it. ``escalation_resolutions``
is the append-only record of each resolution, with the escalation it settled
copied in, since taking an item over sends it back through the queue where the
next label replaces the row's own. ``verification_items.status`` gains
``upheld`` (a string column with no constraint, so no DDL for it).

Revision ID: a1921e8e0e2f
Revises: f1ee20b60261
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1921e8e0e2f"
down_revision: str | None = "f1ee20b60261"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``escalation_resolutions``."""
    op.create_table(
        "escalation_resolutions",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("escalated_by_id", sa.Uuid(), nullable=True),
        sa.Column("escalated_label", sa.String(length=20), nullable=False),
        sa.Column("escalated_note", sa.Text(), nullable=False),
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
            ["escalated_by_id"],
            ["users.id"],
            name=op.f("fk_escalation_resolutions_escalated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["verification_items.id"],
            name=op.f("fk_escalation_resolutions_item_id_verification_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["users.id"],
            name=op.f("fk_escalation_resolutions_lead_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalation_resolutions")),
    )
    op.create_index(
        op.f("ix_escalation_resolutions_item_id"),
        "escalation_resolutions",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_escalation_resolutions_lead_id"),
        "escalation_resolutions",
        ["lead_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the record; an upheld item reads as escalated again."""
    op.execute("UPDATE verification_items SET status = 'escalated' WHERE status = 'upheld'")
    op.drop_index(op.f("ix_escalation_resolutions_lead_id"), table_name="escalation_resolutions")
    op.drop_index(op.f("ix_escalation_resolutions_item_id"), table_name="escalation_resolutions")
    op.drop_table("escalation_resolutions")
