"""A tenant's team: its owner, its members, and inviting colleagues into it.

The tenant's owner is recorded (``tenants.owner_user_id``): set by the signup
that creates the tenant or claims a trial, and changed only by the owner's
``transfer_ownership`` or an operator's ``assign_owner``. The owner, or an
account granted ``admin``, invites, revokes an invitation or removes a
member; neither removes the owner, who must transfer ownership first. An
invitation
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.security import hash_claim_secret
from app.core.tenancy import TenantScopeError, bind_tenant, bound_tenant
from app.domain import billing
from app.domain import roles as roles_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.team import (
    InvitationCreated,
    InvitationOut,
    Member,
    OwnershipIssue,
    Team,
)
from app.models.tables.team import InvitationRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import Role as RoleRow
from app.models.tables.user import User, user_roles

logger = structlog.get_logger(__name__)

#: How long an invitation may wait to be accepted.
INVITATION_DAYS = 14


def _tenant(user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    return tenant


#: How the tenant row is locked while a caller's standing is read: ``share``
#: for an action checked against it (approve, delete, settings, invitations),
#: so a transfer or a removal waits for it or it for them; ``update`` for what
#: changes the team, so two such changes queue.
TenantLock = Literal["share", "update"]


@dataclass(frozen=True)
class Standing:
    """What the caller is in its tenant, read from the database.

    Attributes:
        owner: It is the tenant's recorded owner (``tenants.owner_user_id``).
        roles: Its stored roles, plus the implicit ones.
    """

    owner: bool
    roles: frozenset[Role]

    @property
    def manages(self) -> bool:
        """The owner, or an admin: either manages the team."""
        return self.owner or Role.ADMIN in self.roles


def standing(
    *, session: Session, user: CurrentUser, lock: TenantLock | None = None
) -> Standing | None:
    """Read the caller's ownership and roles now, optionally under row locks.

    With ``lock``, the tenant row is locked first, then the caller's account
    ``FOR SHARE``. A role revocation or grant, a removal, and a transfer each
    lock one of those rows ``FOR UPDATE``: either it committed before this
    read, and is seen, or it waits until the caller's transaction ends. The
    roles are read after the locks, so one revoked while this waited is not
    counted. The token's roles are never trusted here.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.
        lock: How to lock the tenant row; ``None`` reads without locking,
            for what is only shown.

    Returns:
        The standing, or ``None`` if the caller has no active account in the
        tenant (a trial, a deactivated or removed account).
    """
    tenant = _tenant(user)
    try:
        account = uuid.UUID(user.id)
    except ValueError:
        return None
    bind_tenant(session, tenant)
    owner_query = select(TenantRow.owner_user_id).where(TenantRow.id == tenant)
    active_query = select(User.is_active).where(User.id == account, User.tenant_id == tenant)
    if lock is not None:
        owner_query = owner_query.with_for_update(read=lock == "share")
        active_query = active_query.with_for_update(read=True)
    owner = session.execute(owner_query).one_or_none()
    if owner is None or not session.scalar(active_query):
        return None
    held = set(roles_domain.IMPLICIT_ROLES)
    for name in session.scalars(
        select(RoleRow.name)
        .join(user_roles, user_roles.c.role_id == RoleRow.id)
        .where(user_roles.c.user_id == account)
    ):
        try:
            held.add(Role(name))
        except ValueError:
            logger.warning("roles.unknown_role_skipped", user_id=user.id, role=name)
    return Standing(owner=owner[0] == account, roles=frozenset(held))


def is_owner(*, session: Session, user: CurrentUser) -> bool:
    """Whether the caller manages the tenant's team (read unlocked, for display).

    Args:
        session: Open database session.
        user: The caller.

    Returns:
        True for the tenant's recorded owner, or an active account holding
        ``admin``.
    """
    found = standing(session=session, user=user)
    return found is not None and found.manages


def _require_manager(
    session: Session, user: CurrentUser, *, lock: TenantLock = "share"
) -> uuid.UUID:
    tenant = _tenant(user)
    found = standing(session=session, user=user, lock=lock)
    if found is None or not found.manages:
        raise AuthorizationError(
            "only the account's owner manages its team", code="team_owner_only"
        )
    return tenant


def _owner_id(session: Session, tenant: uuid.UUID) -> uuid.UUID | None:
    return session.scalar(select(TenantRow.owner_user_id).where(TenantRow.id == tenant))


def _open(now: datetime) -> ColumnElement[bool]:
    return (InvitationRow.accepted_at.is_(None)) & (InvitationRow.expires_at > now)


def seats_taken(
    *, session: Session, tenant_id: uuid.UUID | None = None, now: datetime | None = None
) -> int:
    """Members plus invitations still open, in one tenant.

    Counted with an explicit tenant filter, not only the session's binding,
    so a caller under a lifted tenant filter (signup accepting an invitation)
    counts its own tenant's seats rather than every tenant's.

    Args:
        session: Open database session.
        tenant_id: The tenant; the session's bound tenant where omitted.
        now: Injected for tests.

    Returns:
        The seats they hold.

    Raises:
        TenantScopeError: If no tenant is given and the session is bound to none.
    """
    tenant = tenant_id or bound_tenant(session)
    if tenant is None:
        raise TenantScopeError("seats are counted in one tenant; none was given or bound")
    moment = now or datetime.now(UTC)
    members = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.tenant_id == tenant, User.is_active.is_(True))
    )
    waiting = session.scalar(
        select(func.count())
        .select_from(InvitationRow)
        .where(InvitationRow.tenant_id == tenant, _open(moment))
    )
    return (members or 0) + (waiting or 0)


def members(*, session: Session, user: CurrentUser) -> list[Member]:
    """The tenant's active accounts, its owner first.

    Args:
        session: Open database session.
        user: The caller, any member.

    Returns:
        The members.
    """
    tenant = _tenant(user)
    bind_tenant(session, tenant)
    owner = _owner_id(session, tenant)
    rows = session.scalars(
        select(User).where(User.is_active.is_(True)).order_by(User.created_at, User.id)
    ).all()
    # The owner first, then by seniority.
    ordered = sorted(rows, key=lambda row: row.id != owner)
    return [
        Member(id=str(row.id), email=row.email, full_name=row.full_name, owner=row.id == owner)
        for row in ordered
    ]


def team(*, session: Session, user: CurrentUser) -> Team:
    """The caller's team and its seats.

    Args:
        session: Open database session.
        user: The caller, any member.

    Returns:
        The members, whether the caller manages the team and whether it is
        the owner, and its seats.
    """
    listed = members(session=session, user=user)
    tenant = _tenant(user)
    found = standing(session=session, user=user)
    return Team(
        members=listed,
        owner=found is not None and found.manages,
        owns=found is not None and found.owner,
        seats=billing.seat_limit(session=session, tenant_id=tenant),
        seats_taken=seats_taken(session=session, tenant_id=tenant),
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
    tenant = _require_manager(session, user)
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
    if limit is not None and seats_taken(session=session, tenant_id=tenant, now=moment) >= limit:
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
    _require_manager(session, user)
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
    _require_manager(session, user)
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

    The tenant row is locked ``FOR UPDATE``, then the member's account, so a
    transfer, another removal, or an action the member is taking (which holds
    their account ``FOR SHARE``) finishes first or waits for this.

    Args:
        session: Open database session. The caller commits.
        user: The caller, the tenant's owner or an admin.
        member_id: The member.

    Raises:
        AuthorizationError: If the caller manages nothing (``team_owner_only``).
        ValidationError: If the caller removes themselves
            (``team_remove_self``), or anyone removes the tenant's owner, who
            must transfer ownership first (``team_owner_not_removable``).
        NotFoundError: If no such member is the tenant's.
    """
    tenant = _require_manager(session, user, lock="update")
    row = _locked_member(session, tenant, member_id)
    # The account looked up, not the id as sent: an uppercase or braced id is the caller too.
    if _is_caller(row, user):
        raise ValidationError("the owner cannot remove themselves", code="team_remove_self")
    if row.id == _owner_id(session, tenant):
        logger.warning("team.owner_removal_refused", user_id=member_id, by=user.id)
        raise ValidationError(
            "the account's owner cannot be removed until ownership is transferred",
            code="team_owner_not_removable",
        )
    row.is_active = False
    session.flush()
    logger.info("team.removed", user_id=member_id, by=user.id)


def _is_caller(row: User, user: CurrentUser) -> bool:
    """Whether the account found is the caller's own, compared as UUIDs."""
    try:
        return row.id == uuid.UUID(user.id)
    except ValueError:
        return False


def _locked_member(session: Session, tenant: uuid.UUID, member_id: str) -> User:
    """An active account of the tenant, locked ``FOR UPDATE``."""
    try:
        key = uuid.UUID(member_id)
    except ValueError as exc:
        raise NotFoundError("no such member", code="team_member_not_found") from exc
    row = session.scalars(
        select(User)
        .where(User.id == key, User.tenant_id == tenant)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None or not row.is_active:
        raise NotFoundError("no such member", code="team_member_not_found")
    return row


def transfer_ownership(*, session: Session, user: CurrentUser, member_id: str) -> None:
    """Hand the tenant's ownership to another active member of it.

    Only the recorded owner transfers: an admin can neither transfer nor
    take ownership. The tenant row is locked ``FOR UPDATE`` first, so two
    transfers queue (the second then finds its caller no longer the owner),
    and an approval, delete or settings save checked against the old owner
    either commits first or waits and is checked against the new one. The
    previous owner stays a member, with only the roles it holds.

    Args:
        session: Open database session. The caller commits.
        user: The caller, the tenant's owner.
        member_id: The new owner: an active account of the same tenant.

    Raises:
        AuthorizationError: If the caller is not the owner
            (``team_transfer_owner_only``).
        ValidationError: If the caller names itself (``team_transfer_self``).
        NotFoundError: If no such active member is the tenant's
            (``team_member_not_found``).
    """
    tenant = _tenant(user)
    found = standing(session=session, user=user, lock="update")
    if found is None or not found.owner:
        logger.warning("team.ownership_transfer_refused", tenant_id=str(tenant), by=user.id)
        raise AuthorizationError(
            "only the account's owner transfers its ownership", code="team_transfer_owner_only"
        )
    row = _locked_member(session, tenant, member_id)
    if _is_caller(row, user):
        raise ValidationError("you already own the account", code="team_transfer_self")
    session.execute(update(TenantRow).where(TenantRow.id == tenant).values(owner_user_id=row.id))
    session.flush()
    logger.info(
        "team.ownership_transferred",
        tenant_id=str(tenant),
        from_user_id=user.id,
        to_user_id=str(row.id),
    )


def assign_owner(*, session: Session, email: str) -> uuid.UUID:
    """Record an account as its tenant's owner: an operator's remedy.

    For a tenant the migration left without an owner, or whose owner was
    deactivated outside the API (``ownership_report``). It never replaces an
    active owner: that is the owner's own transfer.

    Args:
        session: A session able to see every tenant (an operator command lifts the filter). The
            caller commits.
        email: The account to make owner; it must be active.

    Returns:
        The tenant.

    Raises:
        NotFoundError: If no active account has that email.
        ValidationError: If the tenant has another, active owner
            (``team_owner_present``), or is deactivated (``tenant_inactive``):
            nobody there can sign in to use it.
    """
    account = session.scalars(select(User).where(User.email == email.strip().lower())).one_or_none()
    if account is None:
        raise NotFoundError("no active account with that email")
    # The tenant first, then accounts: the order every path here takes.
    tenant = session.scalars(
        select(TenantRow)
        .where(TenantRow.id == account.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if not tenant.is_active:
        raise ValidationError(
            "the tenant is deactivated; reactivate it before recording an owner",
            code="tenant_inactive",
        )
    current = tenant.owner_user_id
    if current is not None and current != account.id:
        owner_active = session.scalar(
            select(User.is_active).where(User.id == current).with_for_update()
        )
        if owner_active:
            raise ValidationError(
                "the tenant has an active owner; only they transfer it",
                code="team_owner_present",
            )
    active = session.scalar(select(User.is_active).where(User.id == account.id).with_for_update())
    if not active:
        raise NotFoundError("no active account with that email")
    tenant.owner_user_id = account.id
    session.flush()
    logger.info(
        "team.owner_assigned_by_operator", tenant_id=str(tenant.id), to_user_id=str(account.id)
    )
    return tenant.id


def ownership_report(*, session: Session) -> list[OwnershipIssue]:
    """Active tenants with accounts but no active owner, for an operator.

    Args:
        session: A session able to see every tenant (an operator command lifts the filter).

    Returns:
        One entry per such tenant, by id: ``no_owner`` (the migration found
        its first account ambiguous) or ``owner_inactive`` (deactivated
        outside the API), with how many active admins can still manage it.
        One with none needs ``assign_owner``. Ids and counts only.
    """
    active_members = (
        select(func.count())
        .select_from(User)
        .where(User.tenant_id == TenantRow.id, User.is_active.is_(True))
        .scalar_subquery()
    )
    admins = (
        select(func.count())
        .select_from(User)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(RoleRow, RoleRow.id == user_roles.c.role_id)
        .where(
            User.tenant_id == TenantRow.id,
            User.is_active.is_(True),
            RoleRow.name == Role.ADMIN.value,
        )
        .scalar_subquery()
    )
    owner_active = (
        select(User.is_active).where(User.id == TenantRow.owner_user_id).scalar_subquery()
    )
    rows = session.execute(
        select(TenantRow.id, TenantRow.owner_user_id, owner_active, admins)
        .where(TenantRow.is_active.is_(True), active_members > 0)
        .order_by(TenantRow.id)
    ).all()
    return [
        OwnershipIssue(
            tenant_id=str(tenant_id),
            issue="no_owner" if owner_id is None else "owner_inactive",
            active_admins=admin_count or 0,
        )
        for tenant_id, owner_id, active, admin_count in rows
        if owner_id is None or not active
    ]


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
    # The invitation being used holds one of the seats counted. Signup calls
    # this under a lifted tenant filter, where binding filters nothing: the
    # count names its tenant itself.
    if (
        limit is not None
        and seats_taken(session=session, tenant_id=row.tenant_id, now=moment) > limit
    ):
        raise ValidationError(
            f"the plan holds {limit} seats, all taken",
            code="team_seats_full",
            params={"seats": limit},
        )
    row.accepted_at = moment
    session.flush()
    return row.tenant_id
