"""Let a reviewer dismiss a stale-document flag, with who, when and why.

``expire-stale-sources`` flags a live document whenever its source serves
different bytes. Many such changes are harmless -- a typo fix, a new cover
page -- and without a way to say so a flag stays open forever and buries the
ones that matter. A dismissal records the reviewer, the time and a note, and
holds only while the source serves that same revision.

Revision ID: 3c5e1f7a9b2d
Revises: 8e8df97fb49b
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3c5e1f7a9b2d"
down_revision: str | None = "8e8df97fb49b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the reviewer columns and the ``dismissed`` status."""
    op.add_column("stale_documents", sa.Column("reviewed_by_id", sa.Uuid(), nullable=True))
    op.add_column(
        "stale_documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("stale_documents", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_stale_documents_reviewed_by_id_users"),
        "stale_documents",
        "users",
        ["reviewed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(op.f("ck_stale_documents_status"), "stale_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_stale_documents_status"),
        "stale_documents",
        "status IN ('open', 'dismissed', 'cleared')",
    )
    op.create_check_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"),
        "stale_documents",
        "status <> 'dismissed' OR (reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
    )


def downgrade() -> None:
    """Reopen every dismissal, then drop what recorded it."""
    op.execute("UPDATE stale_documents SET status = 'open' WHERE status = 'dismissed'")
    op.drop_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"), "stale_documents", type_="check"
    )
    op.drop_constraint(op.f("ck_stale_documents_status"), "stale_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_stale_documents_status"), "stale_documents", "status IN ('open', 'cleared')"
    )
    op.drop_constraint(
        op.f("fk_stale_documents_reviewed_by_id_users"), "stale_documents", type_="foreignkey"
    )
    op.drop_column("stale_documents", "review_note")
    op.drop_column("stale_documents", "reviewed_at")
    op.drop_column("stale_documents", "reviewed_by_id")
