"""Record the renderer version each approval was granted under.

Adds ``design_project_revisions.approved_renderer_version``, nullable. An
approval counts only while it equals ``app.design.RENDERER_VERSION``, so a
material change to what the exports print stops an approval counting instead
of printing a different file as the same approval.

No backfill: the code reads NULL as ``RENDERER_BASELINE`` ("unversioned"),
which matches no renderer version. Which renderer an earlier approval was
exported with was never recorded, and export content changed while such
approvals existed, so they stop counting and are granted again on a new
revision. Nothing is computed here, because the renderer is code, not data.

Downgrade drops the column: approvals count again without a renderer check.

Revision ID: c5d1e8a3f7b2
Revises: b4e8d2f6a9c1
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d1e8a3f7b2"
down_revision: str | None = "b4e8d2f6a9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "design_project_revisions"


def upgrade() -> None:
    """Add the nullable column."""
    op.add_column(_TABLE, sa.Column("approved_renderer_version", sa.String(40), nullable=True))


def downgrade() -> None:
    """Drop it."""
    op.drop_column(_TABLE, "approved_renderer_version")
