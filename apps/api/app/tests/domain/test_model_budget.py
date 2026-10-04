"""The monthly model-call ceiling, against a real database: the lock is the point."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import TooManyRequestsError
from app.core.tenancy import cross_tenant_info
from app.domain.model_budget import (
    ModelBudgetExceededError,
    charge_model_call,
    current_period,
)
from app.models.tables import user as _user  # noqa: F401
from app.models.tables.tenant import ModelUsageRow, TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: the ceiling is held by a row lock",
)

SEPTEMBER = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenant")
def _tenant(engine: Engine) -> Iterator[uuid.UUID]:
    """A fresh tenant per test, and its usage removed afterwards."""
    factory = sessionmaker(bind=engine, info=cross_tenant_info("tests set up tenants"))
    with factory() as session:
        row = TenantRow(slug=f"budget-{uuid.uuid4().hex[:8]}", name="Budget test")
        session.add(row)
        session.commit()
        tenant = row.id
    yield tenant
    with factory() as session:
        session.execute(text("DELETE FROM model_usage WHERE tenant_id = :t"), {"t": tenant})
        session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant})
        session.commit()


def test_the_period_is_the_utc_month() -> None:
    assert current_period(SEPTEMBER) == "2026-09"
    assert current_period(OCTOBER) == "2026-10"


def test_the_refusal_is_a_429() -> None:
    assert issubclass(ModelBudgetExceededError, TooManyRequestsError)


@requires_postgres
def test_calls_are_counted_up_to_the_ceiling_and_then_refused(
    engine: Engine, tenant: uuid.UUID
) -> None:
    with Session(engine) as session:
        counts = [
            charge_model_call(session=session, tenant_id=tenant, limit=3, now=SEPTEMBER)
            for _ in range(3)
        ]
        session.commit()
        with pytest.raises(ModelBudgetExceededError, match="3 model calls for 2026-09"):
            charge_model_call(session=session, tenant_id=str(tenant), limit=3, now=SEPTEMBER)
        session.rollback()

    assert counts == [1, 2, 3]
    with Session(engine) as session:
        session.info.update(cross_tenant_info("test reads usage"))
        stored = session.execute(select(ModelUsageRow.calls)).scalars().all()
        assert 3 in stored


@requires_postgres
def test_a_new_month_starts_fresh(engine: Engine, tenant: uuid.UUID) -> None:
    with Session(engine) as session:
        charge_model_call(session=session, tenant_id=tenant, limit=1, now=SEPTEMBER)
        session.commit()
        assert charge_model_call(session=session, tenant_id=tenant, limit=1, now=OCTOBER) == 1
        session.commit()


@requires_postgres
def test_a_rolled_back_request_is_not_billed(engine: Engine, tenant: uuid.UUID) -> None:
    with Session(engine) as session:
        charge_model_call(session=session, tenant_id=tenant, limit=5, now=SEPTEMBER)
        session.rollback()
        assert charge_model_call(session=session, tenant_id=tenant, limit=5, now=SEPTEMBER) == 1
        session.commit()


@requires_postgres
def test_racing_requests_cannot_overspend(engine: Engine, tenant: uuid.UUID) -> None:
    """Ten requests for the last three calls: exactly three win."""

    def attempt(_: int) -> bool:
        with Session(engine) as session:
            try:
                charge_model_call(session=session, tenant_id=tenant, limit=3, now=SEPTEMBER)
            except ModelBudgetExceededError:
                session.rollback()
                return False
            session.commit()
            return True

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(attempt, range(10)))

    assert outcomes.count(True) == 3


def test_no_ceiling_means_no_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset MODEL_CALLS_PER_MONTH counts but never refuses."""
    from app.domain import model_budget

    class _Row:
        calls = 10_000

    monkeypatch.setattr(model_budget, "_locked_row", lambda *_a: _Row())
    monkeypatch.setattr(model_budget, "bind_tenant", lambda *_a: None)
    from app.domain import billing

    monkeypatch.setattr(
        billing,
        "get_settings",
        lambda: type("S", (), {"model_calls_per_month": None, "billing_enforced": False})(),
    )

    class _Session:
        def flush(self) -> None:
            pass

    assert (
        model_budget.charge_model_call(
            session=_Session(),  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
        )
        == 10_001
    )
