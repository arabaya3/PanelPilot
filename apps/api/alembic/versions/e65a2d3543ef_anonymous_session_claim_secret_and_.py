"""anonymous session claim secret and derived expiry

Revision ID: e65a2d3543ef
Revises: 6378472e8d97
Create Date: 2026-08-25 23:45:57.258601
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e65a2d3543ef"
down_revision: str | None = "6378472e8d97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing trial sessions predate the claim secret and cannot produce one.
    # They get an unmatchable placeholder rather than a usable value: a session
    # whose ownership nobody can prove must become UNCLAIMABLE, not claimable
    # by anyone. Backfilling something guessable here would reintroduce the
    # cross-tenant takeover the secret exists to prevent.
    op.add_column(
        "anonymous_sessions",
        sa.Column(
            "claim_secret_hash",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'unclaimable-legacy-session'"),
        ),
    )
    # Dropped so new rows must supply a real hash.
    op.alter_column("anonymous_sessions", "claim_secret_hash", server_default=None)

    # Expiry is derived from this column now, replacing a stored boolean that
    # nothing ever set -- which meant trial sessions never expired at all.
    # Existing rows expire immediately; an unclaimable legacy session should
    # not linger.
    op.add_column(
        "anonymous_sessions",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.alter_column("anonymous_sessions", "expires_at", server_default=None)

    op.drop_column("anonymous_sessions", "is_expired")


def downgrade() -> None:
    op.add_column(
        "anonymous_sessions",
        sa.Column(
            "is_expired",
            sa.BOOLEAN(),
            autoincrement=False,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("anonymous_sessions", "is_expired", server_default=None)
    op.drop_column("anonymous_sessions", "expires_at")
    op.drop_column("anonymous_sessions", "claim_secret_hash")
