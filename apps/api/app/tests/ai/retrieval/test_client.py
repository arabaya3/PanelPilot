"""Tests for `app/ai/retrieval/client.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.retrieval.client import IndexTarget, index_chunk
from app.ai.retrieval.mappings import REQUIRED_FIELDS


def test_index_chunk_refuses_a_document_with_a_null_required_field() -> None:
    """The "none left null on ingest" criterion, enforced not documented.

    Strict mapping catches a typo'd field name but not an omitted one, so this
    is the check that actually holds the line.
    """
    document: dict[str, Any] = dict.fromkeys(REQUIRED_FIELDS, "x")
    document["page"] = None
    del document["source_url"]

    with pytest.raises(ValueError, match="required fields missing or null") as caught:
        index_chunk(IndexTarget.STAGING, chunk_id="c1", document=document)
    # The message must name every gap, not just the first.
    assert "page" in str(caught.value)
    assert "source_url" in str(caught.value)
