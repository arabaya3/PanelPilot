"""Tenant isolation, enforced once for every ORM query (ADR 0003).

``TenantScopedMixin`` gives a table its ``tenant_id``. This module is what makes
that column a boundary rather than a convention: every ORM statement a
session issues against a tenant-scoped table is filtered to the tenant the
session is bound to — ``SELECT``, ``session.get``, joins, correlated
subqueries, relationship loads, and ORM ``UPDATE``/``DELETE`` alike.

Three states, and the default is the safe one:

- **Bound** (``bind_tenant``): scoped rows of other tenants do not exist, as
  far as this session can tell. A row of another tenant by id is simply not
  found, which is the same answer a caller gets for an id that never existed.
- **Cross-tenant** (``cross_tenant``): the filter is lifted for a named
  reason — authentication finding an account by email before anyone is known,
  staff review spanning customers, a system job. Allowed only in the modules
  ``app/tests/test_architecture.py`` lists, so a new one is a reviewed edit.
- **Neither**: a query that touches a scoped table **raises**. Per-query
  discipline failed silently — a forgotten filter returned another customer's
  rows and a one-tenant fixture never noticed. Here, forgetting fails loudly,
  the first time the code runs.

Writes are guarded too: a bound session cannot flush a scoped row belonging to
another tenant.

What this does not cover, and the architecture test forbids instead: raw SQL
(``text()``) and Core statements against a table object, which never pass
through the ORM events this relies on.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction, with_loader_criteria

from app.core.errors import PanelPilotError
from app.models.tables.tenant import TenantScopedMixin

_TENANT_KEY = "panelpilot.tenant_id"
_CROSS_TENANT_KEY = "panelpilot.cross_tenant_reason"


class TenantScopeError(PanelPilotError):
    """A tenant-scoped table was reached without a tenant to scope it to.

    A programming error, never a user's: it means a code path queried customer
    data without either binding the session to the caller's tenant or
    declaring why it may span tenants. Maps to 500 like any other defect.
    """


def bind_tenant(session: Session, tenant_id: uuid.UUID | str) -> None:
    """Scope every tenant-scoped query this session issues to one tenant.

    Idempotent for the same tenant. Rebinding to a different one raises: a
    session that answered for one customer must never start answering for
    another, and a silent rebind is exactly how that would happen.

    Args:
        session: The session to scope.
        tenant_id: The tenant, as a UUID or its string form (as a JWT carries it).

    Raises:
        TenantScopeError: If the tenant id is malformed, or the session is
            already bound to a different tenant.
    """
    try:
        tenant = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_id)
    except ValueError as exc:
        raise TenantScopeError("tenant id is not a UUID") from exc

    current = session.info.get(_TENANT_KEY)
    if current is not None and current != tenant:
        raise TenantScopeError("session is already bound to a different tenant")
    session.info[_TENANT_KEY] = tenant
    _evict_foreign_rows(session, tenant)


def _evict_foreign_rows(session: Session, tenant: uuid.UUID) -> None:
    """Drop other tenants' rows from the session's identity map.

    ``session.get`` answers from the identity map without issuing a query, so
    the filter never sees it: a row loaded before binding, or inside a
    cross-tenant block, would come back by id as though it belonged here.
    Expunged, it has to be loaded again — through the filter.

    Args:
        session: The session, bound to ``tenant``.
        tenant: The tenant it is bound to.
    """
    for row in list(session.identity_map.values()):
        if isinstance(row, TenantScopedMixin) and row.tenant_id != tenant:
            session.expunge(row)


def bound_tenant(session: Session) -> uuid.UUID | None:
    """Return the tenant a session is bound to, if any.

    Args:
        session: The session.

    Returns:
        The bound tenant, or ``None``.
    """
    tenant = session.info.get(_TENANT_KEY)
    return tenant if isinstance(tenant, uuid.UUID) else None


@contextmanager
def cross_tenant(session: Session, *, reason: str) -> Iterator[Session]:
    """Lift tenant filtering for the duration of the block, for a stated reason.

    The reason is not decoration: it is what a reviewer reads to decide
    whether the exemption is legitimate, and it must be non-empty.

    Nested blocks keep the outer reason; the filter comes back when the
    outermost block exits, even if it raised.

    Args:
        session: The session to lift filtering on.
        reason: Why this code may see every tenant's rows.

    Yields:
        The same session.

    Raises:
        ValueError: If no reason is given.
    """
    if not reason.strip():
        raise ValueError("cross_tenant needs a reason")
    previous = session.info.get(_CROSS_TENANT_KEY)
    if previous is None:
        session.info[_CROSS_TENANT_KEY] = reason
    try:
        yield session
    finally:
        if previous is None:
            session.info.pop(_CROSS_TENANT_KEY, None)
            tenant = bound_tenant(session)
            if tenant is not None:
                # What the block loaded must not outlive it in a bound session.
                _evict_foreign_rows(session, tenant)


def cross_tenant_info(reason: str) -> dict[str, str]:
    """Session ``info`` for a session that spans tenants for its whole life.

    For sessions built outside the request path — a maintenance script, a test
    that inspects rows across tenants — where there is no block to wrap:
    ``sessionmaker(bind=engine, info=cross_tenant_info("..."))``. Requests
    never use it; they are bound by ``resolve_caller``.

    Args:
        reason: Why this session may see every tenant's rows.

    Returns:
        The ``info`` mapping to construct the session with.

    Raises:
        ValueError: If no reason is given.
    """
    if not reason.strip():
        raise ValueError("cross_tenant_info needs a reason")
    return {_CROSS_TENANT_KEY: reason}


def _is_scoped(mapper: Any) -> bool:
    return isinstance(mapper.class_, type) and issubclass(mapper.class_, TenantScopedMixin)


def _mappers_of(state: ORMExecuteState) -> list[Any]:
    """Every mapper a statement names at the top level, including its target."""
    mappers = list(state.all_mappers)
    if state.bind_mapper is not None:
        mappers.append(state.bind_mapper)
    return mappers


@event.listens_for(Session, "do_orm_execute")
def _scope_statement(state: ORMExecuteState) -> None:
    """Add the tenant filter to a statement, or refuse an unscoped one."""
    if not (state.is_select or state.is_update or state.is_delete):
        return
    session = state.session
    if session.info.get(_CROSS_TENANT_KEY) is not None:
        return

    tenant = bound_tenant(session)
    if tenant is None:
        if any(_is_scoped(mapper) for mapper in _mappers_of(state)):
            raise TenantScopeError(
                "query on a tenant-scoped table from a session bound to no tenant; "
                "bind_tenant() it to the caller, or wrap it in cross_tenant() "
                "with the reason it may span tenants"
            )
        return

    # Applied to every statement, not only those naming a scoped mapper at the
    # top level: the criteria reach scoped entities in joins, correlated
    # subqueries and relationship loads, which a top-level check would miss.
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda cls: cls.tenant_id == tenant,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, _flush_context: UOWTransaction, _instances: object) -> None:
    """Refuse to write a scoped row into another tenant from a bound session."""
    tenant = bound_tenant(session)
    if tenant is None or session.info.get(_CROSS_TENANT_KEY) is not None:
        return
    for row in (*session.new, *session.dirty):
        if isinstance(row, TenantScopedMixin) and row.tenant_id != tenant:
            raise TenantScopeError(
                f"refusing to write a {type(row).__name__} for another tenant "
                "from a session bound to this one"
            )
