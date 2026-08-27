"""Tests for `app/models/tables/tenant.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

``tenant_id`` is the only thing separating one customer's diagnostic history
from another's, so these assert the column is structurally impossible to omit
rather than merely conventional.
"""

from __future__ import annotations

import pytest

from app.models.tables import calculations, diagnostics, escalation, ingestion, user
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow, TenantScopedMixin

# Rows owned by a customer. Anything absent here is shared infrastructure and
# must justify that below.
TENANT_SCOPED = {
    "users",
    "diagnostic_sessions",
    "calculation_records",
    "escalation_items",
    # Issued credentials belong to the tenant that owns them.
    "refresh_tokens",
    # Scoped from creation: a trial gets a provisional tenant immediately,
    # because diagnostic_sessions.tenant_id is NOT NULL. See BE-002.
    "anonymous_sessions",
    # A flagged answer carries the customer's question and the content they
    # were shown, which is as confidential as any other turn. See AI-014.
    "flagged_answers",
}

# Shared corpus and audit infrastructure. Adding a tenant column to these would
# imply per-customer documentation, which is not the model.
DELIBERATELY_UNSCOPED = {
    "tenants",
    "roles",
    "user_roles",
    "crawl_jobs",
    "staged_documents",
    "verification_items",
    "promotion_audits",
    "source_health",
    "diagnostic_turns",  # reached only through its tenant-scoped session
}


def test_every_table_is_either_tenant_scoped_or_deliberately_not() -> None:
    """No table may quietly land in neither set.

    A new customer-data table without ``tenant_id`` is a cross-tenant leak
    waiting to happen, and the failure is silent — queries just return other
    people's rows. This forces the decision to be made explicitly.
    """
    known = TENANT_SCOPED | DELIBERATELY_UNSCOPED
    actual = set(Base.metadata.tables)
    unclassified = actual - known
    assert not unclassified, (
        f"{unclassified} are neither tenant-scoped nor listed as deliberately "
        "shared. Decide which, and say so here."
    )
    assert not known - actual, f"{known - actual} listed but no longer exist"


@pytest.mark.parametrize("table_name", sorted(TENANT_SCOPED))
def test_tenant_scoped_tables_carry_a_non_nullable_indexed_tenant_id(
    table_name: str,
) -> None:
    table = Base.metadata.tables[table_name]
    assert "tenant_id" in table.columns, f"{table_name} has no tenant_id"
    column = table.columns["tenant_id"]
    # Nullable would let a row exist owned by nobody, which every tenant-filtered
    # query would then silently exclude — or worse, include.
    assert not column.nullable, f"{table_name}.tenant_id is nullable"
    indexed = any("tenant_id" in {c.name for c in idx.columns} for idx in table.indexes)
    assert indexed, f"{table_name}.tenant_id is not indexed; every query filters on it"


@pytest.mark.parametrize("table_name", sorted(TENANT_SCOPED))
def test_tenant_foreign_key_restricts_deletion(table_name: str) -> None:
    """Deleting a tenant must not silently cascade away their records."""
    table = Base.metadata.tables[table_name]
    fks = [fk for fk in table.foreign_keys if fk.column.table.name == "tenants"]
    assert fks, f"{table_name}.tenant_id has no foreign key to tenants"
    assert all(
        fk.ondelete == "RESTRICT" for fk in fks
    ), f"{table_name} does not RESTRICT on tenant deletion"


def test_source_health_is_not_tenant_scoped() -> None:
    """The corpus is shared infrastructure, not customer data.

    Asserted rather than assumed: scoping it by tenant would mean each customer
    crawls their own copy of the same manufacturer manuals.
    """
    assert "tenant_id" not in Base.metadata.tables["source_health"].columns


def test_tenant_slug_is_unique() -> None:
    """Slugs appear in URLs; two tenants sharing one is a routing ambiguity."""
    table = Base.metadata.tables["tenants"]
    slug_indexes = [idx for idx in table.indexes if {c.name for c in idx.columns} == {"slug"}]
    assert slug_indexes, "tenants.slug is not indexed"
    assert all(idx.unique for idx in slug_indexes), "tenants.slug is not unique"


def test_scoped_mixin_is_what_supplies_the_column() -> None:
    """The column comes from one shared mixin, not copy-paste per table.

    A hand-rolled tenant_id could differ in nullability or delete rule without
    anything noticing.
    """
    for model in (user.User, diagnostics.DiagnosticSessionRow, calculations.CalculationRecordRow):
        assert issubclass(model, TenantScopedMixin), f"{model.__name__} rolls its own tenant_id"
    assert issubclass(escalation.EscalationItemRow, TenantScopedMixin)
    assert not issubclass(escalation.SourceHealthRow, TenantScopedMixin)
    # Referenced so the imports are load-bearing rather than incidental.
    assert ingestion.StagedDocumentRow.__tablename__ == "staged_documents"
    assert TenantRow.__tablename__ == "tenants"
