"""A tenant's team: its members, and inviting colleagues into it.

The tenant's owner is its first account (or one granted ``admin``): only
the owner invites, revokes an invitation or removes a member. An invitation
names an email and carries a token the owner sends; the colleague signs up
with it (``auth.signup``) and joins this tenant. The token is shown once and
only its hash is kept.

Seats: once billing is enforced, the members and the invitations still open
together may not exceed the seats the plan holds (``billing.seat_limit``).

Every function scoped to a caller binds the session to its tenant first
(ADR 0003).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.security import hash_claim_secret
from app.core.tenancy import bind_tenant
from app.domain import billing
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.team import InvitationCreated, InvitationOut, Member, Team
from app.models.tables.team import InvitationRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

#: How long an invitation may wait to be accepted.
INVITATION_DAYS = 14


def _tenant(user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    return tenant


def _owner_id(session: Session) -> uuid.UUID | None:
    """The tenant's first active account."""
    return session.scalar(
        select(User.id).where(User.is_active.is_(True)).order_by(User.created_at, User.id).limit(1)
    )


def is_owner(*, session: Session, user: CurrentUser) -> bool:
    """Whether the caller manages the tenant's team.

    Args:
        session: Open database session.
        user: The caller.

    Returns:
        True for the tenant's first account, or one holding ``admin``.
    """
    bind_tenant(session, _tenant(user))
    return user.has_role(Role.ADMIN) or str(_owner_id(session)) == user.id


def _require_owner(session: Session, user: CurrentUser) -> uuid.UUID:
    tenant = _tenant(user)
    if not is_owner(session=session, user=user):
        raise AuthorizationError(
            "only the account's owner manages its team", code="team_owner_only"
        )
    return tenant


def _open(now: datetime) -> ColumnElement[bool]:
    return (InvitationRow.accepted_at.is_(None)) & (InvitationRow.expires_at > now)


def seats_taken(*, session: Session, now: datetime | None = None) -> int:
    """Members plus invitations still open, in the bound tenant.

    Args:
        session: A session bound to the tenant.
        now: Injected for tests.

    Returns:
        The seats they hold.
    """
    moment = now or datetime.now(UTC)
    members = session.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True)))
    waiting = session.scalar(select(func.count()).select_from(InvitationRow).where(_open(moment)))
    return (members or 0) + (waiting or 0)


def members(*, session: Session, user: CurrentUser) -> list[Member]:
    """The tenant's active accounts, its owner first.

    Args:
        session: Open database session.
        user: The caller, any member.

    Returns:
        The members.
    """
    bind_tenant(session, _tenant(user))
    owner = _owner_id(session)
    rows = session.scalars(
        select(User).where(User.is_active.is_(True)).order_by(User.created_at, User.id)
    ).all()
    return [
        Member(id=str(row.id), email=row.email, full_name=row.full_name, owner=row.id == owner)
        for row in rows
    ]


def team(*, session: Session, user: CurrentUser) -> Team:
    """The caller's team and its seats.

    Args:
        session: Open database session.
        user: The caller, any member.

    Returns:
        The members, whether the caller owns the team, and its seats.
    """
    listed = members(session=session, user=user)
    tenant = _tenant(user)
    return Team(
        members=listed,
        owner=is_owner(session=session, user=user),
        seats=billing.seat_limit(session=session, tenant_id=tenant),
        seats_taken=seats_taken(session=session),
    )


def invite(
    *, session: Session, user: CurrentUser, email: str, now: datetime | None = None
) -> InvitationCreated:
    """Invite a colleague into the caller's tenant.

    Args:
        session: Open database session. The caller commits.
        user: The caller, the tenant's owner.
        email: The colleague's address.
        now: Injected for tests.

    Returns:
        The invitation, with its token, shown this once.

    Raises:
        AuthorizationError: If the caller is not the owner.
        ValidationError: If the address already has an account, or the plan
            holds no more seats.
    """
    tenant = _require_owner(session, user)
    moment = now or datetime.now(UTC)
    address = email.strip().lower()
    if session.scalar(select(User.id).where(User.email == address)) is not None:
        raise ValidationError("that email is already a member", code="team_member_exists")
    # Asking again replaces the open invitation: a new token, a new expiry.
    previous = session.scalars(
        select(InvitationRow).where(InvitationRow.email == address, _open(moment))
    ).all()
    for row in previous:
        session.delete(row)
    session.flush()
    limit = billing.seat_limit(session=session, tenant_id=tenant)
    if limit is not None and seats_taken(session=session, now=moment) >= limit:
        raise ValidationError(
            f"the plan holds {limit} seats, all taken",
            code="team_seats_full",
            params={"seats": limit},
        )
    token = secrets.token_urlsafe(32)
    row = InvitationRow(
        tenant_id=tenant,
        email=address,
        token_hash=hash_claim_secret(token),
        invited_by=user.email,
        expires_at=moment + timedelta(days=INVITATION_DAYS),
    )
    session.add(row)
    session.flush()
    logger.info("team.invited", tenant_id=str(tenant), invitation_id=str(row.id))
    return InvitationCreated(
        id=str(row.id), email=address, token=token, expires_at=row.expires_at.isoformat()
    )


def invitations(*, session: Session, user: CurrentUser) -> list[InvitationOut]:
    """The invitations still open.

    Args:
        session: Open database session.
        user: The caller, the tenant's owner.

    Returns:
        The open invitations, newest first.

    Raises:
        AuthorizationError: If the caller is not the owner.
    """
    _require_owner(session, user)
    rows = session.scalars(
        select(InvitationRow)
        .where(_open(datetime.now(UTC)))
        .order_by(InvitationRow.created_at.desc())
    ).all()
    return [
        InvitationOut(
            id=str(row.id),
            email=row.email,
            invited_by=row.invited_by,
            expires_at=row.expires_at.isoformat(),
        )
        for row in rows
    ]


def revoke(*, session: Session, user: CurrentUser, invitation_id: str) -> None:
    """Withdraw an open invitation.

    Args:
        session: Open database session. The caller commits.
        user: The caller, the tenant's owner.
        invitation_id: The invitation.

    Raises:
        AuthorizationError: If the caller is not the owner.
        NotFoundError: If no such invitation is the tenant's.
    """
    _require_owner(session, user)
    try:
        key = uuid.UUID(invitation_id)
    except ValueError as exc:
        raise NotFoundError("no such invitation", code="team_invitation_not_found") from exc
    row = session.get(InvitationRow, key)
    if row is None or row.accepted_at is not None:
        raise NotFoundError("no such invitation", code="team_invitation_not_found")
    session.delete(row)
    session.flush()


def remove(*, session: Session, user: CurrentUser, member_id: str) -> None:
    """Take a member out of the team: their account is deactivated.

    Args:
        session: Open database session. The caller commits.
        user: The caller, the tenant's owner.
        member_id: The member.

    Raises:
        AuthorizationError: If the caller is not the owner.
        ValidationError: If the owner tries to remove themselves.
        NotFoundError: If no such member is the tenant's.
    """
    _require_owner(session, user)
    if member_id == user.id:
        raise ValidationError("the owner cannot remove themselves", code="team_remove_self")
    try:
        key = uuid.UUID(member_id)
    except ValueError as exc:
        raise NotFoundError("no such member", code="team_member_not_found") from exc
    row = session.get(User, key)
    if row is None or not row.is_active:
        raise NotFoundError("no such member", code="team_member_not_found")
    row.is_active = False
    session.flush()
    logger.info("team.removed", user_id=member_id)


def accept(*, session: Session, token: str, email: str, now: datetime | None = None) -> uuid.UUID:
    """Use an invitation for a new account: the tenant it joins.

    Called by signup, inside its own lift of the tenant filter: the invitee
    has no tenant until this says which.

    Args:
        session: A session that sees every tenant. The caller commits.
        token: The invitation's token, as sent.
        email: The new account's address, normalised; it must be the one
            invited.
        now: Injected for tests.

    Returns:
        The tenant the account joins; the invitation is marked accepted.

    Raises:
        ValidationError: If the token is unknown, used, expired or for
            another address, or the plan's seats filled since.
    """
    moment = now or datetime.now(UTC)
    row = session.scalars(
        select(InvitationRow).where(InvitationRow.token_hash == hash_claim_secret(token))
    ).one_or_none()
    if row is None or row.accepted_at is not None or row.expires_at <= moment:
        raise ValidationError("this invitation is not valid", code="team_invitation_invalid")
    if row.email != email:
        raise ValidationError("this invitation is for another email", code="team_invitation_email")
    limit = billing.seat_limit(session=session, tenant_id=row.tenant_id)
    if limit is not None:
        # The invitation being used holds one of the seats counted.
        bind_tenant(session, row.tenant_id)
        if seats_taken(session=session, now=moment) > limit:
            raise ValidationError(
                f"the plan holds {limit} seats, all taken",
                code="team_seats_full",
                params={"seats": limit},
            )
    row.accepted_at = moment
    session.flush()
    return row.tenant_id
