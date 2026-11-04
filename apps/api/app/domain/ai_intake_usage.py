"""The per-account AI intake allowance and the one call in flight (M6b, S2).

The tenant's monthly model-call ceiling (``model_budget``) bounds what a
tenant may cost; this bounds what one account of it may use of that, and
keeps it to one AI intake call at a time, so parallel tabs cannot fan out.

Both are read and written under the account's row lock, in the same short
transaction that charges the tenant, committed before the provider is asked:
two concurrent requests cannot both see "one left", and a refusal by either
limit charges nothing. The lease taken there is released when the call ends,
however it ends; a worker that dies mid-call leaves it to expire.

A call the provider fails (unavailable, timed out, malformed) stays counted,
as it stays charged to the tenant: the provider was asked. Nothing retries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, TooManyRequestsError
from app.core.tenancy import bind_tenant
from app.domain.model_budget import current_period
from app.models.tables.ai_intake_usage import AiIntakeUsageRow

logger = structlog.get_logger(__name__)

#: How long a lease outlives the call's own deadline before it lapses on its
#: own (a crashed worker never releases it). The call is abandoned at its
#: deadline, so a live lease never needs this long.
LEASE_MARGIN_S = 30.0


class AiUserLimitExceededError(TooManyRequestsError):
    """The account has used this month's AI intake calls."""

    #: Distinct from ``model_budget_exceeded`` (the tenant's) and
    #: ``rate_limited`` (wait a few minutes): this one resets with the month.
    CODE = "ai_user_limit_exceeded"

    def __init__(self, *, limit: int, period: str, retry_after_seconds: int | None) -> None:
        """Name the cap and the month it is used up for."""
        super().__init__(
            f"you have used your {limit} AI intake calls for {period}; "
            "the allowance resets at the start of next month",
            retry_after_seconds=retry_after_seconds,
            code=self.CODE,
            params={"limit": limit, "period": period},
        )


class AiRequestInFlightError(ConflictError):
    """The account already has an AI intake call running (another tab)."""

    CODE = "ai_request_in_flight"

    def __init__(self) -> None:
        """Say so under this refusal's own code."""
        super().__init__(
            "an AI proposal you asked for is still being prepared; wait for it to finish",
            code=self.CODE,
        )


@dataclass(frozen=True)
class Lease:
    """The call in flight: its lease id, and the account's allowance after it."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    lease_id: uuid.UUID
    limit: int | None
    used: int

    @property
    def remaining(self) -> int | None:
        """Calls left this month after this one, ``None`` with no cap."""
        return None if self.limit is None else max(self.limit - self.used, 0)


def _deadline_s() -> float:
    from app.ai.intake_proposer import DEADLINE_S

    return DEADLINE_S


def _locked_row(session: Session, tenant: uuid.UUID, user: uuid.UUID) -> AiIntakeUsageRow:
    """The account's row, created if needed, locked for update."""
    statement = (
        select(AiIntakeUsageRow)
        .where(AiIntakeUsageRow.tenant_id == tenant, AiIntakeUsageRow.user_id == user)
        .with_for_update()
    )
    row = session.execute(statement).scalar_one_or_none()
    if row is not None:
        return row
    try:
        # A savepoint: losing the race to create the row costs this insert only.
        with session.begin_nested():
            session.add(
                AiIntakeUsageRow(tenant_id=tenant, user_id=user, period=current_period(), calls=0)
            )
    except IntegrityError:
        pass
    return session.execute(statement).scalar_one()


def _next_month(now: datetime) -> datetime:
    first = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def acquire(
    *,
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int | None = None,
    now: datetime | None = None,
) -> Lease:
    """Count one AI intake call for the account and take its in-flight lease, or refuse.

    Rides the caller's transaction (the caller commits, with the tenant's
    charge); nothing is counted if it raises.

    Args:
        session: Open session; bound to ``tenant_id`` here.
        tenant_id: The account's tenant.
        user_id: The account.
        limit: The cap; ``AI_INTAKE_CALLS_PER_USER_PER_MONTH`` by default.
        now: Injected for tests.

    Raises:
        AiRequestInFlightError: A call of the account's is still in flight.
        AiUserLimitExceededError: The account's month is used up.
    """
    bind_tenant(session, tenant_id)
    moment = now or datetime.now(UTC)
    cap = limit if limit is not None else get_settings().ai_intake_calls_per_user_per_month
    period = current_period(moment)
    row = _locked_row(session, tenant_id, user_id)
    if row.lease_id is not None and row.lease_expires_at and row.lease_expires_at > moment:
        logger.info("ai_intake.in_flight", tenant_id=str(tenant_id))
        raise AiRequestInFlightError()
    if row.period != period:
        row.period = period
        row.calls = 0
    if cap is not None and row.calls >= cap:
        logger.info("ai_intake.user_limit", tenant_id=str(tenant_id), period=period)
        wait = int((_next_month(moment) - moment).total_seconds()) + 1
        raise AiUserLimitExceededError(limit=cap, period=period, retry_after_seconds=wait)
    row.calls += 1
    row.lease_id = uuid.uuid4()
    row.lease_expires_at = moment + timedelta(seconds=_deadline_s() + LEASE_MARGIN_S)
    session.flush()
    return Lease(
        tenant_id=tenant_id, user_id=user_id, lease_id=row.lease_id, limit=cap, used=row.calls
    )


def release(*, session: Session, lease: Lease) -> None:
    """End the call's lease (only its own: a later call's lease is left alone). Commits."""
    try:
        bind_tenant(session, lease.tenant_id)
        row = session.execute(
            select(AiIntakeUsageRow)
            .where(
                AiIntakeUsageRow.tenant_id == lease.tenant_id,
                AiIntakeUsageRow.user_id == lease.user_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is not None and row.lease_id == lease.lease_id:
            row.lease_id = None
            row.lease_expires_at = None
        session.commit()
    except BaseException:
        session.rollback()
        raise
