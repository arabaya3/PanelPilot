"""Tests for `app/ai/retrieval/client.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.retrieval import client as client_module
from app.ai.retrieval.client import IndexTarget, ensure_index, index_chunk
from app.ai.retrieval.mappings import REQUIRED_FIELDS
from app.core.config import Settings


def _complete_document() -> dict[str, Any]:
    """A chunk body with every required field populated."""
    return dict.fromkeys(REQUIRED_FIELDS, "x")


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


# --- stage_chunk must be unable to publish -----------------------------------
#
# These exist because a mutation survived without them. `test_architecture.py`
# exempts this module from the "only promotion.py names PRODUCTION" rule --- it
# has to, since `resolve_index` maps both targets --- so a `stage_chunk` that
# quietly wrote to production passed every structural check in the suite. The
# guard has to be behavioural here: assert the index actually written.


def test_stage_chunk_writes_to_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason this helper exists separately from `index_chunk`."""
    written: dict[str, object] = {}

    class _Client:
        def index(self, **kwargs: object) -> None:
            # `**kwargs` rather than the real signature: the client is called
            # with `id=`, and naming that parameter here shadows a builtin for
            # no benefit -- the assertions only read the index it wrote to.
            written.update(kwargs)

    monkeypatch.setattr(client_module, "get_client", lambda: _Client())
    monkeypatch.setattr(
        client_module,
        "resolve_index",
        lambda target: f"resolved-{target.value}",
    )

    client_module.stage_chunk(chunk_id="c1", document=_complete_document())

    assert written["index"] == "resolved-staging"


def test_stage_chunk_never_writes_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stated as its own assertion rather than implied by the one above.

    A regression here is the single worst failure this codebase can have:
    unreviewed crawled content becoming live and citable, with no human in the
    loop and nothing in the audit trail. ADR 0001 exists for exactly this.
    """
    targets: list[str] = []

    class _Client:
        def index(self, **kwargs: object) -> None:
            targets.append(str(kwargs["index"]))

    monkeypatch.setattr(client_module, "get_client", lambda: _Client())
    monkeypatch.setattr(client_module, "resolve_index", lambda target: target.value)

    client_module.stage_chunk(chunk_id="c1", document=_complete_document())

    assert targets == ["staging"]
    assert "production" not in targets


def test_stage_chunk_refuses_an_incomplete_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same completeness bar as promotion.

    A chunk missing its page or source_url is unusable as a citation whether it
    is staged or live, and catching it here means a reviewer is never shown an
    item that could not have been promoted anyway.
    """
    monkeypatch.setattr(client_module, "get_client", lambda: None)

    with pytest.raises(ValueError, match="required fields"):
        client_module.stage_chunk(chunk_id="c1", document={"text": "no citation fields"})
