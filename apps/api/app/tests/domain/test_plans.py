"""Tests for `app/domain/plans.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.domain import plans
from app.domain.plans import Feature, Interval, PlanKey


def test_annual_is_ten_months_for_twelve() -> None:
    engineer = plans.PLANS[PlanKey.ENGINEER]
    assert engineer.price_usd(Interval.MONTHLY, 1) == Decimal(29)
    assert engineer.price_usd(Interval.ANNUAL, 1) == Decimal(290)


def test_seats_beyond_the_included_are_charged_each() -> None:
    team = plans.PLANS[PlanKey.TEAM]
    assert team.price_usd(Interval.MONTHLY, 3) == Decimal(79)
    assert team.price_usd(Interval.MONTHLY, 5) == Decimal(79 + 2 * 25)


def test_a_plan_refuses_seats_it_cannot_hold() -> None:
    with pytest.raises(ValidationError) as caught:
        plans.PLANS[PlanKey.ENGINEER].check_seats(2)
    assert caught.value.code == "plan_seats"
    with pytest.raises(ValidationError):
        plans.PLANS[PlanKey.TEAM].check_seats(11)
    with pytest.raises(ValidationError):
        plans.PLANS[PlanKey.TEAM].check_seats(0)
    plans.PLANS[PlanKey.ENTERPRISE].check_seats(500)


def test_enterprise_is_priced_by_agreement() -> None:
    assert plans.PLANS[PlanKey.ENTERPRISE].price_usd(Interval.ANNUAL, 60) is None


def test_free_lacks_the_paid_features_and_paid_plans_have_them() -> None:
    free = plans.PLANS[PlanKey.FREE]
    assert Feature.ECAD_EXPORT not in free.features
    assert Feature.APPROVAL_WORKFLOW not in free.features
    for key in (PlanKey.ENGINEER, PlanKey.TEAM, PlanKey.COMPANY):
        assert plans.PLANS[key].features == frozenset(Feature)


def test_plan_looks_up_by_key_or_refuses() -> None:
    assert plans.plan("team").key is PlanKey.TEAM
    with pytest.raises(ValidationError) as caught:
        plans.plan("gold")
    assert caught.value.code == "plan_unknown"
