"""Tests for `app/models/tables/team.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from app.models.tables.team import InvitationRow
from app.models.tables.tenant import TenantScopedMixin


def test_an_invitation_is_tenant_scoped_and_its_token_unique() -> None:
    assert issubclass(InvitationRow, TenantScopedMixin)
    assert InvitationRow.__table__.c.token_hash.unique
