"""Tests for `app/models/schemas/search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.search import SearchRequest


def test_top_k_is_optional() -> None:
    assert SearchRequest(query="drive fault F0001").top_k is None


@pytest.mark.parametrize("top_k", [1, 50])
def test_top_k_within_bounds_is_accepted(top_k: int) -> None:
    assert SearchRequest(query="q", top_k=top_k).top_k == top_k


@pytest.mark.parametrize("top_k", [0, -1, 51, 10_000])
def test_top_k_outside_bounds_is_refused(top_k: int) -> None:
    """Unbounded, top_k is a request for the whole index."""
    with pytest.raises(ValidationError):
        SearchRequest(query="q", top_k=top_k)


def test_the_query_is_bounded() -> None:
    assert SearchRequest(query="x" * 4000)
    with pytest.raises(ValidationError):
        SearchRequest(query="x" * 4001)
