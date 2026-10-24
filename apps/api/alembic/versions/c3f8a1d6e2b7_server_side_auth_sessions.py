"""Server-side sign-in sessions, so a logout ends the access token too.

Adds ``auth_sessions`` (one row per sign-in, revocable) and a nullable
``refresh_tokens.session_id`` naming the sign-in a refresh token belongs to.

Backwards compatible by construction: nothing existing is rewritten. Refresh
tokens issued before this have ``session_id`` NULL and access tokens issued
before it carry no ``sid`` claim; both are refused from this revision on (see
``app.domain.auth``), so an account signed in at deploy signs in once more.
The downgrade drops the column and the table; tokens issued meanwhile then
carry a ``sid`` the older code ignores.

Revision ID: c3f8a1d6e2b7
Revises: b4e7a9c2d1f6
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3f8a1d6e2b7"
down_revision: str | None = "b4e7a9c2d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sessions table and link refresh tokens to it."""
    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_auth_sessions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
    )
    op.create_index(op.f("ix_auth_sessions_tenant_id"), "auth_sessions", ["tenant_id"])
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])

    op.add_column("refresh_tokens", sa.Column("session_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_refresh_tokens_session_id_auth_sessions"),
        "refresh_tokens",
        "auth_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"])


def downgrade() -> None:
    """Drop the link and the table; refresh tokens themselves are kept."""
    op.drop_index(op.f("ix_refresh_tokens_session_id"), table_name="refresh_tokens")
    op.drop_constraint(
        op.f("fk_refresh_tokens_session_id_auth_sessions"), "refresh_tokens", type_="foreignkey"
    )
    op.drop_column("refresh_tokens", "session_id")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_tenant_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
