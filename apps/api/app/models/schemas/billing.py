"""Request and response schemas for plans and subscriptions."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.plans import Feature, Interval, PlanKey


class PlanOut(BaseModel):
    """One plan, as the pricing page shows it.

    Attributes:
        key: Its key.
        monthly_usd: The price a month for the seats included, billed monthly;
            ``None`` where it is agreed per customer.
        annual_usd: The price a year for the seats included, billed annually.
        seats_included: The accounts the price covers.
        extra_seat_usd: The price a month of each further seat, if sold.
        max_seats: The most accounts it holds.
        model_calls_per_month: The pooled AI allowance a month.
        saved_projects: The projects it may keep; ``None`` for no limit.
        features: What it switches on.
    """

    key: PlanKey
    monthly_usd: Decimal | None
    annual_usd: Decimal | None
    seats_included: int
    extra_seat_usd: Decimal | None
    max_seats: int | None
    model_calls_per_month: int | None
    saved_projects: int | None
    features: list[Feature]


class PlanCatalogue(BaseModel):
    """Every plan, cheapest first.

    Attributes:
        plans: The plans.
        currency: The currency prices are in.
        annual_months_charged: Months charged for a year paid at once.
    """

    plans: list[PlanOut]
    currency: str = "USD"
    annual_months_charged: int


class Entitlements(BaseModel):
    """What the caller's account is on, and what it has used.

    Attributes:
        plan: The plan in force.
        interval: How it is billed.
        status: "free", "active" or "expired".
        current_period_end: Until when a paid plan holds (ISO 8601).
        seats: The accounts paid for.
        seats_used: The accounts the tenant holds.
        model_calls_per_month: The pooled AI allowance; ``None`` for none.
        model_calls_used: Calls made this month.
        saved_projects: The projects it may keep; ``None`` for no limit.
        saved_projects_used: The projects it keeps.
        features: What it switches on.
        enforced: Whether the plan's limits are applied yet; until billing
            opens, every account may use every feature.
        requested_plan: A plan asked for and not yet active.
        requested_interval: Its interval.
        requested_seats: Its seats.
    """

    plan: PlanKey
    interval: Interval
    status: str
    current_period_end: str | None = None
    seats: int
    seats_used: int
    model_calls_per_month: int | None
    model_calls_used: int
    saved_projects: int | None
    saved_projects_used: int
    features: list[Feature]
    enforced: bool
    requested_plan: PlanKey | None = None
    requested_interval: Interval | None = None
    requested_seats: int | None = None


class PlanRequest(BaseModel):
    """Ask to move the account to a plan.

    Attributes:
        plan: The plan.
        interval: Monthly or annual.
        seats: The accounts to pay for.
    """

    plan: PlanKey
    interval: Interval = Interval.MONTHLY
    seats: int = Field(default=1, ge=1, le=1000)
