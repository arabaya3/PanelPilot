"""A tenant's subscription: what it is on, what it asked for, what it may do.

The plan in force is read here and nowhere else, so every limit -- the
monthly model calls, the saved projects, a feature -- answers to the same
row. A tenant with no subscription, or whose paid period has ended, is on
the free plan.

Limits are applied only once ``BILLING_ENFORCED`` is set. Until billing
opens there is no way to pay, so refusing a feature would only lock out the
engineers trying the product; the entitlements still say what each plan
allows, and the model-call ceiling stays ``MODEL_CALLS_PER_MONTH``.

A plan is asked for by the account and activated by an operator
(``set-plan`` in the worker) or, once one is chosen, a payment provider.
Asking changes nothing the account may do.

Every function scoped to a caller binds the session to its tenant first
(ADR 0003).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.domain import plans
from app.domain.plans import Feature, Interval, Plan, PlanKey
from app.models.schemas.auth import CurrentUser
from app.models.schemas.billing import Entitlements, PlanCatalogue, PlanOut, PlanRequest
from app.models.tables.billing import SubscriptionRow
from app.models.tables.design_projects import DesignProjectRow
from app.models.tables.tenant import ModelUsageRow, TenantRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)


def catalogue() -> PlanCatalogue:
    """Every plan, as the pricing page shows it.

    Returns:
        The plans in catalogue order.
    """
    return PlanCatalogue(
        plans=[_plan_out(p) for p in plans.PLANS.values()],
        annual_months_charged=plans.ANNUAL_MONTHS_CHARGED,
    )


def _plan_out(plan: Plan) -> PlanOut:
    annual = None
    if plan.monthly_usd is not None:
        annual = plan.price_usd(Interval.ANNUAL, plan.seats_included)
    return PlanOut(
        key=plan.key,
        monthly_usd=plan.monthly_usd,
        annual_usd=annual,
        seats_included=plan.seats_included,
        extra_seat_usd=plan.extra_seat_usd,
        max_seats=plan.max_seats,
        model_calls_per_month=plan.model_calls_per_month,
        saved_projects=plan.saved_projects,
        features=sorted(plan.features),
    )


def enforced() -> bool:
    """Whether plan limits are applied (``BILLING_ENFORCED``)."""
    return get_settings().billing_enforced


def _tenant(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc


def _row(session: Session) -> SubscriptionRow | None:
    return session.scalars(select(SubscriptionRow)).one_or_none()


def _in_force(row: SubscriptionRow | None, now: datetime) -> tuple[Plan, str]:
    """The plan a subscription holds now, and its status."""
    if row is None or row.plan == PlanKey.FREE.value:
        return plans.PLANS[PlanKey.FREE], "free"
    if row.current_period_end is not None and row.current_period_end <= now:
        return plans.PLANS[PlanKey.FREE], "expired"
    return plans.plan(row.plan), "active"


def plan_of(*, session: Session, tenant_id: str | uuid.UUID, now: datetime | None = None) -> Plan:
    """The plan a tenant is on now.

    Args:
        session: Open database session.
        tenant_id: The tenant.
        now: Injected for tests.

    Returns:
        Its plan; the free plan without a subscription in force.
    """
    bind_tenant(session, _tenant(tenant_id))
    return _in_force(_row(session), now or datetime.now(UTC))[0]


def model_call_ceiling(*, session: Session, tenant_id: str | uuid.UUID) -> int | None:
    """The model calls a tenant may make this month.

    Args:
        session: Open database session.
        tenant_id: The tenant.

    Returns:
        Its plan's pooled allowance once billing is enforced; until then
        ``MODEL_CALLS_PER_MONTH``. ``None`` for no ceiling.
    """
    if not enforced():
        return get_settings().model_calls_per_month
    return plan_of(session=session, tenant_id=tenant_id).model_calls_per_month


def require_feature(*, session: Session, user: CurrentUser, feature: Feature) -> None:
    """Refuse a feature the caller's plan does not include.

    Args:
        session: Open database session.
        user: The caller.
        feature: The feature asked for.

    Raises:
        AuthorizationError: If billing is enforced and the plan lacks it.
    """
    if not enforced():
        return
    plan = plan_of(session=session, tenant_id=user.tenant_id)
    if feature not in plan.features:
        raise AuthorizationError(
            f"the {plan.key.value} plan does not include {feature.value}",
            code="plan_feature",
            params={"plan": plan.key.value, "feature": feature.value},
        )


def seat_limit(*, session: Session, tenant_id: str | uuid.UUID) -> int | None:
    """The accounts a tenant may hold.

    Args:
        session: Open database session.
        tenant_id: The tenant.

    Returns:
        The seats paid for, or the plan's included seats, once billing is
        enforced; ``None`` (no limit) until then.
    """
    if not enforced():
        return None
    bind_tenant(session, _tenant(tenant_id))
    row = _row(session)
    plan, status = _in_force(row, datetime.now(UTC))
    if status == "active" and row is not None:
        return row.seats
    return plan.seats_included


def require_project_room(*, session: Session, user: CurrentUser) -> None:
    """Refuse a new saved project beyond the caller's plan.

    Args:
        session: Open database session.
        user: The caller.

    Raises:
        AuthorizationError: If billing is enforced and the plan's saved
            projects are all used.
    """
    if not enforced():
        return
    plan = plan_of(session=session, tenant_id=user.tenant_id)
    if plan.saved_projects is None:
        return
    held = session.scalar(select(func.count()).select_from(DesignProjectRow)) or 0
    if held >= plan.saved_projects:
        raise AuthorizationError(
            f"the {plan.key.value} plan keeps {plan.saved_projects} projects",
            code="plan_projects",
            params={"plan": plan.key.value, "limit": plan.saved_projects},
        )


def entitlements(
    *, session: Session, user: CurrentUser, now: datetime | None = None
) -> Entitlements:
    """What the caller's account is on and has used.

    Args:
        session: Open database session.
        user: The caller.
        now: Injected for tests.

    Returns:
        The plan, its limits and the account's use of them.
    """
    tenant = _tenant(user.tenant_id)
    bind_tenant(session, tenant)
    moment = now or datetime.now(UTC)
    row = _row(session)
    plan, status = _in_force(row, moment)
    seats_used = session.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    calls = session.scalar(
        select(ModelUsageRow.calls).where(
            ModelUsageRow.period == moment.astimezone(UTC).strftime("%Y-%m")
        )
    )
    projects = session.scalar(select(func.count()).select_from(DesignProjectRow))
    active = status == "active" and row is not None
    return Entitlements(
        plan=plan.key,
        interval=Interval(row.interval) if active and row else Interval.MONTHLY,
        status=status,
        current_period_end=(
            row.current_period_end.isoformat()
            if active and row and row.current_period_end
            else None
        ),
        seats=row.seats if active and row else plan.seats_included,
        seats_used=seats_used or 0,
        model_calls_per_month=model_call_ceiling(session=session, tenant_id=tenant),
        model_calls_used=calls or 0,
        saved_projects=plan.saved_projects if enforced() else None,
        saved_projects_used=projects or 0,
        features=sorted(plan.features) if enforced() else sorted(Feature),
        enforced=enforced(),
        requested_plan=PlanKey(row.requested_plan) if row and row.requested_plan else None,
        requested_interval=(
            Interval(row.requested_interval) if row and row.requested_interval else None
        ),
        requested_seats=row.requested_seats if row else None,
    )


def request_plan(
    *, session: Session, user: CurrentUser, request: PlanRequest, now: datetime | None = None
) -> Entitlements:
    """Record the plan the caller's account asks to move to.

    Args:
        session: Open database session. The caller commits.
        user: The caller, recorded as who asked.
        request: The plan, interval and seats.
        now: Injected for tests.

    Returns:
        The entitlements, the request included; what the account may do is
        unchanged until the plan is activated.

    Raises:
        ValidationError: For the free plan, or seats the plan cannot hold.
    """
    plan = plans.PLANS[request.plan]
    if plan.key is PlanKey.FREE:
        raise ValidationError("the free plan needs no request", code="plan_free")
    plan.check_seats(request.seats)
    tenant = _tenant(user.tenant_id)
    bind_tenant(session, tenant)
    row = _row(session)
    if row is None:
        row = SubscriptionRow(tenant_id=tenant)
        session.add(row)
    row.requested_plan = request.plan.value
    row.requested_interval = request.interval.value
    row.requested_seats = request.seats
    row.requested_by = user.email
    row.requested_at = now or datetime.now(UTC)
    session.flush()
    logger.info(
        "billing.plan_requested",
        tenant_id=str(tenant),
        plan=request.plan.value,
        interval=request.interval.value,
        seats=request.seats,
    )
    return entitlements(session=session, user=user, now=now)


def activate(
    *,
    session: Session,
    tenant_slug: str,
    plan_key: str,
    interval: str,
    seats: int,
    until: datetime | None,
    provider: str = "manual",
    provider_ref: str | None = None,
) -> SubscriptionRow:
    """Put a tenant on a plan: an operator's act, or a payment provider's.

    Args:
        session: A session that sees every tenant (an operator's). The
            caller commits.
        tenant_slug: The tenant, by its slug.
        plan_key: The plan.
        interval: "monthly" or "annual".
        seats: The accounts paid for.
        until: The end of the paid period; ``None`` for the free plan or an
            agreement without one.
        provider: Who activated it.
        provider_ref: The provider's own reference.

    Returns:
        The subscription as saved; a matching request is cleared.

    Raises:
        NotFoundError: If no tenant has that slug.
        ValidationError: For an unknown plan or interval, or seats the plan
            cannot hold.
    """
    plan = plans.plan(plan_key)
    try:
        billed = Interval(interval)
    except ValueError as exc:
        raise ValidationError(f"no interval {interval!r}", code="plan_interval") from exc
    plan.check_seats(seats)
    tenant = session.scalars(select(TenantRow).where(TenantRow.slug == tenant_slug)).one_or_none()
    if tenant is None:
        raise NotFoundError(f"no tenant {tenant_slug!r}")
    row = session.scalars(
        select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant.id)
    ).one_or_none()
    if row is None:
        row = SubscriptionRow(tenant_id=tenant.id)
        session.add(row)
    row.plan = plan.key.value
    row.interval = billed.value
    row.seats = seats
    row.current_period_end = until
    row.provider = provider
    row.provider_ref = provider_ref
    if row.requested_plan == plan.key.value:
        row.requested_plan = row.requested_interval = row.requested_by = None
        row.requested_seats = None
        row.requested_at = None
    session.flush()
    logger.info(
        "billing.plan_activated",
        tenant_id=str(tenant.id),
        plan=plan.key.value,
        interval=billed.value,
        seats=seats,
        provider=provider,
    )
    return row
