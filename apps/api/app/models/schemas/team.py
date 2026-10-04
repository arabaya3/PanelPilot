"""Request and response schemas for a tenant's team."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class Member(BaseModel):
    """One account in the team.

    Attributes:
        id: The account.
        email: Its address.
        full_name: Its name, if given.
        owner: Whether it manages the team.
    """

    id: str
    email: str
    full_name: str | None = None
    owner: bool


class Team(BaseModel):
    """The caller's team.

    Attributes:
        members: Its active accounts, owner first.
        owner: Whether the caller manages it.
        seats: The seats the plan holds; ``None`` while billing is not
            enforced.
        seats_taken: Members plus invitations still open.
    """

    members: list[Member]
    owner: bool
    seats: int | None
    seats_taken: int


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
