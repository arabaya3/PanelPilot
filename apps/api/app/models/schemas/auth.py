"""Authentication and authorization schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    """Capability a caller may hold."""

    ENGINEER = "engineer"
    REVIEWER = "reviewer"
    INGESTION = "ingestion"
    ADMIN = "admin"


class CurrentUser(BaseModel):
    """The authenticated caller, as resolved from their credentials."""

    id: str
    email: str
    # The isolation boundary, carried on every authenticated request. Present
    # on the caller rather than looked up per query, so a domain function
    # cannot forget which tenant it is acting for.
    tenant_id: str
    roles: frozenset[Role]
    # The server-side sign-in (`auth_sessions`) an account's access token
    # belongs to; `None` for a trial, which has no sign-in to end.
    session_id: str | None = None
    # The refresh generation the access token was issued with (`rgen`);
    # `None` for a trial or a token from before it was carried.
    refresh_generation: int | None = None

    def has_role(self, role: Role) -> bool:
        """Report whether the caller holds a role.

        Args:
            role: The role to check for.

        Returns:
            ``True`` if the caller holds it.
        """
        return role in self.roles


class Permission(StrEnum):
    """An action the server checks the caller may take (``app.domain.permissions``).

    Sent to the client so it can hide or disable what it may not do; the
    server checks it again on every request regardless.
    """

    APPROVE_REVISION = "approve_revision"
    DELETE_PROJECT = "delete_project"
    EDIT_COMPANY_SETTINGS = "edit_company_settings"
