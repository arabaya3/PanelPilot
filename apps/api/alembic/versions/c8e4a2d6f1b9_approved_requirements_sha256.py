"""Bind an approval to the requirements record it was granted with (M6b).

Adds the nullable ``design_project_revisions.approved_requirements_sha256``,
set with each approval: the revision's ``requirements_sha256`` then, or the
hash of JSON ``null`` for a revision with no stored record (a derived one).
An approval counts only while it equals the record's hash now.

Existing approvals are left NULL: what record they were granted with was
never recorded, and no hash is invented for them. NULL reads as *not bound*
(``approval_changed`` names ``requirements``): such an approval is not
current. Those approvals predate the rules bump and are not current anyway.

Downgrade drops the column; approvals then no longer bind the record.

Revision ID: c8e4a2d6f1b9
Revises: b7d3f1a9c2e4
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e4a2d6f1b9"
down_revision: str | None = "b7d3f1a9c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_requirements_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_project_revisions", "approved_requirements_sha256")
