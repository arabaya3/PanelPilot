"""Derive a session's refresh tokens per generation; drop the one-use grace marker.

Adds nullable ``auth_sessions.token_salt`` and ``refresh_tokens.generation``,
and drops ``refresh_tokens.grace_used_at``. A tab that raced a rotation is now
handed the same live successor (re-derived from the salt and the server key)
instead of a once-only extra pair, so the marker has no use.

Backwards compatible: existing sessions have no salt and existing tokens no
generation (read as 0). A session's next rotation sets its salt; until then its
live token cannot be re-derived, so a race in that one rotation is refused
rather than answered.

Revision ID: a9d3f6b1c8e4
Revises: e7b2c9d4a1f3
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9d3f6b1c8e4"
down_revision: str | None = "e7b2c9d4a1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the salt and the generation; drop the grace marker."""
    op.add_column("auth_sessions", sa.Column("token_salt", sa.String(length=64), nullable=True))
    op.add_column("refresh_tokens", sa.Column("generation", sa.Integer(), nullable=True))
    op.drop_column("refresh_tokens", "grace_used_at")


def downgrade() -> None:
    """Restore the marker (empty) and drop the salt and the generation."""
    op.add_column(
        "refresh_tokens", sa.Column("grace_used_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_column("refresh_tokens", "generation")
    op.drop_column("auth_sessions", "token_salt")
