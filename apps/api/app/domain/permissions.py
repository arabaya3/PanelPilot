"""Who may approve a revision, delete a project or change company settings.

The policy, in one place:

=========================  =====  =====  ========  ============  =====
Permission                 Owner  Admin  Reviewer  Other member  Trial
=========================  =====  =====  ========  ============  =====
``approve_revision``       yes    yes    yes       no            no
``delete_project``         yes    yes    no        no            no
``edit_company_settings``  yes    yes    no        no            no
=========================  =====  =====  ========  ============  =====

"Owner" is ``team.is_owner``: the tenant's first active account, or any
account holding ``admin``. "Reviewer" is the stored ``reviewer`` role
(``roles.roles_of``). Every member still reads projects and settings, and
saves and exports revisions; nothing here is granted to every member.

Compatibility: ownership is derived, not stored, so no migration is needed.
A tenant created before these checks keeps its first account as owner, with
every permission. Its other members lose approve, delete and settings until
an operator grants them ``reviewer`` with the worker's ``grant-role`` command
(which allows approval only) or ``admin`` (which allows all three).

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


def allowed(*, session: Session, user: CurrentUser) -> list[Permission]:
    """Every permission the caller holds, in a stable order.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller, its roles as read from the database this request.

    Returns:
        The permissions, sorted by name.
    """
    if not _has_account(user):
        return []
    if team.is_owner(session=session, user=user):
        return sorted(Permission)
    held: set[Permission] = set()
    for role, granted in _BY_ROLE.items():
        if user.has_role(role):
            held |= granted
    return sorted(held)


def can(*, session: Session, user: CurrentUser, permission: Permission) -> bool:
    """Whether the caller holds one permission.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.
        permission: What it wants to do.

    Returns:
        ``True`` if allowed.
    """
    return permission in allowed(session=session, user=user)


def require(*, session: Session, user: CurrentUser, permission: Permission) -> None:
    """Refuse the caller unless it holds a permission.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.
        permission: What it wants to do.

    Raises:
        AuthorizationError: If it does not, with the permission's own code
            (``approval_not_permitted``, ``project_delete_not_permitted`` or
            ``settings_not_permitted``).
    """
    if not can(session=session, user=user, permission=permission):
        message, code = _REFUSALS[permission]
        raise AuthorizationError(message, code=code)
