"""Tests for `app/models/tables/design_ai_proposals.py`."""

from __future__ import annotations

from app.models.tables.design_ai_proposals import DesignAiProposalRow
from app.models.tables.tenant import TenantScopedMixin


def test_proposals_are_tenant_scoped() -> None:
    assert issubclass(DesignAiProposalRow, TenantScopedMixin)


def test_a_deleted_revision_leaves_its_proposal_used() -> None:
    (fk,) = DesignAiProposalRow.__table__.c.used_by_revision_id.foreign_keys
    assert fk.ondelete == "SET NULL"
    assert "used_at" in DesignAiProposalRow.__table__.c
