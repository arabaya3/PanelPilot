"""Tests for `app/models/tables/session.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
Tenant scoping for these tables is asserted in test_tenant.py alongside every
other scoped table, so that rule lives in one place.
"""

from __future__ import annotations

from app.models.tables.base import Base


def test_refresh_tokens_are_stored_by_hash_only() -> None:
    """A leaked dump must not hand over working credentials."""
    columns = Base.metadata.tables["refresh_tokens"].columns
    assert "token_hash" in columns
    assert "token" not in columns, "the raw token must never be a column"
    # Unique, so a replayed value cannot create a second live row.
    assert columns["token_hash"].unique


def test_refresh_tokens_are_revoked_not_deleted() -> None:
    """Rotation needs the superseded row to still be there to recognise a replay."""
    assert "revoked_at" in Base.metadata.tables["refresh_tokens"].columns


def test_an_anonymous_session_maps_to_exactly_one_conversation() -> None:
    """Two trials sharing a conversation would leak history between them."""
    columns = Base.metadata.tables["anonymous_sessions"].columns
    assert columns["diagnostic_session_id"].unique
    assert not columns["diagnostic_session_id"].nullable


def test_an_unclaimed_session_is_the_normal_state() -> None:
    """Claiming is the exception, so the claim columns are nullable."""
    columns = Base.metadata.tables["anonymous_sessions"].columns
    assert columns["claimed_by_user_id"].nullable
    assert columns["claimed_at"].nullable


def test_a_sign_in_session_is_revoked_not_deleted() -> None:
    """A logout must be recognisable afterwards, so a replay is refused as one."""
    columns = Base.metadata.tables["auth_sessions"].columns
    assert columns["revoked_at"].nullable
    assert not columns["user_id"].nullable


def test_a_refresh_token_names_its_session_and_older_ones_may_not() -> None:
    """Nullable for tokens issued before sessions existed; those are refused, not adopted."""
    column = Base.metadata.tables["refresh_tokens"].columns["session_id"]
    assert column.nullable
    (foreign_key,) = column.foreign_keys
    assert foreign_key.column.table.name == "auth_sessions"
    assert foreign_key.ondelete == "CASCADE"
