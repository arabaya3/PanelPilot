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
from app.ai.retrieval.query_classifier import classify_query
from app.models.schemas.retrieval_config import BlendWeights, QueryType, RetrievalConfig
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


def test_the_default_pipeline_blend_is_a_real_blend() -> None:
    """The named pipeline is the fallback for a search issued without a config.

    It must still fuse both legs: a fallback that silently became single-leg
    would degrade retrieval for exactly the callers who did not think about
    tuning.
    """
    weights = hybrid_search.search_pipeline_body()["phase_results_processors"][0][
        "normalization-processor"
    ]["combination"]["parameters"]["weights"]
    assert len(weights) == 2
    assert all(w > 0 for w in weights)
    assert pytest.approx(1.0) == sum(weights)


def test_the_blend_is_conditional_on_the_query() -> None:
    """AI-002's central change: one global weight underserves one query type.

    A fault code is a literal token BM25 finds; a described symptom is not in
    the manual's words at all and only the vector leg reaches it.
    """
    config = RetrievalConfig()
    code = config.weights_for(classify_query("F0001 on the ACS880"))
    symptom = config.weights_for(
        classify_query("the drive trips when the conveyor starts under load")
    )
    assert code.bm25 > code.vector
    assert symptom.vector > symptom.bm25


def test_the_inline_pipeline_carries_the_query_weights() -> None:
    """Carry this query's weights in the request body.

    Weights travel with the request rather than needing a named pipeline per
    query type, which would be a cluster-state write on every re-tune.
    """
    weights = BlendWeights(bm25=0.85, vector=0.15)
    body = hybrid_search.inline_pipeline(weights)
    parameters = body["phase_results_processors"][0]["normalization-processor"]["combination"][
        "parameters"
    ]
    assert parameters["weights"] == [0.85, 0.15]


def test_the_weight_order_matches_the_leg_order() -> None:
    """If these disagree each weight lands on the wrong leg.

    That reads as a tuning problem and is not one — no amount of re-tuning
    fixes weights applied to the wrong signal.
    """
    body = hybrid_search._build_query(
        query="q", vector=[0.0] * EMBEDDING_DIMENSIONS, filters=None, top_k=3
    )
    legs = body["query"]["hybrid"]["queries"]
    # Leg 0 is lexical, leg 1 is vector; as_pipeline_weights emits [bm25, vector].
    assert "match" in legs[0]["bool"]["must"][0]
    assert "knn" in legs[1]
    assert BlendWeights(bm25=0.7, vector=0.3).as_pipeline_weights() == [0.7, 0.3]


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


# --- unit: the conditional blend reaches the actual request -----------------


class _RecordingClient:
    """Captures the search request instead of issuing it."""

    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None
        self.kwargs: dict[str, Any] | None = None

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        self.body = kwargs["body"]
        return {"hits": {"hits": []}}


def _capture_request(monkeypatch: pytest.MonkeyPatch, query: str, **kwargs: Any) -> dict[str, Any]:
    """Run a production search against a fake client and return the request body.

    ``top_k`` and ``min_score`` are passed explicitly so this needs no real
    settings — the request body is what is under test, not configuration
    loading.
    """
    client = _RecordingClient()
    monkeypatch.setattr(hybrid_search, "get_client", lambda: client)
    monkeypatch.setattr(hybrid_search, "embed_query", lambda _q: [0.0] * EMBEDDING_DIMENSIONS)
    monkeypatch.setattr(hybrid_search, "resolve_index", lambda _t: "test-index")
    # The default path seeds from settings, which need no real values here.
    monkeypatch.setattr(hybrid_search, "retrieval_config_from_settings", RetrievalConfig)
    kwargs.setdefault("top_k", 5)
    kwargs.setdefault("min_score", 0.0)
    hybrid_search.search(query, **kwargs)
    assert client.body is not None
    return client.body


def _blend_in(body: dict[str, Any]) -> list[float]:
    """Pull the blend weights out of a captured request body.

    Args:
        body: The captured OpenSearch request body.

    Returns:
        The ``[bm25, vector]`` weights the request carries.
    """
    processor = body["search_pipeline"]["phase_results_processors"][0]
    weights = processor["normalization-processor"]["combination"]["parameters"]["weights"]
    return [float(w) for w in weights]


def test_the_request_carries_the_blend_for_this_query_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI-002 is only real if the weights reach the actual request.

    A config object nothing sends is a tuning knob wired to nothing — which is
    indistinguishable, from the outside, from tuning that does not work.
    """
    body = _capture_request(monkeypatch, "F0001")
    weights = body["search_pipeline"]["phase_results_processors"][0]["normalization-processor"][
        "combination"
    ]["parameters"]["weights"]
    expected = RetrievalConfig().weights_for(QueryType.FAULT_CODE)
    assert weights == expected.as_pipeline_weights()


def test_two_query_types_produce_different_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conditioning must be observable at the request, not just in config.

    If both queries sent the same weights, every test above could pass while
    retrieval behaved identically for a fault code and a described symptom.
    """

    def weights_for(query: str) -> list[float]:
        return _blend_in(_capture_request(monkeypatch, query))

    code = weights_for("F0001")
    symptom = weights_for("the drive trips when the conveyor starts under load")
    assert code != symptom
    assert code[0] > code[1], "a fault code should lean lexical"
    assert symptom[1] > symptom[0], "a described symptom should lean semantic"


def test_an_explicit_config_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-tuning must be a config change, not a code change."""
    tuned = RetrievalConfig(
        weights={
            QueryType.FAULT_CODE: BlendWeights(bm25=0.95, vector=0.05),
            QueryType.PARAMETER_LOOKUP: BlendWeights(bm25=0.6, vector=0.4),
            QueryType.SYMPTOM_DESCRIPTION: BlendWeights(bm25=0.2, vector=0.8),
        }
    )
    body = _capture_request(monkeypatch, "F0001", config=tuned)
    weights = body["search_pipeline"]["phase_results_processors"][0]["normalization-processor"][
        "combination"
    ]["parameters"]["weights"]
    assert weights == [0.95, 0.05]


def test_only_one_pipeline_applies_to_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never both a named pipeline and an inline one.

    Sending a `search_pipeline` query parameter alongside a body-level
    definition leaves which applies to precedence rules. If the named one won,
    every query would silently run under the fixed fallback blend while every
    test still passed — the shape of a defect a previous review found in this
    same file.
    """
    client = _RecordingClient()
    monkeypatch.setattr(hybrid_search, "get_client", lambda: client)
    monkeypatch.setattr(hybrid_search, "embed_query", lambda _q: [0.0] * EMBEDDING_DIMENSIONS)
    monkeypatch.setattr(hybrid_search, "resolve_index", lambda _t: "test-index")
    monkeypatch.setattr(hybrid_search, "retrieval_config_from_settings", RetrievalConfig)

    hybrid_search.search("F0001", top_k=5, min_score=0.0)

    assert client.kwargs is not None
    assert "search_pipeline" in client.kwargs["body"]
    assert "params" not in client.kwargs, "a query-param pipeline would compete with the inline one"


def test_the_environment_still_configures_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    """RETRIEVAL_TOP_K is a documented variable and must still take effect.

    RetrievalConfig is the single source the query path reads, so this bridge
    is the one place the two meet — if it stopped honouring settings, an
    operator's configuration would be silently ignored.
    """

    class _Settings:
        retrieval_top_k = 3
        retrieval_min_score = 0.42

    monkeypatch.setattr(hybrid_search, "get_settings", _Settings)
    config = hybrid_search.retrieval_config_from_settings()
    assert config.top_k == 3
    assert config.min_score == 0.42


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
