"""Store each new revision's voltage-drop calculation record, ``pp-vdrec/1`` (ADR 0004).

Adds three nullable columns to ``design_project_revisions``:

* ``calculation_record`` (JSONB): what the design computed for voltage drop
  when the revision was saved, bound to its snapshot, profile and rules;
* ``calculation_record_sha256``: its SHA-256, checked on every read;
* ``approved_calculation_record_sha256``: the record hash an approval was
  granted with, set in the approval's locked transaction.

No existing row is changed and nothing is backfilled: an older revision has
no record and reads as not recorded, and its approval (with no binding) keeps
counting.

Downgrade drops the three columns; every stored record and approval binding
is lost with them. Code without this change ignores them.

Revision ID: c3f7a9e2d4b6
Revises: a7d3e9c1b5f2
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3f7a9e2d4b6"
down_revision: str | None = "a7d3e9c1b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "design_project_revisions",
        sa.Column("calculation_record", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("calculation_record_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("approved_calculation_record_sha256", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_project_revisions", "approved_calculation_record_sha256")
    op.drop_column("design_project_revisions", "calculation_record_sha256")
    op.drop_column("design_project_revisions", "calculation_record")
