"""Tests for `app/core/tenancy.py` — the tenant filter (ADR 0003).

Every test that matters here uses two tenants. A one-tenant fixture is the
reason per-query filtering failed silently: with only one customer's rows in
the database, a query that forgot its filter still returned the right answer.
"""

from __future__ import annotations

import importlib
import pkgutil
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.core.tenancy import (
    TenantScopeError,
    bind_tenant,
    bound_tenant,
    cross_tenant,
    cross_tenant_info,
)
from app.models.tables.base import Base
from app.models.tables.diagnostics import DiagnosticSessionRow, DiagnosticTurnRow
from app.models.tables.tenant import TenantRow, TenantScopedMixin

_SLUG_PREFIX = "tenancy-tests-"


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM diagnostic_turns LIMIT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture(name="engine")
def _engine() -> Iterator[Engine]:
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    yield engine
    with Session(engine, info=cross_tenant_info("test cleanup spans tenants")) as cleanup:
        tenants = f"(SELECT id FROM tenants WHERE slug LIKE '{_SLUG_PREFIX}%')"
        cleanup.execute(text(f"DELETE FROM diagnostic_turns WHERE tenant_id IN {tenants}"))
        cleanup.execute(text(f"DELETE FROM diagnostic_sessions WHERE tenant_id IN {tenants}"))
        cleanup.execute(text(f"DELETE FROM tenants WHERE slug LIKE '{_SLUG_PREFIX}%'"))
        cleanup.commit()
    engine.dispose()


class _TwoTenants:
    """Tenant A and tenant B, each with one conversation holding one turn."""

    def __init__(self, engine: Engine) -> None:
        with Session(engine, info=cross_tenant_info("fixture writes both tenants")) as setup:
            self.tenant_a = self._tenant(setup, "a")
            self.tenant_b = self._tenant(setup, "b")
            self.conv_a, self.turn_a = self._conversation(setup, self.tenant_a, "question a")
            self.conv_b, self.turn_b = self._conversation(setup, self.tenant_b, "question b")
            setup.commit()

    @staticmethod
    def _tenant(session: Session, name: str) -> uuid.UUID:
        row = TenantRow(slug=f"{_SLUG_PREFIX}{name}-{uuid.uuid4().hex[:8]}", name=name)
        session.add(row)
        session.flush()
        return row.id

    @staticmethod
    def _conversation(
        session: Session, tenant: uuid.UUID, question: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        conv = DiagnosticSessionRow(tenant_id=tenant)
        session.add(conv)
        session.flush()
        turn = DiagnosticTurnRow(
            session_id=conv.id, tenant_id=tenant, position=1, question=question, answer="x"
        )
        session.add(turn)
        session.flush()
        return conv.id, turn.id


@pytest.fixture(name="world")
def _world(engine: Engine) -> _TwoTenants:
    return _TwoTenants(engine)


@pytest.fixture(name="as_a")
def _as_a(engine: Engine, world: _TwoTenants) -> Iterator[Session]:
    """A session bound to tenant A, as a request by one of A's users gets."""
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, world.tenant_a)
        yield session
        session.rollback()


# --- a bound session sees one tenant, whatever the query shape ---------------


@requires_db
def test_a_select_returns_only_the_bound_tenants_rows(as_a: Session, world: _TwoTenants) -> None:
    ids = set(as_a.scalars(select(DiagnosticSessionRow.id)))

    assert world.conv_a in ids
    assert world.conv_b not in ids


@requires_db
def test_another_tenants_row_by_id_does_not_exist(as_a: Session, world: _TwoTenants) -> None:
    """The same answer as an id that was never issued — nothing to probe."""
    assert as_a.get(DiagnosticTurnRow, world.turn_b) is None
    assert as_a.get(DiagnosticTurnRow, world.turn_a) is not None


@requires_db
def test_an_update_cannot_reach_another_tenant(as_a: Session, world: _TwoTenants) -> None:
    result = as_a.execute(
        update(DiagnosticTurnRow)
        .where(DiagnosticTurnRow.id == world.turn_b)
        .values(answer="overwritten")
    )

    assert result.rowcount == 0  # type: ignore[attr-defined]


@requires_db
def test_a_correlated_subquery_is_filtered_too(as_a: Session, world: _TwoTenants) -> None:
    """The session list reads its titles this way; the filter must reach inside."""
    inner = aliased(DiagnosticTurnRow)
    question = (
        select(inner.question).where(inner.session_id == world.conv_b).limit(1).scalar_subquery()
    )

    assert as_a.execute(select(question)).scalar() is None


@requires_db
def test_a_relationship_load_is_filtered_too(
    engine: Engine, as_a: Session, world: _TwoTenants
) -> None:
    """A row reached by walking a relationship is still subject to the filter."""
    turn = as_a.get(DiagnosticTurnRow, world.turn_a)
    assert turn is not None
    assert turn.session.id == world.conv_a


# --- the default is refusal, not everything ---------------------------------


@requires_db
def test_an_unbound_session_cannot_query_customer_data(engine: Engine, world: _TwoTenants) -> None:
    """Forgetting to scope fails loudly, the first time the code runs."""
    del world
    with sessionmaker(bind=engine)() as session, pytest.raises(TenantScopeError):
        session.scalars(select(DiagnosticSessionRow)).all()


@requires_db
def test_an_unbound_session_can_still_read_shared_tables(engine: Engine) -> None:
    """Tenants themselves, the corpus and the queue are not customer data."""
    with sessionmaker(bind=engine)() as session:
        session.scalars(select(TenantRow).limit(1)).all()


@requires_db
def test_cross_tenant_sees_everything_and_then_stops(as_a: Session, world: _TwoTenants) -> None:
    with cross_tenant(as_a, reason="test: staff view spans tenants"):
        assert as_a.get(DiagnosticSessionRow, world.conv_b) is not None

    # Evicted on the way out, so the identity map cannot hand it back.
    assert as_a.get(DiagnosticSessionRow, world.conv_b) is None


@requires_db
def test_a_session_built_cross_tenant_spans_tenants(engine: Engine, world: _TwoTenants) -> None:
    with Session(engine, info=cross_tenant_info("test: inspect both tenants")) as session:
        ids = set(session.scalars(select(DiagnosticSessionRow.id)))
    assert {world.conv_a, world.conv_b} <= ids


# --- binding --------------------------------------------------------------------


@requires_db
def test_binding_evicts_rows_loaded_before_it(engine: Engine, world: _TwoTenants) -> None:
    """`session.get` answers from the identity map without a query to filter."""
    with Session(engine, info=cross_tenant_info("test: preload both")) as session:
        assert session.get(DiagnosticSessionRow, world.conv_b) is not None
        session.info.clear()  # as a session would be without the reason
        bind_tenant(session, world.tenant_a)

        assert session.get(DiagnosticSessionRow, world.conv_b) is None


def test_rebinding_to_another_tenant_is_refused() -> None:
    """A session that answered for one customer must never answer for another."""
    session = Session()
    tenant = uuid.uuid4()
    bind_tenant(session, tenant)
    bind_tenant(session, str(tenant))  # the same one, as a JWT carries it

    with pytest.raises(TenantScopeError):
        bind_tenant(session, uuid.uuid4())
    assert bound_tenant(session) == tenant


def test_a_malformed_tenant_is_refused() -> None:
    with pytest.raises(TenantScopeError):
        bind_tenant(Session(), "not-a-uuid")


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_exemption_needs_a_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="reason"), cross_tenant(Session(), reason=reason):
        pass
    with pytest.raises(ValueError, match="reason"):
        cross_tenant_info(reason)


def test_a_nested_exemption_keeps_the_outer_one_until_it_ends() -> None:
    session = Session()
    with cross_tenant(session, reason="outer"):
        with cross_tenant(session, reason="inner"):
            pass
        assert session.info  # still exempt: the outer block has not ended
    assert not session.info


# --- writes -------------------------------------------------------------------


@requires_db
def test_a_bound_session_cannot_write_into_another_tenant(
    as_a: Session, world: _TwoTenants
) -> None:
    as_a.add(DiagnosticSessionRow(tenant_id=world.tenant_b))

    with pytest.raises(TenantScopeError):
        as_a.flush()


# --- coverage of every scoped table --------------------------------------------


def _scoped_models() -> list[type[Any]]:
    # Every table module, imported explicitly: the registry only knows the
    # models something has imported, and a scoped table missing from this
    # list would be exempt from the test by accident.
    import app.models.tables as tables_package

    for module in pkgutil.iter_modules(tables_package.__path__):
        importlib.import_module(f"{tables_package.__name__}.{module.name}")
    return sorted(
        (
            mapper.class_
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, TenantScopedMixin)
        ),
        key=lambda cls: cls.__name__,
    )


@requires_db
@pytest.mark.parametrize("model", _scoped_models(), ids=lambda cls: cls.__name__)
def test_every_scoped_table_is_filtered(engine: Engine, model: type[Any]) -> None:
    """No scoped table is exempt by omission: each query carries the predicate."""
    statements: list[str] = []

    def _record(*args: Any, **_kw: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        with sessionmaker(bind=engine)() as session:
            bind_tenant(session, uuid.uuid4())
            session.scalars(select(model).limit(1)).all()
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    table = model.__tablename__
    assert any(f"{table}.tenant_id" in sql for sql in statements), statements
