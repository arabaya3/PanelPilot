"""Tests for `app/domain/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotImplementedYetError
from app.domain.search import search_documents
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.search import SearchRequest


def test_search_says_it_is_not_available_yet() -> None:
    """A 501 with a reason, not the anonymous 500 of a bare NotImplementedError."""
    user = CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )
    with pytest.raises(NotImplementedYetError, match="not available yet"):
        search_documents(user=user, request=SearchRequest(query="F0001"))
