"""What an account may do, read from the database on every request.

Roles used to live only in the access token, and every token was minted with
``ENGINEER`` — nothing ever read the ``roles`` table. So the reviewer and
ingestion capabilities the four-eyes rule rests on (ADR 0001) were held by
nobody, and had anyone been given them in a token, revoking one would have
taken effect only when that token expired.

Now the database is the only source. ``roles_of`` is what authorization reads,
per request, so a grant or a revocation applies to the next request the
account makes. Roles still appear in the token, as a hint a client may use to
decide what to show; the server never trusts them.

``ENGINEER`` is not stored. Every active account is an engineer — it is the
role the product exists for — so storing it would only create a way to hold an
account that cannot ask a question.

Grants are made by an operator through the worker (``grant-role`` /
``revoke-role``), not through the API: there is no admin surface yet, and a
role that can grant roles is not something to expose before one exists.

Framework-agnostic — nothing here imports FastAPI.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.user import Role as RoleRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

#: Held by every active account without being stored.
IMPLICIT_ROLES: frozenset[Role] = frozenset({Role.ENGINEER})


def roles_of(user: User) -> frozenset[Role]:
    """Return what an account may do: its stored grants plus the implicit ones.

    Args:
        user: The account, loaded in the current session.

    Returns:
        Every role the account holds.

    A stored role name the code no longer knows is skipped and logged rather
    than failing the request. Refusing the account over a stale grant would
    lock someone out of everything for a capability that no longer exists.
    """
    held = set(IMPLICIT_ROLES)
    for row in user.roles:
        try:
            held.add(Role(row.name))
        except ValueError:
            logger.warning("roles.unknown_role_skipped", user_id=str(user.id), role=row.name)
    return frozenset(held)


def grant_role(*, session: Session, email: str, role: Role) -> bool:
    """Give an account a role.

    Args:
        session: Open database session, able to see the account's tenant. The
            caller commits.
        email: The account, by email.
        role: The role to grant.

    Returns:
        ``True`` if granted, ``False`` if the account already held it.

    Raises:
        ValidationError: If the role is one every account holds implicitly.
        NotFoundError: If no account has that email.
    """
    if role in IMPLICIT_ROLES:
        raise ValidationError(f"{role.value} is held by every account; it is not granted")
    user = _account(session=session, email=email)
    if any(row.name == role.value for row in user.roles):
        return False

    row = session.execute(select(RoleRow).where(RoleRow.name == role.value)).scalar_one_or_none()
    if row is None:
        # Created on first use from the enum, which is the contract; the table
        # is storage. Seeding it in a migration would have to be kept in step
        # with the enum by hand.
        row = RoleRow(name=role.value)
        session.add(row)
    user.roles.append(row)
    session.flush()
    # Ids only: this is an audit line, and an email is personal data.
    logger.info("roles.granted", user_id=str(user.id), role=role.value)
    return True


def revoke_role(*, session: Session, email: str, role: Role) -> bool:
    """Take a role away from an account. Applies to its next request.

    Args:
        session: Open database session, able to see the account's tenant. The
            caller commits.
        email: The account, by email.
        role: The role to revoke.

    Returns:
        ``True`` if revoked, ``False`` if the account did not hold it.

    Raises:
        ValidationError: If the role is one every account holds implicitly.
        NotFoundError: If no account has that email.
    """
    if role in IMPLICIT_ROLES:
        raise ValidationError(f"{role.value} is held by every account; it cannot be revoked")
    user = _account(session=session, email=email)
    kept = [row for row in user.roles if row.name != role.value]
    if len(kept) == len(user.roles):
        return False
    user.roles = kept
    session.flush()
    logger.info("roles.revoked", user_id=str(user.id), role=role.value)
    return True


def reviewer_by_email(*, session: Session, email: str) -> CurrentUser:
    """Load an account that holds the reviewer role, as the principal it acts as.

    For an operator command that clears review items in a named person's
    name: the roles come from the stored grants, never from the command line.

    Args:
        session: A session able to see every tenant's accounts.
        email: The reviewer's address.

    Returns:
        The reviewer.

    Raises:
        NotFoundError: If there is no such account.
        AuthorizationError: If it does not hold the reviewer role, or is
            deactivated.
    """
    user = _account(session=session, email=email)
    roles = roles_of(user)
    if not user.is_active or Role.REVIEWER not in roles:
        raise AuthorizationError(f"{user.email} is not an active reviewer")
    return CurrentUser(
        id=str(user.id), email=user.email, tenant_id=str(user.tenant_id), roles=roles
    )


def _account(*, session: Session, email: str) -> User:
    """Load an account by email.

    Args:
        session: Open database session.
        email: The address, compared case-insensitively as signup stores it.

    Returns:
        The account.

    Raises:
        NotFoundError: If there is none.
    """
    user = session.execute(
        select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("no account with that email")
    return user


def holders_of(*, session: Session, role: Role) -> list[uuid.UUID]:
    """Return every active account holding a role, oldest first.

    Args:
        session: A session able to see every tenant's accounts: reviewers
            work a queue that spans customers.
        role: A stored role; the implicit one is held by everyone and is not
            a meaningful question here.

    Returns:
        Their ids, in a stable order so repeated runs distribute the same way.

    Raises:
        ValidationError: If asked about the implicit role.
    """
    if role in IMPLICIT_ROLES:
        raise ValidationError(f"{role.value} is held by every account")
    return list(
        session.scalars(
            select(User.id)
            .join(User.roles)
            .where(RoleRow.name == role.value, User.is_active.is_(True))
            .order_by(User.created_at, User.id)
        )
    )
