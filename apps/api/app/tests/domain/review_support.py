"""Synthetic tenants and accounts for the engineering-review tests (M3).

Every tenant is new (slug ``m3rev-<hex>``) and every account synthetic
(``m3-<hex>@synthetic.example``, named "Synthetic ..."), so the append-only
rows these tests leave never meet another test's tenant. No identity here is
a real person, and no qualification statement is a real one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.tenancy import bind_tenant
from app.domain import roles
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.tenant import TenantRow

#: A qualification statement that is plainly not a real one.
SYNTHETIC_QUALIFICATION = "SYNTHETIC TEST STATEMENT - not a real qualification"


def new_tenant(engine: Engine) -> str:
    """A fresh tenant with no accounts."""
    with sessionmaker(bind=engine)() as session:
        row = TenantRow(slug=f"m3rev-{uuid.uuid4().hex[:16]}", name="Synthetic M3 tenant")
        session.add(row)
        session.commit()
        return str(row.id)


def new_account(
    engine: Engine, tenant: str, *, owner: bool = False, role: Role | None = None
) -> CurrentUser:
    """A synthetic active account; optionally the tenant's owner, or holding a role."""
    account = uuid.uuid4()
    email = f"m3-{account.hex[:16]}@synthetic.example"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, full_name, is_active, created_at, "
                "updated_at) VALUES (:i, :t, :e, :n, true, now(), now())"
            ),
            {"i": account, "t": uuid.UUID(tenant), "e": email, "n": "Synthetic account"},
        )
        if owner:
            conn.execute(
                text("UPDATE tenants SET owner_user_id = :u WHERE id = :t"),
                {"u": account, "t": uuid.UUID(tenant)},
            )
    if role is not None:
        with sessionmaker(bind=engine)() as session:
            bind_tenant(session, tenant)
            roles.grant_role(session=session, email=email, role=role)
            session.commit()
    held = frozenset({Role.ENGINEER} | ({role} if role else set()))
    return CurrentUser(id=str(account), email=email, tenant_id=tenant, roles=held)
