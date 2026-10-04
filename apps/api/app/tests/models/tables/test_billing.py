"""Tests for `app/models/tables/billing.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from app.models.tables.billing import SubscriptionRow
from app.models.tables.tenant import TenantScopedMixin


def test_one_subscription_per_tenant() -> None:
    assert issubclass(SubscriptionRow, TenantScopedMixin)
    constraints = {
        tuple(c.name for c in constraint.columns)
        for constraint in SubscriptionRow.__table__.constraints  # type: ignore[attr-defined]
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id",) in constraints
