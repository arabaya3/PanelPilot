"""ORM models for diagnostic sessions and their turns."""

from __future__ import annotations

from app.models.tables.base import Base, TimestampMixin


class DiagnosticSessionRow(Base, TimestampMixin):
    """A diagnostic conversation belonging to one user."""

    __tablename__ = "diagnostic_sessions"


class DiagnosticTurnRow(Base, TimestampMixin):
    """One question/answer exchange, with its citations and confidence."""

    __tablename__ = "diagnostic_turns"
