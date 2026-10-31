"""Record that a spent refresh token's one reuse inside the window was used.

Adds nullable ``refresh_tokens.grace_used_at``. A token rotated a moment ago
may be presented once more by a refresh that raced the rotation; the second
such presentation is a replay. Existing rows start NULL, which is right: none
has been honoured inside the window yet.

Revision ID: e7b2c9d4a1f3
Revises: c3f8a1d6e2b7
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b2c9d4a1f3"
down_revision: str | None = "c3f8a1d6e2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the column."""
    op.add_column(
        "refresh_tokens", sa.Column("grace_used_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Drop it; the window is then unbounded again, as before."""
    op.drop_column("refresh_tokens", "grace_used_at")
