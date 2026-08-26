"""Tests for `app/ai/retrieval/client.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.retrieval.client import IndexTarget, ensure_index, index_chunk
from app.ai.retrieval.mappings import REQUIRED_FIELDS
from app.core.config import Settings


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


def test_resolve_index_maps_each_target_to_its_own_configured_name(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the staging/production mapping itself.

    Everything else resolves index names through this function, including the
    fixtures that test the separation — so swapping these two return values
    would pass the entire suite while sending every answer to the staging
    corpus and every crawl to production. Flagged twice in review as the one
    unasserted link in the ADR 0001 chain.
    """
    from app.ai.retrieval import client as client_module
    from app.ai.retrieval.client import IndexTarget, resolve_index

    # Uses the test settings fixture rather than the ambient environment, so
    # the assertion holds without a configured .env and cannot be affected by
    # whatever index names a developer happens to have exported.
    monkeypatch.setattr(client_module, "get_settings", lambda: settings)
    assert resolve_index(IndexTarget.STAGING) == settings.opensearch_staging_index
    assert resolve_index(IndexTarget.PRODUCTION) == settings.opensearch_production_index
    # And they are genuinely two indices, not one name behind two enum members.
    assert settings.opensearch_staging_index != settings.opensearch_production_index


def test_setup_registers_every_pipeline_a_query_can_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query naming an unregistered pipeline is an error, not a fallback.

    `_search` emits one pipeline name per query type, so setup must register
    all of them. Registering only the default would leave every real query
    referencing something that does not exist.
    """
    from app.ai.retrieval import client as client_module
    from app.ai.retrieval.hybrid_search import pipeline_name_for
    from app.models.schemas.retrieval_config import QueryType, RetrievalConfig

    registered: list[str] = []

    # These stand in for the OpenSearch client surface; the keyword arguments
    # are accepted and ignored because only the pipeline names matter here.
    class _Indices:
        def exists(self, **_kwargs: Any) -> bool:
            return True

        def create(self, **_kwargs: Any) -> None:
            raise AssertionError("the index already exists")

    class _Transport:
        def perform_request(self, _method: str, path: str, **_kwargs: Any) -> None:
            if path.startswith("/_search/pipeline/"):
                registered.append(path.removeprefix("/_search/pipeline/"))

    class _Client:
        transport = _Transport()
        indices = _Indices()

    monkeypatch.setattr(client_module, "get_client", lambda: _Client())
    monkeypatch.setattr(client_module, "resolve_index", lambda _t: "test-index")
    monkeypatch.setattr(
        "app.ai.retrieval.hybrid_search.retrieval_config_from_settings", RetrievalConfig
    )

    ensure_index(IndexTarget.PRODUCTION)

    for query_type in QueryType:
        assert pipeline_name_for(query_type) in registered
