"""Tests for `app/models/tables/ai_intake_usage.py`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import UniqueConstraint

from app.models.tables.ai_intake_usage import AiIntakeUsageRow
from app.models.tables.tenant import TenantScopedMixin


def test_usage_is_tenant_scoped_and_one_row_per_account() -> None:
    assert issubclass(AiIntakeUsageRow, TenantScopedMixin)
    table: Any = AiIntakeUsageRow.__table__
    unique = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert [sorted(col.name for col in c.columns) for c in unique] == [["tenant_id", "user_id"]]


def test_the_lease_is_optional() -> None:
    columns = AiIntakeUsageRow.__table__.c
    assert columns.lease_id.nullable
    assert columns.lease_expires_at.nullable
    assert not columns.calls.nullable
