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

    def has_role(self, role: Role) -> bool:
        """Report whether the caller holds a role.

        Args:
            role: The role to check for.

        Returns:
            ``True`` if the caller holds it.
        """
        return role in self.roles
