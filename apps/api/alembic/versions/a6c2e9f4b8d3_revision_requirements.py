"""Store each revision's requirements record, ``pp-req/1`` (M6a).

Adds two nullable columns to ``design_project_revisions``: ``requirements``
(JSONB, the record) and ``requirements_sha256`` (its SHA-256, checked on
every read like the snapshot hash). No existing row is changed: an older
revision has neither, and the API derives a record for it (``derived``),
which never counts as acknowledged.

Downgrade drops both columns; every stored record and acknowledgement is
lost with them.

Revision ID: a6c2e9f4b8d3
Revises: d9e4b7c2a5f1
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a6c2e9f4b8d3"
down_revision: str | None = "d9e4b7c2a5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "design_project_revisions",
        sa.Column("requirements", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "design_project_revisions",
        sa.Column("requirements_sha256", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_project_revisions", "requirements_sha256")
    op.drop_column("design_project_revisions", "requirements")
