"""Store the equipment context a turn was answered about.

The conversation history sidebar (FE-011) must restore a past session's context
indicator, not just its messages. That indicator is driven by
``StructuredDiagnosis.equipment_model``, which lives only on the live response:
nothing wrote it down, so a replayed turn came back with it unset and the chip
reloaded blank.

Re-deriving it from the stored prose was the alternative and is worse — it
would mean guessing a model number out of an answer, which is exactly what the
chip's neutral state exists to prevent. Recording what was actually shown is
the only version that cannot be wrong.

Nullable with no backfill. Turns written before this migration genuinely have
no recorded context, and inventing one for them would put a model number on a
conversation nobody ever identified.

Revision ID: a7c31f95d2b8
Revises: e0224010bab8
Create Date: 2026-08-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c31f95d2b8"
down_revision: str | None = "e0224010bab8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the recorded equipment context to a diagnostic turn."""
    op.add_column(
        "diagnostic_turns",
        sa.Column("equipment_model", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("diagnostic_turns", "equipment_model")
