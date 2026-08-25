"""Tests for `app/ai/retrieval/hybrid_search.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Two layers:

* Unit tests over query construction and response mapping — no I/O, always run.
* An integration test that indexes a known corpus into a **real** OpenSearch
  and asserts the expected chunk lands in the top 3. That is BE-003's
  acceptance criterion, and mocking the engine would assert nothing about
  whether the mapping and the hybrid query actually work. It is skipped when
  no OpenSearch is reachable, and CI runs it against a service container.

Embeddings are supplied by the test rather than by ``embed_query``. Choosing
the embedding model is AI-001/AI-002's job, not BE-003's; what BE-003 owns is
the schema, the query, and the score fusion, and those are what is asserted.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from typing import Any

import pytest

from app.ai.retrieval import hybrid_search
from app.ai.retrieval.mappings import (
    EMBEDDING_DIMENSIONS,
    DocType,
    VerificationStatus,
    missing_required_fields,
)
from app.models.schemas.search import SearchFilters

# --- a deterministic stand-in for a real embedding model --------------------


# A tiny hand-built "concept space". Each axis is a topic; a text's vector is
# the topics it mentions. Crude, but genuinely semantic: two passages about
# earth faults land near each other even with no shared vocabulary, which a
# hash-based stub can never do. That is what makes the ablation test below
# able to detect a broken vector leg.
_CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("overcurrent", "f0001", "trip", "current", "shorted", "exceeded"),
    ("earth", "leakage", "ground", "f2330", "insulation"),
    ("undervoltage", "a30003", "dc link", "supply voltage", "precharge"),
    ("ampacity", "derating", "correction", "cable", "xlpe", "pvc", "insulated conductor"),
    ("cooling", "dissipation", "enclosure", "thermal", "heat", "climate"),
)


def _semantic_embed(text: str) -> list[float]:
    """Project text onto a small fixed concept space, then pad deterministically.

    Deterministic and dependency-free, but unlike a hash it carries meaning:
    a paraphrase with no shared words still lands on the same axis. The
    padding keeps the width at EMBEDDING_DIMENSIONS without adding signal.
    """
    lowered = text.lower()
    axes = [float(sum(1 for term in concept if term in lowered)) for concept in _CONCEPTS]
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Padding is tiny relative to a concept hit, so it breaks ties without
    # ever outweighing an actual topic match.
    padding = [
        (digest[i % len(digest)] / 255.0) * 0.001 for i in range(EMBEDDING_DIMENSIONS - len(axes))
    ]
    raw = axes + padding
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


# Kept under the old name so every existing call site reads the same.
_fake_embed = _semantic_embed


# --- unit: query construction ----------------------------------------------


def test_query_runs_both_legs_as_optional_clauses() -> None:
    """Both legs must run; neither may be able to suppress the other."""
    body = hybrid_search._build_query(
        query="overload trip", vector=[0.1] * EMBEDDING_DIMENSIONS, filters=None, top_k=5
    )
    legs = body["query"]["hybrid"]["queries"]
    # Two independently-scored legs, normalised by the pipeline before blending.
    assert len(legs) == 2
    assert legs[0]["bool"]["must"][0]["match"]["content"]["query"] == "overload trip"
    assert "knn" in legs[1]
    assert body["size"] == 5


def test_weights_favour_the_lexical_leg() -> None:
    """Engineers search with part numbers and fault codes, which BM25 handles."""
    assert hybrid_search.BM25_WEIGHT > hybrid_search.VECTOR_WEIGHT
    assert pytest.approx(1.0) == hybrid_search.BM25_WEIGHT + hybrid_search.VECTOR_WEIGHT


def test_filters_do_not_contribute_to_score() -> None:
    """Narrowing by brand must not reorder relevance within that brand."""
    body = hybrid_search._build_query(
        query="q",
        vector=[0.0] * EMBEDDING_DIMENSIONS,
        filters=SearchFilters(manufacturers=["ABB"], document_types=["manual"]),
        top_k=3,
    )
    legs = body["query"]["hybrid"]["queries"]
    lexical = legs[0]["bool"]["filter"]
    vector = legs[1]["knn"]["content_vector"]["filter"]["bool"]["filter"]
    for filters in (lexical, vector):
        assert {"terms": {"brand": ["ABB"]}} in filters
        assert {"terms": {"doc_type": ["manual"]}} in filters
    # Both legs must filter identically, or they score different candidate sets.
    assert lexical == vector


def test_passages_below_min_score_are_dropped() -> None:
    response: dict[str, Any] = {
        "hits": {
            "hits": [
                {"_id": "a", "_score": 0.9, "_source": {"content": "keep", "brand": "ABB"}},
                {"_id": "b", "_score": 0.1, "_source": {"content": "drop", "brand": "ABB"}},
            ]
        }
    }
    passages = hybrid_search._to_passages(response, min_score=0.5)
    assert [p.id for p in passages] == ["a"]


# --- unit: the staging isolation ADR 0001 requires --------------------------


def test_search_exposes_no_index_argument() -> None:
    """`search` must not be steerable at staging by passing an argument."""
    import inspect

    params = set(inspect.signature(hybrid_search.search).parameters)
    assert "index" not in params
    assert "target" not in params


def test_staging_has_a_separately_named_entry_point() -> None:
    """Querying unverified content must be deliberate and greppable."""
    assert hasattr(hybrid_search, "search_staging")
    assert "staging" in hybrid_search.search_staging.__name__


# --- unit: the "no field left null" half of the criterion -------------------


# --- integration: the acceptance criterion ---------------------------------

CORPUS: tuple[dict[str, Any], ...] = (
    {
        "id": "abb-acs880-overload",
        "brand": "ABB",
        "model": "ACS880",
        "doc_type": DocType.MANUAL,
        "page": 88,
        "section": "Fault tracing",
        "source_url": "https://example.invalid/acs880#f0001",
        "content": "Fault F0001 OVERCURRENT indicates the drive output current "
        "exceeded the trip limit. Check motor cable insulation and shorted turns.",
    },
    {
        "id": "abb-acs880-earth-fault",
        "brand": "ABB",
        "model": "ACS880",
        "doc_type": DocType.MANUAL,
        "page": 91,
        "section": "Fault tracing",
        "source_url": "https://example.invalid/acs880#f2330",
        "content": "Fault F2330 EARTH LEAKAGE is reported when the drive detects "
        "an earth fault in the motor or motor cable.",
    },
    {
        "id": "siemens-g120-undervoltage",
        "brand": "Siemens",
        "model": "SINAMICS G120",
        "doc_type": DocType.MANUAL,
        "page": 210,
        "section": "Alarms and faults",
        "source_url": "https://example.invalid/g120#a30003",
        "content": "Alarm A30003 undervoltage in the DC link. Verify the supply "
        "voltage and the precharge contactor.",
    },
    {
        "id": "schneider-cable-derating",
        "brand": "Schneider Electric",
        "model": "EIG-2024",
        "doc_type": DocType.GUIDE,
        "page": 412,
        "section": "Cable sizing",
        "source_url": "https://example.invalid/eig#g6",
        "content": "Ambient temperature correction factors for cable ampacity are "
        "tabulated for PVC and XLPE insulation at 30 degrees reference.",
    },
    {
        "id": "rittal-enclosure-cooling",
        "brand": "Rittal",
        "model": "TS8",
        "doc_type": DocType.DATASHEET,
        "page": 12,
        "section": "Climate control",
        "source_url": "https://example.invalid/ts8#cooling",
        "content": "Required cooling output is the difference between installed "
        "component dissipation and the enclosure effective surface heat loss.",
    },
)

# query -> the chunk id that must appear in the top 3.
EXPECTED: tuple[tuple[str, str], ...] = (
    ("drive tripped on overcurrent F0001", "abb-acs880-overload"),
    ("earth leakage fault on the motor cable", "abb-acs880-earth-fault"),
    ("A30003 undervoltage DC link", "siemens-g120-undervoltage"),
    ("ambient correction factor for cable ampacity", "schneider-cable-derating"),
    ("how much cooling does the enclosure need", "rittal-enclosure-cooling"),
    # Paraphrase-only: shares no content word with the target chunk, which says
    # "EARTH LEAKAGE ... earth fault", never "ground" or "current escaping".
    # BM25 alone cannot serve this; it is the pair that proves the vector leg
    # is doing work. See test_vector_leg_is_load_bearing.
    ("current escaping to ground through the winding", "abb-acs880-earth-fault"),
)


def _opensearch_available() -> bool:
    """Report whether an OpenSearch we may write to is reachable."""
    try:
        from app.ai.retrieval.client import get_client

        return bool(get_client().ping())
    except Exception:
        return False


requires_opensearch = pytest.mark.skipif(
    not os.environ.get("OPENSEARCH_URL") or not _opensearch_available(),
    reason="needs a reachable OpenSearch; CI provides one as a service container",
)


@pytest.fixture
def indexed_corpus() -> Iterator[str]:
    """Build a throwaway production index holding CORPUS, and drop it after."""
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client

    client = get_client()
    name = ensure_index(IndexTarget.PRODUCTION, recreate=True)
    for doc in CORPUS:
        body = {k: v for k, v in doc.items() if k != "id"}
        body["verification_status"] = VerificationStatus.VERIFIED
        body["content_vector"] = _fake_embed(body["content"])
        body["content_hash"] = hashlib.sha256(body["content"].encode()).hexdigest()
        # Guard the "none left null" criterion at write time, not just in docs.
        assert missing_required_fields(body) == [], f"{doc['id']} incomplete"
        client.index(index=name, id=doc["id"], body=body, refresh=True)
    try:
        yield name
    finally:
        client.indices.delete(index=name, ignore=[404])


@requires_opensearch
@pytest.mark.parametrize(("query", "expected_id"), EXPECTED)
def test_expected_chunk_appears_in_top_three(
    query: str,
    expected_id: str,
    indexed_corpus: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-003's acceptance criterion, against a real engine."""
    monkeypatch.setattr(hybrid_search, "embed_query", _fake_embed)
    results = hybrid_search.search(query, top_k=3, min_score=0.0)
    assert results, f"no results at all for {query!r}"
    assert expected_id in [
        p.id for p in results[:3]
    ], f"{expected_id!r} not in top 3 for {query!r}: {[p.id for p in results]}"


@requires_opensearch
def test_results_carry_a_resolvable_citation(
    indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passage with no citation is unusable to cite-or-refuse."""
    monkeypatch.setattr(hybrid_search, "embed_query", _fake_embed)
    results = hybrid_search.search("overcurrent F0001", top_k=3, min_score=0.0)
    top = results[0]
    assert top.citation.manufacturer
    assert top.citation.document_id.startswith("https://")
    assert top.citation.page is not None


@requires_opensearch
def test_brand_filter_excludes_other_manufacturers(
    indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid_search, "embed_query", _fake_embed)
    results = hybrid_search.search("fault", brand="Siemens", top_k=10, min_score=0.0)
    assert results
    assert {p.citation.manufacturer for p in results} == {"Siemens"}


@requires_opensearch
def test_strict_mapping_rejects_an_unknown_field(indexed_corpus: str) -> None:
    """A typo'd field name must fail the write, not create a silent new field."""
    from opensearchpy.exceptions import RequestError

    from app.ai.retrieval.client import get_client

    body = {k: v for k, v in CORPUS[0].items() if k != "id"}
    body["verification_status"] = VerificationStatus.VERIFIED
    body["content_vector"] = _fake_embed(body["content"])
    body["content_hash"] = hashlib.sha256(body["content"].encode()).hexdigest()
    body["brnad"] = "typo"

    with pytest.raises(RequestError):
        get_client().index(index=indexed_corpus, id="bad", body=body, refresh=True)


@requires_opensearch
def test_vector_leg_is_load_bearing(indexed_corpus: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ablation: deleting the kNN leg must break retrieval, or fusion is fiction.

    An earlier version of this module summed un-normalised BM25 and cosine
    scores in one bool query. Because cosine is bounded at 1.0 and BM25 is not,
    the vector leg could contribute at most a few percent and the whole 5-pair
    fixture still passed with the kNN clause deleted entirely. That is the
    regression this test exists to catch: if the suite passes without the
    vector leg, the weights are decorative.
    """
    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    query, expected = "current escaping to ground through the winding", "abb-acs880-earth-fault"

    # With both legs, the paraphrase resolves.
    both = hybrid_search.search(query, top_k=3, min_score=0.0)
    assert expected in [
        p.id for p in both[:3]
    ], f"hybrid failed the paraphrase query: {[p.id for p in both]}"

    # Lexical only: the target shares no content word with the query, so it
    # must NOT be reachable. If it is, the pair is not actually testing fusion.
    original = hybrid_search._build_query

    def lexical_only(**kwargs: Any) -> dict[str, Any]:
        # Drop the hybrid wrapper entirely: the normalisation pipeline declares
        # one weight per leg, so a one-leg hybrid query is rejected outright.
        body = original(**kwargs)
        body["query"] = body["query"]["hybrid"]["queries"][0]
        return body

    monkeypatch.setattr(hybrid_search, "_build_query", lexical_only)
    lexical = hybrid_search.search(query, top_k=3, min_score=0.0)
    assert expected not in [p.id for p in lexical[:3]], (
        "BM25 alone answered a paraphrase-only query, so this fixture cannot "
        "detect a broken vector leg"
    )


@requires_opensearch
def test_scores_are_normalised_into_the_unit_range(
    indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fused scores must be comparable across queries.

    Raw BM25 is unbounded, so a min_score threshold against it means something
    different for every query. The search pipeline min-max normalises each leg
    before blending, which is what makes retrieval_min_score a stable knob.
    """
    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    for query in ("overcurrent F0001", "cooling", "ampacity correction factor"):
        results = hybrid_search.search(query, top_k=5, min_score=0.0)
        assert results, query
        assert all(
            0.0 <= p.score <= 1.0 for p in results
        ), f"{query}: scores outside [0,1]: {[p.score for p in results]}"


@requires_opensearch
def test_unverified_content_is_never_returned(
    indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Index-name isolation is not enough on its own.

    If unverified content ever reaches the production index -- a botched
    re-index, a restore, a bad promotion -- the shared search path must still
    refuse to serve it. See ADR 0001.
    """
    from app.ai.retrieval.client import get_client

    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    body = {k: v for k, v in CORPUS[0].items() if k != "id"}
    body["verification_status"] = VerificationStatus.UNVERIFIED
    body["content_vector"] = _semantic_embed(body["content"])
    body["content_hash"] = hashlib.sha256(body["content"].encode()).hexdigest()
    get_client().index(index=indexed_corpus, id="sneaky", body=body, refresh=True)

    results = hybrid_search.search("overcurrent F0001", top_k=10, min_score=0.0)
    assert "sneaky" not in [p.id for p in results]
    # ...and the verified copy of the same content still comes back.
    assert "abb-acs880-overload" in [p.id for p in results]


@pytest.fixture
def staged_corpus() -> Iterator[str]:
    """Build a throwaway staging index of UNVERIFIED chunks, and drop it after.

    Unverified is the normal state of staging: that is what a reviewer is
    there to look at.
    """
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client

    client = get_client()
    name = ensure_index(IndexTarget.STAGING, recreate=True)
    for doc in CORPUS:
        body = {k: v for k, v in doc.items() if k != "id"}
        body["verification_status"] = VerificationStatus.UNVERIFIED
        body["content_vector"] = _semantic_embed(body["content"])
        body["content_hash"] = hashlib.sha256(body["content"].encode()).hexdigest()
        client.index(index=name, id=doc["id"], body=body, refresh=True)
    try:
        yield name
    finally:
        client.indices.delete(index=name, ignore=[404])


@requires_opensearch
def test_staging_search_returns_unverified_content(
    staged_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer path must see exactly what production refuses to serve.

    Regression test. The verified-only filter was briefly unconditional, which
    made `search_staging` return nothing at all — reviewers could not see the
    content they exist to review, so promotion could never happen. Production
    isolation and reviewer visibility are opposite requirements on the same
    query builder, and only a behavioural test on this path catches the day
    one silently defeats the other.
    """
    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    results = hybrid_search.search_staging("overcurrent F0001", top_k=3, min_score=0.0)
    assert results, "search_staging returned nothing against an unverified corpus"
    assert "abb-acs880-overload" in [p.id for p in results[:3]]


@requires_opensearch
def test_production_search_cannot_see_the_staging_corpus(
    staged_corpus: str, indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both indices populated: `search` must still only read production."""
    from app.ai.retrieval.client import get_client

    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    # A chunk that exists ONLY in staging.
    body = {k: v for k, v in CORPUS[0].items() if k != "id"}
    body["verification_status"] = VerificationStatus.UNVERIFIED
    body["content"] = "Staging exclusive marker chunk about overcurrent."
    body["content_vector"] = _semantic_embed(body["content"])
    body["content_hash"] = hashlib.sha256(body["content"].encode()).hexdigest()
    get_client().index(index=staged_corpus, id="staging-only", body=body, refresh=True)

    assert "staging-only" not in [
        p.id for p in hybrid_search.search("overcurrent F0001", top_k=10, min_score=0.0)
    ]
    assert "staging-only" in [
        p.id for p in hybrid_search.search_staging("overcurrent F0001", top_k=10, min_score=0.0)
    ]


@requires_opensearch
def test_removing_the_knn_leg_breaks_the_paraphrase_pair(
    indexed_corpus: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ablation that actually carries the evidence.

    Neutering the vector to zeros only proves the kNN clause is *present*:
    OpenSearch rejects a zero vector under cosine, so the query dies before
    ranking. Deleting the leg outright is what proves it *ranks*.
    """
    monkeypatch.setattr(hybrid_search, "embed_query", _semantic_embed)
    query, expected = "current escaping to ground through the winding", "abb-acs880-earth-fault"
    original = hybrid_search._build_query

    def without_knn(**kwargs: Any) -> dict[str, Any]:
        body = original(**kwargs)
        # One leg only, so the hybrid wrapper (and its per-leg weights) goes too.
        body["query"] = body["query"]["hybrid"]["queries"][0]
        return body

    monkeypatch.setattr(hybrid_search, "_build_query", without_knn)
    assert expected not in [
        p.id for p in hybrid_search.search(query, top_k=3, min_score=0.0)[:3]
    ], "BM25 alone answered the paraphrase query; the fixture cannot detect a dead vector leg"
