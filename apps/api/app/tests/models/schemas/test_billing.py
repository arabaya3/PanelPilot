"""Tests for `app/models/schemas/billing.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.plans import Interval, PlanKey
from app.models.schemas.billing import PlanRequest


def test_a_request_defaults_to_one_seat_monthly() -> None:
    request = PlanRequest(plan=PlanKey.ENGINEER)
    assert request.interval is Interval.MONTHLY
    assert request.seats == 1


def test_a_request_refuses_an_unknown_plan_or_no_seats() -> None:
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({"plan": "gold"})
    with pytest.raises(ValidationError):
        PlanRequest(plan=PlanKey.TEAM, seats=0)
