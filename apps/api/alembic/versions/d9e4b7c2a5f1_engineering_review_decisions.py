"""Record engineering reviewer grants and review decisions, append-only (M3).

Creates ``engineering_reviewer_grants``, ``engineering_review_decisions``
(both tenant-scoped) and ``global_engineering_review_decisions`` (no tenant;
written by an operator), with a trigger function that refuses ``UPDATE`` and
``DELETE`` on all three. No existing row is changed, and nothing is
inserted: no reviewer is designated and no decision is recorded.

Downgrade drops the triggers, the function and the tables. Any recorded
decision is lost with them, and stored designs read under M2's rule again
(any review status other than ``unreviewed`` refused).

Revision ID: d9e4b7c2a5f1
Revises: c5d1e8a3f7b2
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d9e4b7c2a5f1"
down_revision: str | None = "c5d1e8a3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = "pp_refuse_append_only_change"
_TABLES = (
    "engineering_reviewer_grants",
    "engineering_review_decisions",
    "global_engineering_review_decisions",
)
_KINDS = "subject_kind IN ('template', 'pattern', 'rule')"
_DECISIONS = "decision IN ('approved', 'rejected', 'withdrawn')"
_DIGEST = "subject_digest ~ '^[0-9a-f]{64}$'"


def _now() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _seq() -> sa.Column[int]:
    return sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False)


def upgrade() -> None:
    """Create the tables, then the trigger that keeps them append-only."""
    op.create_table(
        "engineering_reviewer_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        _seq(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("qualification_statement", sa.Text(), nullable=True),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=False),
        _now(),
        sa.CheckConstraint(
            "action IN ('grant', 'revoke')", name=op.f("ck_engineering_reviewer_grants_action")
        ),
        sa.CheckConstraint(
            "(action = 'grant') = (qualification_statement IS NOT NULL)",
            name=op.f("ck_engineering_reviewer_grants_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_engineering_reviewer_grants_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="RESTRICT",
            name="fk_engineering_reviewer_grants_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_engineering_reviewer_grants"),
        sa.UniqueConstraint("seq", name="uq_engineering_reviewer_grants_seq"),
    )
    op.create_index(
        "ix_engineering_reviewer_grants_tenant_id", "engineering_reviewer_grants", ["tenant_id"]
    )
    op.create_index(
        "ix_engineering_reviewer_grants_user_seq",
        "engineering_reviewer_grants",
        ["user_id", "seq"],
    )

    op.create_table(
        "engineering_review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        _seq(),
        sa.Column("subject_kind", sa.String(10), nullable=False),
        sa.Column("subject_ref", sa.String(160), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("qualification_statement", sa.Text(), nullable=False),
        sa.Column("standards", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _now(),
        sa.CheckConstraint(_KINDS, name=op.f("ck_engineering_review_decisions_subject_kind")),
        sa.CheckConstraint(_DECISIONS, name=op.f("ck_engineering_review_decisions_decision")),
        sa.CheckConstraint(_DIGEST, name=op.f("ck_engineering_review_decisions_subject_digest")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_engineering_review_decisions_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="RESTRICT",
            name="fk_engineering_review_decisions_reviewer_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_engineering_review_decisions"),
        sa.UniqueConstraint("seq", name="uq_engineering_review_decisions_seq"),
    )
    op.create_index(
        "ix_engineering_review_decisions_tenant_id", "engineering_review_decisions", ["tenant_id"]
    )
    op.create_index(
        "ix_engineering_review_decisions_subject",
        "engineering_review_decisions",
        ["tenant_id", "subject_kind", "subject_ref", "seq"],
    )

    op.create_table(
        "global_engineering_review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        _seq(),
        sa.Column("subject_kind", sa.String(10), nullable=False),
        sa.Column("subject_ref", sa.String(160), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("reviewer_label", sa.String(200), nullable=False),
        sa.Column("qualification_statement", sa.Text(), nullable=False),
        sa.Column("standards", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(200), nullable=False),
        _now(),
        sa.CheckConstraint(
            _KINDS, name=op.f("ck_global_engineering_review_decisions_subject_kind")
        ),
        sa.CheckConstraint(
            _DECISIONS, name=op.f("ck_global_engineering_review_decisions_decision")
        ),
        sa.CheckConstraint(
            _DIGEST, name=op.f("ck_global_engineering_review_decisions_subject_digest")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_global_engineering_review_decisions"),
        sa.UniqueConstraint("seq", name="uq_global_engineering_review_decisions_seq"),
    )
    op.create_index(
        "ix_global_engineering_review_decisions_subject",
        "global_engineering_review_decisions",
        ["subject_kind", "subject_ref", "seq"],
    )

    op.execute(f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """)
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()"
        )


def downgrade() -> None:
    """Drop the triggers, the function and the tables."""
    for table in reversed(_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.drop_table(table)
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
