"""Record the approving account's id, and an optional approval note.

An approval names its approver by the signed-in account only. The account's
id is recorded beside its email (``approved_account``): an id never changes
nor passes to another person, while an email can be changed, and then given
to a new account. The note is what the engineer adds to an approval; it is
never an identity.

Both columns are nullable: a revision approved before them keeps its email
and its typed name (``approved_by``, no longer printed), and has neither.

Revision ID: f8c4a2e6d9b3
Revises: b2e8d5f7c3a1
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8c4a2e6d9b3"
down_revision: str | None = "b2e8d5f7c3a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``approved_user_id`` and ``approval_note`` to ``design_project_revisions``."""
    # No foreign key: the audit identity is kept as it was recorded, even if
    # the account is deleted later (it then resolves to no display name).
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("approval_note", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    """Drop the two columns."""
    op.drop_column("design_project_revisions", "approval_note")
    op.drop_column("design_project_revisions", "approved_user_id")
