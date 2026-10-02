"""Approval of a saved design revision.

A revision is approved once, by the engineer who signs it: the name the
title block prints, the account that approved, and when. Never cleared.

Revision ID: d4a7f2c9e1b6
Revises: c8d2e5f1a7b3
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a7f2c9e1b6"
down_revision: str | None = "c8d2e5f1a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the approval columns to ``design_project_revisions``."""
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_account", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the approval columns."""
    op.drop_column("design_project_revisions", "approved_at")
    op.drop_column("design_project_revisions", "approved_account")
    op.drop_column("design_project_revisions", "approved_by")
