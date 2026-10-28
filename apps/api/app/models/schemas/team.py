"""Request and response schemas for a tenant's team."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class Member(BaseModel):
    """One account in the team.

    Attributes:
        id: The account.
        email: Its address.
        full_name: Its name, if given.
        owner: Whether it is the tenant's recorded owner.
    """

    id: str
    email: str
    full_name: str | None = None
    owner: bool


class Team(BaseModel):
    """The caller's team.

    Attributes:
        members: Its active accounts, the owner first.
        owner: Whether the caller manages it: the owner, or an admin.
        owns: Whether the caller is the recorded owner, the one account
            that may transfer ownership.
        seats: The seats the plan holds; ``None`` while billing is not
            enforced.
        seats_taken: Members plus invitations still open.
    """

    members: list[Member]
    owner: bool
    owns: bool
    seats: int | None
    seats_taken: int


class TransferOwnershipRequest(BaseModel):
    """Hand the tenant's ownership to another active member."""

    member_id: str = Field(min_length=1, max_length=64)


class OwnershipIssue(BaseModel):
    """A tenant with accounts but no active owner, for an operator.

    Attributes:
        tenant_id: The tenant.
        issue: ``no_owner`` or ``owner_inactive``.
        active_admins: Active admins who can still manage it; with none,
            nobody can until an operator runs ``assign-owner``.
    """

    tenant_id: str
    issue: Literal["no_owner", "owner_inactive"]
    active_admins: int


class InviteRequest(BaseModel):
    """Invite a colleague by email."""

    email: EmailStr


class InvitationOut(BaseModel):
    """An invitation still open.

    Attributes:
        id: The invitation.
        email: Who it is for.
        invited_by: Who sent it.
        expires_at: When it lapses (ISO 8601).
    """

    id: str
    email: str
    invited_by: str
    expires_at: str


class InvitationCreated(BaseModel):
    """A new invitation, with the token to send: shown this once.

    Attributes:
        id: The invitation.
        email: Who it is for.
        token: What the colleague signs up with.
        expires_at: When it lapses (ISO 8601).
    """

    id: str
    email: str
    token: str
    expires_at: str
