"""Store the server-built design of each saved revision, and bind approval to it.

Adds nullable columns to ``design_project_revisions``: the design snapshot the
server built from the request, its canonical SHA-256, the profile hash, the
rules and catalogue versions, the findings, and the snapshot hash an approval
was given for. Every column is nullable, so revisions saved before this keep
working as they are (they open, and must be saved again to be approved).

Revision ID: b4e7a9c2d1f6
Revises: a7d1e3f5b2c8
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4e7a9c2d1f6"
down_revision: str | None = "a7d1e3f5b2c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "design_project_revisions"


def upgrade() -> None:
    """Add the snapshot, hash, version, findings and approval-binding columns."""
    op.add_column(
        _TABLE,
        sa.Column("design_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(_TABLE, sa.Column("snapshot_sha256", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("profile_sha256", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("rules_version", sa.String(length=40), nullable=True))
    op.add_column(_TABLE, sa.Column("catalogue_version", sa.String(length=40), nullable=True))
    op.add_column(
        _TABLE, sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("approved_snapshot_sha256", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    """Drop the columns; the requests and approvals themselves are kept."""
    for column in (
        "approved_snapshot_sha256",
        "findings",
        "catalogue_version",
        "rules_version",
        "profile_sha256",
        "snapshot_sha256",
        "design_snapshot",
    ):
        op.drop_column(_TABLE, column)
