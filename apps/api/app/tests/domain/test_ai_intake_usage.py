"""Tests for `app/domain/ai_intake_usage.py` that need no database.

The locking, the cap and the lease against Postgres, with real concurrent
sessions, are in ``test_ai_intake_privacy_db.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.errors import ConflictError, TooManyRequestsError, status_for
from app.domain import ai_intake_usage
from app.domain.ai_intake_usage import AiRequestInFlightError, AiUserLimitExceededError, Lease


def _lease(limit: int | None, used: int) -> Lease:
    return Lease(
        tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), lease_id=uuid.uuid4(), limit=limit, used=used
    )


def test_remaining_after_this_call() -> None:
    assert _lease(100, 1).remaining == 99
    assert _lease(2, 2).remaining == 0
    assert _lease(None, 7).remaining is None


def test_the_limit_refusal_is_its_own_429_naming_limit_and_month() -> None:
    refused = AiUserLimitExceededError(limit=5, period="2026-11", retry_after_seconds=10)
    assert isinstance(refused, TooManyRequestsError)
    assert status_for(refused) == 429
    assert refused.code == "ai_user_limit_exceeded"
    assert refused.params == {"limit": "5", "period": "2026-11"}
    assert refused.retry_after_seconds == 10


def test_a_call_in_flight_is_a_409_of_its_own() -> None:
    refused = AiRequestInFlightError()
    assert isinstance(refused, ConflictError)
    assert status_for(refused) == 409
    assert refused.code == "ai_request_in_flight"


def test_the_next_month_starts_at_midnight_utc() -> None:
    assert ai_intake_usage._next_month(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert ai_intake_usage._next_month(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(
        2026, 2, 1, tzinfo=UTC
    )


def test_a_lease_outlives_the_deadline_only_by_its_margin() -> None:
    assert ai_intake_usage._deadline_s() == 60.0
    assert 0 < ai_intake_usage.LEASE_MARGIN_S <= 60
