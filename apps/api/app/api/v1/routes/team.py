"""A tenant's team: members and invitations.

Thin by contract: parse, call one domain function, return. Who may invite,
and how many seats a plan holds, lives in ``app.domain.team``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import team as team_domain
from app.models.schemas.team import InvitationCreated, InvitationOut, InviteRequest, Team

router = APIRouter()


@router.get("", response_model=Team)
def get_team(session: SessionDep, user: CurrentUserDep) -> Team:
    """The caller's team and its seats."""
    return team_domain.team(session=session, user=user)


@router.post("/invitations", response_model=InvitationCreated, status_code=status.HTTP_201_CREATED)
def invite(payload: InviteRequest, session: SessionDep, user: CurrentUserDep) -> InvitationCreated:
    """Invite a colleague; the token is shown this once, for the owner to send."""
    created = team_domain.invite(session=session, user=user, email=payload.email)
    session.commit()
    return created


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(session: SessionDep, user: CurrentUserDep) -> list[InvitationOut]:
    """The invitations still open."""
    return team_domain.invitations(session=session, user=user)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(invitation_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Withdraw an open invitation."""
    team_domain.revoke(session=session, user=user, invitation_id=invitation_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(member_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Take a member out of the team."""
    team_domain.remove(session=session, user=user, member_id=member_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
