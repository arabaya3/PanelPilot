"""Retract a live document's passages, with who, when and why.

ADR 0001 names retraction as the audited operation on production for an
urgent removal. ``retraction_audits`` is its record, append-only like
``promotion_audits``, and the revisions it lists are ones promotion refuses
to publish again. ``stale_documents`` gains a ``retracted`` status, reviewed
like a dismissal.

Revision ID: f1ee20b60261
Revises: 3c5e1f7a9b2d
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1ee20b60261"
down_revision: str | None = "3c5e1f7a9b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``retraction_audits`` and allow ``retracted`` stale documents."""
    op.create_table(
        "retraction_audits",
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_retraction_audits_reviewer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retraction_audits")),
    )
    op.create_index(
        op.f("ix_retraction_audits_reviewer_id"), "retraction_audits", ["reviewer_id"], unique=False
    )
    op.create_index(
        op.f("ix_retraction_audits_source_url"), "retraction_audits", ["source_url"], unique=False
    )

    # Check constraints are invisible to autogenerate; replaced by hand.
    op.drop_constraint(op.f("ck_stale_documents_status"), "stale_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_stale_documents_status"),
        "stale_documents",
        "status IN ('open', 'dismissed', 'retracted', 'cleared')",
    )
    op.drop_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"), "stale_documents", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"),
        "stale_documents",
        "status NOT IN ('dismissed', 'retracted') "
        "OR (reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
    )


def downgrade() -> None:
    """Drop them again. A retracted flag reads ``cleared``: nothing live cites it."""
    op.execute("UPDATE stale_documents SET status = 'cleared' WHERE status = 'retracted'")
    op.drop_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"), "stale_documents", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_stale_documents_dismissal_reviewed"),
        "stale_documents",
        "status <> 'dismissed' OR (reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
    )
    op.drop_constraint(op.f("ck_stale_documents_status"), "stale_documents", type_="check")
    op.create_check_constraint(
        op.f("ck_stale_documents_status"),
        "stale_documents",
        "status IN ('open', 'dismissed', 'cleared')",
    )
    op.drop_index(op.f("ix_retraction_audits_source_url"), table_name="retraction_audits")
    op.drop_index(op.f("ix_retraction_audits_reviewer_id"), table_name="retraction_audits")
    op.drop_table("retraction_audits")
