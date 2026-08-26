"""Record a diagnostic turn's outcome, and make its ordering collision-proof.

A stored turn kept only its text, so replaying history could not tell an
answer from a refusal — a past successful diagnosis reloaded as "the assistant
declined to help". ``refused`` records what actually happened.

``position`` had no uniqueness, so two concurrent turns could both read the
same maximum and write the same position, leaving the conversation to order
them arbitrarily and interleave question with answer.

Revision ID: c14f7a2b9e30
Revises: e65a2d3543ef
Create Date: 2026-08-26

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c14f7a2b9e30"
down_revision: str | None = "e65a2d3543ef"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the outcome columns and the ordering constraint."""
    # `server_default` so the ADD succeeds on a populated table: an existing
    # turn predates the distinction, and false ("was answered") is the right
    # reading for rows written before refusals were recorded separately —
    # refusals were stored the same way, but calling a past answer a refusal
    # is the more alarming error of the two.
    op.add_column(
        "diagnostic_turns",
        sa.Column("refused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "diagnostic_turns",
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0")),
    )

    # Deduplicate before constraining, or the ADD fails on any database that
    # already raced. Renumbers duplicates rather than deleting a turn: a
    # conversation missing an exchange is worse than one whose numbering
    # shifted.
    op.execute(
        """
        WITH renumbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY session_id ORDER BY position, created_at, id
                   ) AS corrected
            FROM diagnostic_turns
        )
        UPDATE diagnostic_turns AS t
        SET position = r.corrected
        FROM renumbered AS r
        WHERE t.id = r.id AND t.position <> r.corrected
        """
    )
    op.create_unique_constraint(
        "uq_diagnostic_turns_session_position", "diagnostic_turns", ["session_id", "position"]
    )


def downgrade() -> None:
    """Drop the constraint and the outcome columns."""
    op.drop_constraint(
        "uq_diagnostic_turns_session_position", "diagnostic_turns", type_="unique"
    )
    op.drop_column("diagnostic_turns", "confidence")
    op.drop_column("diagnostic_turns", "refused")
