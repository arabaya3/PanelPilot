"""The monthly ceiling on model calls, per tenant.

Every paid model call -- a diagnosis, a photo read, a PLC program -- charges
one unit against its tenant's month before it is made. The free-question
count bounds what a trial may ask; this bounds what any account may cost.

Charged under a row lock, so two requests racing for the last call cannot
both see "one left". The charge rides the caller's transaction: a request
that fails and rolls back is not billed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import TooManyRequestsError
from app.core.tenancy import bind_tenant
from app.models.tables.tenant import ModelUsageRow

logger = structlog.get_logger(__name__)


class ModelBudgetExceededError(TooManyRequestsError):
    """The tenant has used this month's model calls."""


def current_period(now: datetime | None = None) -> str:
    """Return the UTC calendar month a call is charged to.

    Args:
        now: Injected for tests.

    Returns:
        ``YYYY-MM``.
    """
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")


def _locked_row(session: Session, tenant: uuid.UUID, period: str) -> ModelUsageRow:
    """Return this month's row, created if needed, locked for update."""
    statement = (
        select(ModelUsageRow)
        .where(ModelUsageRow.tenant_id == tenant, ModelUsageRow.period == period)
        .with_for_update()
    )
    row = session.execute(statement).scalar_one_or_none()
    if row is not None:
        return row
    try:
        # A savepoint, so losing the race to create the month's row costs
        # this insert only and not the caller's transaction.
        with session.begin_nested():
            session.add(ModelUsageRow(tenant_id=tenant, period=period, calls=0))
    except IntegrityError:
        pass
    return session.execute(statement).scalar_one()


def charge_model_call(
    *,
    session: Session,
    tenant_id: str | uuid.UUID,
    limit: int | None = None,
    now: datetime | None = None,
) -> int:
    """Charge one model call to the tenant's month, or refuse it.

    Args:
        session: Open database session. The caller commits.
        tenant_id: Whose month.
        limit: The ceiling; ``MODEL_CALLS_PER_MONTH`` by default, and none
            when that is unset.
        now: Injected for tests.

    Returns:
        Calls made this month, including this one.

    Raises:
        ModelBudgetExceededError: If the month's calls are used up. Nothing
            is charged.
    """
    tenant = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(tenant_id)
    bind_tenant(session, tenant)
    ceiling = limit if limit is not None else get_settings().model_calls_per_month
    period = current_period(now)

    row = _locked_row(session, tenant, period)
    if ceiling is not None and row.calls >= ceiling:
        logger.info("model_budget.exhausted", tenant_id=str(tenant), period=period)
        raise ModelBudgetExceededError(
            f"this account has used its {ceiling} model calls for {period}; "
            "the allowance resets at the start of next month"
        )
    row.calls += 1
    session.flush()
    return row.calls
