"""ORM models for users and roles.

Table definitions only. Columns are added alongside the Alembic migration that
creates them — never one without the other.
"""

from __future__ import annotations

from app.models.tables.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """A person who signs in to PanelPilot."""

    __tablename__ = "users"


class Role(Base, TimestampMixin):
    """A capability grant, e.g. ``reviewer`` or ``ingestion``."""

    __tablename__ = "roles"
