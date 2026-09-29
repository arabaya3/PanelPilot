"""Queue crawl jobs for the worker instead of running them in the request.

A crawl ran inside ``POST /ingestion/crawl-jobs``: fetching, PDF parsing,
embedding and staging, for minutes, on a request thread holding a database
transaction. ADR 0002 exists to keep exactly that off the API runtime.

The route now records the request and returns; the worker claims queued jobs
and runs them. For that the job row has to carry what used to live only in
the HTTP request:

- ``request``: the crawl request itself, so the worker can run it.
- ``requested_by``: the subject who asked for it — recorded as the ingester of
  record on everything the crawl stages, which is what the four-eyes check at
  promotion compares against. A string, not a foreign key: the system actor
  that scheduled crawls run as is deliberately not a row in ``users``.
- ``error``: why a failed job failed, which the row could not say before.
- ``started_at`` / ``finished_at``: when a worker claimed it and when it ended,
  so a job abandoned by a worker that died can be recognised as abandoned.

All nullable: rows written before this ran inline and have none of it.

Revision ID: a1d4e7f2c9b3
Revises: f3a9c2d4e5b1
Create Date: 2026-09-29

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1d4e7f2c9b3"
down_revision: str | None = "f3a9c2d4e5b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add what a queued job needs to be run later, and to explain its end."""
    op.add_column(
        "crawl_jobs",
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("crawl_jobs", sa.Column("requested_by", sa.String(length=64), nullable=True))
    op.add_column("crawl_jobs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("crawl_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("crawl_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop them again."""
    for column in ("finished_at", "started_at", "error", "requested_by", "request"):
        op.drop_column("crawl_jobs", column)
