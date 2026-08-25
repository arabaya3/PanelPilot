"""Tests for `app/models/tables/escalation.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
Tenant scoping for escalation_items is asserted in test_tenant.py alongside
every other scoped table, so the rule lives in one place.
"""

from __future__ import annotations

from app.models.tables.base import Base


def test_escalation_records_why_the_assistant_declined() -> None:
    """A refusal without a reason is not actionable by whoever triages it."""
    columns = Base.metadata.tables["escalation_items"].columns
    assert not columns["reason"].nullable
    assert not columns["question"].nullable


def test_escalation_survives_its_session_being_removed() -> None:
    """A gap outlives the conversation that exposed it."""
    table = Base.metadata.tables["escalation_items"]
    assert table.columns["session_id"].nullable
    fks = [fk for fk in table.foreign_keys if fk.column.table.name == "diagnostic_sessions"]
    assert fks, "escalation_items has no session foreign key"
    assert all(fk.ondelete == "SET NULL" for fk in fks)


def test_source_health_counts_consecutive_failures_not_total() -> None:
    """A source that failed last year and works now is healthy.

    A running total would never recover and would eventually mark every source
    unhealthy, which is the same as marking none of them.
    """
    columns = Base.metadata.tables["source_health"].columns
    assert "consecutive_failures" in columns
    assert not columns["consecutive_failures"].nullable
    assert "last_success_at" in columns
