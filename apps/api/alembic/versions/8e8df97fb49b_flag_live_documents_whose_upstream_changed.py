"""Flag live documents whose upstream source changed or was withdrawn.

``expire-stale-sources`` asks each source whether it still serves the
revision production was verified against, and records the answer here: one
row per source URL, ``superseded`` when the bytes differ and ``withdrawn`` when
the source answers 404 or 410. A flag, not a retraction -- the live chunks
stay live until a reviewer decides. See ADR 0001.

Revision ID: 8e8df97fb49b
Revises: a1d4e7f2c9b3
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e8df97fb49b"
down_revision: str | None = "a1d4e7f2c9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``stale_documents``."""
    op.create_table(
        "stale_documents",
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("published_hashes", sa.Text(), nullable=False),
        sa.Column("upstream_hash", sa.String(length=64), nullable=True),
        sa.Column("first_flagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
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
            "reason IN ('superseded', 'withdrawn')", name=op.f("ck_stale_documents_reason")
        ),
        sa.CheckConstraint("status IN ('open', 'cleared')", name=op.f("ck_stale_documents_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stale_documents")),
        sa.UniqueConstraint("source_url", name=op.f("uq_stale_documents_source_url")),
    )
    op.create_index(
        op.f("ix_stale_documents_source_id"), "stale_documents", ["source_id"], unique=False
    )
    op.create_index(op.f("ix_stale_documents_status"), "stale_documents", ["status"], unique=False)


def downgrade() -> None:
    """Drop it again."""
    op.drop_index(op.f("ix_stale_documents_status"), table_name="stale_documents")
    op.drop_index(op.f("ix_stale_documents_source_id"), table_name="stale_documents")
    op.drop_table("stale_documents")
