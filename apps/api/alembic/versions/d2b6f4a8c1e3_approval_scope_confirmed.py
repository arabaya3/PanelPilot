"""Record the approval-scope statement an approver confirmed (M6d).

Adds the nullable ``design_project_revisions.approved_scope_version``, set
with each approval to the version of the statement of what a review approval
covers and does not (``app.design.approval_scope``) that the approver ticked.
An approval counts only while it equals the current statement's version.

Existing approvals are left NULL: no confirmation was asked, and none is
invented. NULL reads as not confirmed (``approval_changed`` names
``scope``); those approvals are stale under the renderer bump anyway.

A column, not a field of the bound requirements record: the confirmation is
part of the approval act (like its note and versions), made after the record
is final, and writing it into the record would change the very hash the
approval binds.

Downgrade drops the column; confirmations are lost.

Revision ID: d2b6f4a8c1e3
Revises: e9d3b7a2c5f4
Create Date: 2026-11-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2b6f4a8c1e3"
down_revision: str | None = "e9d3b7a2c5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_scope_version", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_project_revisions", "approved_scope_version")
