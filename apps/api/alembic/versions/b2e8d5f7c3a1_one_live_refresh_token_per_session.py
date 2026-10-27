"""Leave every session one live refresh token; restore the grace marker column.

Two things, both idempotent:

1. **One live token per session.** The once-only grace model (``e7b2c9d4a1f3``)
   could leave a session with two live tokens. The code now rotates only a
   session's newest live token, and this revokes the others: every live token
   that is not its session's newest (by ``created_at``, then ``id``).
2. **``refresh_tokens.grace_used_at`` exists.** An unmerged earlier version of
   ``a9d3f6b1c8e4`` dropped it, and ran on the dev and disposable databases;
   the merged version keeps it for rolling deploys. Re-added here where it is
   missing, so both kinds of database end in the same schema. A new revision
   rather than relying on the edited one, because an applied revision is never
   run again.

The downgrade changes nothing: which databases had the column re-added is not
recorded, and a revoked token is not brought back to life.

Revision ID: b2e8d5f7c3a1
Revises: a9d3f6b1c8e4
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2e8d5f7c3a1"
down_revision: str | None = "a9d3f6b1c8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Restore the column if missing, then revoke all but each session's newest live token."""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("refresh_tokens")}
    if "grace_used_at" not in columns:
        op.add_column(
            "refresh_tokens",
            sa.Column("grace_used_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute("""
        UPDATE refresh_tokens
           SET revoked_at = now()
         WHERE revoked_at IS NULL
           AND session_id IS NOT NULL
           AND id NOT IN (
                 SELECT DISTINCT ON (session_id) id
                   FROM refresh_tokens
                  WHERE revoked_at IS NULL AND session_id IS NOT NULL
               ORDER BY session_id, created_at DESC, id DESC
               )
        """)


def downgrade() -> None:
    """Nothing to undo; see the module docstring."""
