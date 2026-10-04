"""The subscription plans: what each one costs and what it allows.

One catalogue, read by the pricing page, the entitlement checks and the
operator who activates a subscription. Prices are in US dollars, billed
monthly or annually; an annual plan is ten months' price for twelve.

* **Free** -- one engineer, trying the product: a few saved projects and a
  small monthly allowance of model calls.
* **Engineer** -- one engineer working on their own projects, with every
  export, the company settings and approving (signing) a revision.
* **Team** -- a small office: three seats included, more bought one by one,
  and the model allowance pooled.
* **Company** -- a firm: ten seats included, room for fifty, and a larger
  pool.
* **Enterprise** -- priced by agreement; the catalogue only names it.

Framework-agnostic and free of I/O: nothing here touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.core.errors import ValidationError

#: Annual billing charges this many months for twelve.
ANNUAL_MONTHS_CHARGED = 10


class PlanKey(StrEnum):
    """The plans, by the key stored on a subscription."""

    FREE = "free"
    ENGINEER = "engineer"
    TEAM = "team"
    COMPANY = "company"
    ENTERPRISE = "enterprise"


class Interval(StrEnum):
    """How often a subscription is billed."""

    MONTHLY = "monthly"
    ANNUAL = "annual"


class Feature(StrEnum):
    """What a plan may switch on beyond designing a board."""

    #: EPLAN, AutoCAD Electrical, DXF, QElectroTech and AutomationML files.
    ECAD_EXPORT = "ecad_export"
    #: The company's own profile: letters, rules, title block, page order.
    COMPANY_SETTINGS = "company_settings"
    #: A reviewer approves a revision before it is issued.
    APPROVAL_WORKFLOW = "approval_workflow"
    #: The quotation from the parts list.
    QUOTATION = "quotation"


@dataclass(frozen=True)
class Plan:
    """One plan.

    Attributes:
        key: Its key.
        monthly_usd: The price a month, for the seats included; ``None``
            where it is agreed per customer.
        seats_included: The accounts the price covers.
        extra_seat_usd: The price a month of each seat beyond those;
            ``None`` where no more can be bought.
        max_seats: The most accounts the plan holds.
        model_calls_per_month: The model calls the whole account may make a
            month, pooled across its seats; ``None`` for no ceiling.
        saved_projects: The projects it may keep; ``None`` for no limit.
        features: What it switches on.
    """

    key: PlanKey
    monthly_usd: Decimal | None
    seats_included: int
    extra_seat_usd: Decimal | None
    max_seats: int | None
    model_calls_per_month: int | None
    saved_projects: int | None
    features: frozenset[Feature]

    def price_usd(self, interval: Interval, seats: int) -> Decimal | None:
        """What one billing period costs.

        Args:
            interval: Monthly or annual.
            seats: The accounts held; those beyond the included ones are
                charged each.

        Returns:
            The price of the period; ``None`` for a plan priced by agreement.

        Raises:
            ValidationError: If the plan cannot hold that many seats.
        """
        self.check_seats(seats)
        if self.monthly_usd is None:
            return None
        extra = max(0, seats - self.seats_included) * (self.extra_seat_usd or Decimal(0))
        monthly = self.monthly_usd + extra
        months = ANNUAL_MONTHS_CHARGED if interval is Interval.ANNUAL else 1
        return monthly * months

    def check_seats(self, seats: int) -> None:
        """Refuse a seat count the plan cannot hold.

        Args:
            seats: The accounts asked for.

        Raises:
            ValidationError: If it is below one, above the plan's most, or
                above the included seats on a plan that sells no more.
        """
        most = self.max_seats
        if self.extra_seat_usd is None and self.monthly_usd is not None:
            most = self.seats_included
        if seats < 1 or (most is not None and seats > most):
            raise ValidationError(
                f"the {self.key.value} plan holds 1 to {most} seats, not {seats}",
                code="plan_seats",
                params={"plan": self.key.value, "seats": seats, "most": most},
            )


_ALL = frozenset(Feature)

PLANS: dict[PlanKey, Plan] = {
    PlanKey.FREE: Plan(
        key=PlanKey.FREE,
        monthly_usd=Decimal(0),
        seats_included=1,
        extra_seat_usd=None,
        max_seats=1,
        model_calls_per_month=30,
        saved_projects=3,
        features=frozenset({Feature.QUOTATION}),
    ),
    PlanKey.ENGINEER: Plan(
        key=PlanKey.ENGINEER,
        monthly_usd=Decimal(29),
        seats_included=1,
        extra_seat_usd=None,
        max_seats=1,
        model_calls_per_month=500,
        saved_projects=None,
        features=_ALL,
    ),
    PlanKey.TEAM: Plan(
        key=PlanKey.TEAM,
        monthly_usd=Decimal(79),
        seats_included=3,
        extra_seat_usd=Decimal(25),
        max_seats=10,
        model_calls_per_month=2000,
        saved_projects=None,
        features=_ALL,
    ),
    PlanKey.COMPANY: Plan(
        key=PlanKey.COMPANY,
        monthly_usd=Decimal(249),
        seats_included=10,
        extra_seat_usd=Decimal(22),
        max_seats=50,
        model_calls_per_month=8000,
        saved_projects=None,
        features=_ALL,
    ),
    PlanKey.ENTERPRISE: Plan(
        key=PlanKey.ENTERPRISE,
        monthly_usd=None,
        seats_included=50,
        extra_seat_usd=None,
        max_seats=None,
        model_calls_per_month=None,
        saved_projects=None,
        features=_ALL,
    ),
}


def plan(key: str) -> Plan:
    """Look a plan up by its key.

    Args:
        key: As stored on a subscription.

    Returns:
        The plan.

    Raises:
        ValidationError: If no plan has that key.
    """
    try:
        return PLANS[PlanKey(key)]
    except ValueError as exc:
        known = ", ".join(k.value for k in PlanKey)
        raise ValidationError(
            f"no plan {key!r}; plans are {known}", code="plan_unknown", params={"plan": key}
        ) from exc
