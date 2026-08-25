"""ORM models for diagnostic sessions and their turns."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.tables.tenant import TenantScopedMixin


class DiagnosticSessionRow(TenantScopedMixin, UUIDPrimaryKey, TimestampMixin, Base):
    """A diagnostic conversation belonging to one user."""

    __tablename__ = "diagnostic_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
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

    session: Mapped[DiagnosticSessionRow] = relationship(back_populates="turns")
