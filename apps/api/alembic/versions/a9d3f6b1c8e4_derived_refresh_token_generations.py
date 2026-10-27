"""Derive a session's refresh tokens per generation.

Adds nullable ``auth_sessions.token_salt`` and ``refresh_tokens.generation``.
A tab that raced a rotation is handed the same live successor, re-derived from
the salt and the refresh-token key, instead of a once-only extra pair.

``refresh_tokens.grace_used_at`` is kept, unused. Dropping it here would break
a rolling deploy: pods still running the previous code select and write it. A
later contract migration, once no such pod can exist, drops it.

(An earlier, unmerged version of this revision dropped the column, and it ran
on the dev and disposable databases. Editing an applied revision is acceptable
only because it was never merged; ``b2e8d5f7c3a1`` restores the column where
that version ran, so both kinds of database converge.)

Backwards compatible: existing sessions have no salt and existing tokens no
generation (read as 0). A legacy session's first rotation after this sets its
salt and mints generation 1, and a tab racing that rotation is answered like
any other. What is not re-issued is a legacy live token itself, which was
minted at random: it can only rotate.

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
    """Add the salt and the generation."""
    op.add_column("auth_sessions", sa.Column("token_salt", sa.String(length=64), nullable=True))
    op.add_column("refresh_tokens", sa.Column("generation", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop the salt and the generation."""
    op.drop_column("refresh_tokens", "generation")
    op.drop_column("auth_sessions", "token_salt")
