"""Who may approve a revision, delete a project or change company settings.

The policy, in one place:

=========================  =====  =====  ========  ============  =====
Permission                 Owner  Admin  Reviewer  Other member  Trial
=========================  =====  =====  ========  ============  =====
``approve_revision``       yes    yes    yes       no            no
``delete_project``         yes    yes    no        no            no
``edit_company_settings``  yes    yes    no        no            no
manage the team            yes    yes    no        no            no
transfer ownership         yes    no     no        no            no
=========================  =====  =====  ========  ============  =====

"Owner" is the tenant's recorded owner, ``tenants.owner_user_id``
(``team.standing``), never the oldest account. "Admin" and "Reviewer" are
stored roles (``roles.roles_of``). The ``reviewer`` role is deliberately the
same one that clears operator review items (ADR 0001): an account granted it
for either duty holds both. Managing the team is inviting, withdrawing an
invitation and removing a member (``app.domain.team``); nobody, admin or
owner, removes the owner.

Ownership changes in two ways only: the owner transfers it to another active
member of the tenant (``team.transfer_ownership``, ``POST /team/owner``), or
an operator records one with the worker's ``assign-owner`` where there is no
active owner. Deactivating the owner's account is refused through the API
until ownership is transferred; the worker has no deactivation command.

Every check reads the database, not the token, and ``require`` reads it
under row locks inside the acting transaction: the tenant row ``FOR SHARE``,
then the caller's account ``FOR SHARE``, then its roles. A revocation, grant,
removal or transfer locks the same rows ``FOR UPDATE``, so either it commits
first and the action is refused, or the action commits first.

Compatibility (migration ``a7d3e9f1c2b4``): a tenant's oldest account becomes
its owner when that is unambiguous (active, and no other account created at
the same instant); otherwise the owner is left NULL, its admins still manage
it, and the worker's ``owner-report`` lists it, flagging one with no active
admin for an operator's ``assign-owner``. Other legacy members gain nothing:
an operator grants ``reviewer`` (approval only) or ``admin`` (everything but
transferring) with ``grant-role``.

Domain functions call ``require`` after loading what they act on, so a
project of another tenant's is still "not found" rather than "forbidden".

Framework-agnostic: nothing here imports FastAPI.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError
from app.domain import team
from app.models.schemas.auth import CurrentUser, Permission, Role

#: Granted by a stored role, beyond what the owner holds.
_BY_ROLE: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.REVIEWER: frozenset({Permission.APPROVE_REVISION}),
}

#: The refusal each permission is reported with.
_REFUSALS: dict[Permission, tuple[str, str]] = {
    Permission.APPROVE_REVISION: (
        "approving a revision is for the account's owner or a reviewer",
        "approval_not_permitted",
    ),
    Permission.DELETE_PROJECT: (
        "only the account's owner deletes a project",
        "project_delete_not_permitted",
    ),
    Permission.EDIT_COMPANY_SETTINGS: (
        "only the account's owner changes the company settings",
        "settings_not_permitted",
    ),
}


def _has_account(user: CurrentUser) -> bool:
    """A trial resolves to no account (no email, no account id): it holds nothing."""
    if not user.email:
        return False
    try:
        uuid.UUID(user.id)
    except ValueError:
        return False
    return True


def _granted(found: team.Standing | None) -> list[Permission]:
    if found is None:
        return []
    if found.owner:
        return sorted(Permission)
    held: set[Permission] = set()
    for role, granted in _BY_ROLE.items():
        if role in found.roles:
            held |= granted
    return sorted(held)


def allowed(*, session: Session, user: CurrentUser) -> list[Permission]:
    """Every permission the caller holds, in a stable order, read unlocked.

    For what a response shows; an action is checked with ``require``.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller; its ownership and roles are read from the database.

    Returns:
        The permissions, sorted by name.
    """
    if not _has_account(user):
        return []
    return _granted(team.standing(session=session, user=user))


def can(*, session: Session, user: CurrentUser, permission: Permission) -> bool:
    """Whether the caller holds one permission (unlocked, for display).

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.
        permission: What it wants to do.

    Returns:
        ``True`` if allowed.
    """
    return permission in allowed(session=session, user=user)


def require(*, session: Session, user: CurrentUser, permission: Permission) -> None:
    """Refuse the caller unless it holds a permission, locked until commit.

    Reads the caller's standing under ``FOR SHARE`` locks on the tenant row
    and its account (``team.standing``), held until the caller's transaction
    ends: call it inside the transaction that acts, and commit after acting.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.
        permission: What it wants to do.

    Raises:
        AuthorizationError: If it does not, with the permission's own code
            (``approval_not_permitted``, ``project_delete_not_permitted`` or
            ``settings_not_permitted``).
    """
    found = team.standing(session=session, user=user, lock="share") if _has_account(user) else None
    if permission not in _granted(found):
        message, code = _REFUSALS[permission]
        raise AuthorizationError(message, code=code)
