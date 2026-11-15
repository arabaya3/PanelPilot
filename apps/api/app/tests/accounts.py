"""A caller backed by a real account row, for tests against the disposable Postgres.

``permissions.require`` re-reads the caller's account and roles under row
locks rather than trusting the token, so a caller made up in a test (a random
id with ``admin`` in its token) now holds nothing. ``account`` creates the
row and stores the roles it is given, so such a caller is what it claims.
"""

from __future__ import annotations

import os
import uuid
from functools import cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.tenancy import bind_tenant
from app.domain import roles as roles_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.user import User


@cache
def _engine() -> Engine:
    return create_engine(os.environ["TEST_DATABASE_URL"])


def account(
    tenant: uuid.UUID, roles: frozenset[Role] = frozenset(), email: str = "eng@example.com"
) -> CurrentUser:
    """A new active account in ``tenant`` holding ``roles``.

    Args:
        tenant: Its tenant.
        roles: Stored for it; the implicit ones are added.
        email: What the caller's token says. The row's own address is unique
            and made up, so many callers may share a display email.

    Returns:
        The caller, its roles as the database now holds them.
    """
    key = uuid.uuid4()
    stored = f"{key.hex}@accounts.test"
    with Session(_engine()) as session:
        bind_tenant(session, tenant)
        row = User(id=key, tenant_id=tenant, email=stored, is_active=True)
        session.add(row)
        session.flush()
        for role in roles - roles_domain.IMPLICIT_ROLES:
            roles_domain.grant_role(session=session, email=stored, role=role)
        held = roles_domain.roles_of(row)
        session.commit()
    return CurrentUser(id=str(key), email=email, tenant_id=str(tenant), roles=held)
