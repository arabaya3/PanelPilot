"""Tests for `app/models/tables/design_projects.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from app.models.tables.design_projects import DesignProjectRow, DesignRevisionRow
from app.models.tables.tenant import TenantScopedMixin


def test_both_tables_are_tenant_scoped() -> None:
    assert issubclass(DesignProjectRow, TenantScopedMixin)
    assert issubclass(DesignRevisionRow, TenantScopedMixin)


def test_a_revision_number_is_unique_within_its_project() -> None:
    constraints = {
        tuple(c.name for c in constraint.columns)
        for constraint in DesignRevisionRow.__table__.constraints  # type: ignore[attr-defined]
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("project_id", "number") in constraints


def test_revisions_go_with_their_project() -> None:
    (fk,) = DesignRevisionRow.__table__.c.project_id.foreign_keys
    assert fk.ondelete == "CASCADE"
