"""ORM models for users and roles.

Table definitions only. Columns are added alongside the Alembic migration that
creates them — never one without the other.

Roles are a table rather than an enum column because ADR 0001's four-eyes rule
is enforced by asking whether a caller holds the reviewer role, and that
question has to be answerable in a join.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.tables.base import Base, TimestampMixin, UUIDPrimaryKey

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(UUIDPrimaryKey, TimestampMixin, Base):
    """A person who signs in to PanelPilot."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, back_populates="users")


class Role(UUIDPrimaryKey, TimestampMixin, Base):
    """A capability grant, e.g. ``reviewer`` or ``ingestion``.

    Names match ``app.models.schemas.auth.Role`` — the enum is the contract,
    this table is the storage.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")


__all__ = ["Role", "User", "user_roles"]
