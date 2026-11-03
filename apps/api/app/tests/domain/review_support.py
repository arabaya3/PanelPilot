"""Synthetic tenants and accounts for the engineering-review tests (M3).

Every tenant is new (slug ``m3rev-<hex>``) and every account synthetic
(``m3-<hex>@synthetic.example``, named "Synthetic ..."), so the append-only
rows these tests leave never meet another test's tenant. No identity here is
a real person, and no qualification statement is a real one.
"""

from __future__ import annotations

import uuid
from typing import Any

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


#: Who saves every revision in a test module that approves as its own caller:
#: another member of the tenant, so the approval is not by the revision's
#: author (M6a: ``approval_by_author``). The rule itself is not touched.
OTHER_AUTHOR = "m6a-author@synthetic.example"


def author_revisions_as_another_member(monkeypatch: Any, email: str = OTHER_AUTHOR) -> None:
    """Record ``email`` as the author of every revision saved while patched.

    For the tests written before self-approval was refused: their caller saves
    and approves, as a one-person tenant did. Each revision is recorded as saved
    by another member instead, so the approval stays the caller's and is valid.
    """
    from app.domain import design_projects

    original = design_projects._add_revision

    def add(session: Any, project: Any, tenant: Any, user: CurrentUser, *args: Any, **kw: Any):  # type: ignore[no-untyped-def]
        return original(
            session, project, tenant, user.model_copy(update={"email": email}), *args, **kw
        )

    monkeypatch.setattr(design_projects, "_add_revision", add)
