"""ORM models for diagnostic sessions and their turns."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class DiagnosticSessionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A diagnostic conversation belonging to one user."""

    __tablename__ = "diagnostic_sessions"

    # Nullable because an anonymous trial session (BE-002/FE-008) exists
    # before anyone has signed up. It is never tenant-less -- the trial gets a
    # provisional tenant immediately -- but it genuinely has no user until the
    # session is claimed. SET NULL rather than CASCADE for the same reason: a
    # deleted user must not silently take the conversation with them, since the
    # tenant may still be entitled to it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    turns: Mapped[list[DiagnosticTurnRow]] = relationship(
        back_populates="session", order_by="DiagnosticTurnRow.position"
    )


class DiagnosticTurnRow(UUIDPrimaryKey, TimestampMixin, Base):
    """One question/answer exchange, with its citations and confidence.

    Citations and the confidence breakdown are stored as rendered JSON rather
    than normalised: they are an immutable record of what the engineer was
    shown, not queryable state.
    """

    __tablename__ = "diagnostic_turns"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # Whether this turn was a refusal, recorded rather than inferred later:
    # replaying a stored answer as a refusal tells an engineer the assistant
    # declined when it did not, and the text alone cannot distinguish them.
    refused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # The score the engineer was shown, so history reports what they saw
    # rather than a zero that reads as no confidence at all.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    # The equipment the answer was about, as shown to the engineer at the time.
    # Recorded rather than re-derived: the history sidebar has to restore the
    # context indicator, and inferring a model number from the stored prose
    # later would guess -- which is the one thing the indicator must never do.
    # Nullable because plenty of turns never identify a specific unit.
    equipment_model: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[DiagnosticSessionRow] = relationship(back_populates="turns")

    __table_args__ = (
        # Two concurrent turns must not land on the same position: the history
        # would then order them arbitrarily, interleaving question and answer.
        # The domain takes a row lock to avoid the race; this is the backstop
        # if a future caller forgets.
        UniqueConstraint("session_id", "position", name="uq_diagnostic_turns_session_position"),
    )
