"""Record the account id of each revision's author (M6a).

Adds the nullable ``design_project_revisions.author_user_id``. Self-approval
(``approval_by_author``) compares on it, so an email changed after saving, or
an email later given to another account, neither defeats nor misdirects the
check. Existing rows are left NULL (nothing is guessed); they fall back to
the recorded ``author`` email, compared without regard to case.

Downgrade drops the column; rows then compare by email only.

Revision ID: b7d3f1a9c2e4
Revises: a6c2e9f4b8d3
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d3f1a9c2e4"
down_revision: str | None = "a6c2e9f4b8d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("design_project_revisions", sa.Column("author_user_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("design_project_revisions", "author_user_id")
