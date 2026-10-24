"""ORM models for authentication sessions and anonymous trial sessions.

Refresh tokens are stored hashed, never in the clear: a leaked database dump
must not hand someone a set of working credentials.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class AuthSessionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """One sign-in: what a login or signup starts and a logout ends.

    Its id is the ``sid`` claim every access token of the sign-in carries, and
    every refresh token rotated from it names it. Revoking this row is what
    makes a logout effective at once: ``authenticate`` refuses an access token
    whose session is revoked, and ``refresh`` refuses to rotate any of its
    refresh tokens, so neither a copied access token nor a copied refresh
    token outlives the logout.

    Revoked rather than deleted, like refresh tokens, so a replay of anything
    that belonged to it is recognised for what it is.
    """

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Random, per session. With the server's key it derives each generation's
    # refresh token (`security.derive_refresh_token`), so the live token can be
    # handed again to a tab that raced its rotation without storing it. Alone
    # it derives nothing: a leaked row is still not a credential. NULL for a
    # session from before this was kept; its next rotation sets it.
    token_salt: Mapped[str | None] = mapped_column(String(64))


class RefreshTokenRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A issued refresh token, stored as a hash.

    Rotation is the reason rows are revoked rather than deleted: a refresh
    token presented twice is a replay, and that is only detectable if the
    superseded row is still there to recognise.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 of the token. Unique so a replayed value cannot create a second
    # live row alongside the first.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The sign-in this token belongs to. Nullable only for tokens issued before
    # sessions existed; those are refused (see ``auth.refresh``), never treated
    # as a session of their own.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    # Its place in the session's chain: login mints 1, each rotation the next.
    # Only the live token's immediate predecessor is answered inside the reuse
    # window (`auth.refresh`). NULL for tokens minted before this was kept,
    # which count as 0.
    generation: Mapped[int | None] = mapped_column(Integer)


class AnonymousSessionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A trial conversation started before signup.

    Tenant-scoped from creation, because ``diagnostic_sessions.tenant_id`` is
    NOT NULL and the spec is explicit that the schema never special-cases "no
    tenant". A visitor starting a trial gets a **provisional** tenant
    immediately; at signup the new user joins that same tenant rather than
    getting a fresh one.

    That is what makes claiming cheap and safe: the conversation history is
    already under the tenant the user ends up in, so nothing is copied or
    re-pointed and there is no window where rows belong to nobody.
    """

    __tablename__ = "anonymous_sessions"

    diagnostic_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Set once, at claim time. Nullable because an unclaimed session is the
    # normal state, not an error.
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The session id alone must NOT be enough to claim a trial. It travels in
    # URLs and request bodies and is not secret, so treating it as a bearer
    # credential let anyone who learned one join that session's tenant as a
    # full user. This secret is generated when the trial starts, held only by
    # the browser that started it, and compared in constant time at claim.
    claim_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Expiry is DERIVED, not a flag. A stored boolean nothing ever sets means
    # trial sessions live forever, which keeps them claimable forever.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_expired(self) -> bool:
        """Report whether this trial session is past its expiry."""
        return datetime.now(UTC) >= self.expires_at
