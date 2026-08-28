"""Tests for `app/ai/retrieval/embedding.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Everything here is about a failure that does not look like one. A wrong
embedding does not raise, does not log, and does not produce an obviously bad
answer — it produces a *plausible* answer built on the wrong passages, which
the cite-or-refuse guardrail will happily attach citations to. So the tests
weight three things:

* a vector of the wrong width is refused, because the width is baked into the
  index mapping and a mismatch is either rejected by OpenSearch or silently
  accepted against a freshly built index;
* a provider failure raises rather than yielding zeros, because a zero vector
  is a legal kNN input that matches arbitrary neighbours;
* documents and queries are encoded differently, because using one for the
  other costs recall invisibly.

No network. The embedder is injected, which is why it is a parameter at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.ai.retrieval.embedding import (
    Embedder,
    EmbeddingError,
    InputType,
    embed_documents,
    embed_query,
    embed_texts,
)
from app.ai.retrieval.mappings import EMBEDDING_DIMENSIONS


def fake_embedder(
    *,
    dimensions: int = EMBEDDING_DIMENSIONS,
    fail: Exception | None = None,
    count_override: int | None = None,
    record: list[tuple[tuple[str, ...], InputType]] | None = None,
) -> Embedder:
    """Build an embedder that returns predictable vectors.

    Args:
        dimensions: Width of each returned vector.
        fail: Raised instead of returning, to simulate a provider outage.
        count_override: Return this many vectors regardless of input count.
        record: Appended to with each call's arguments.

    Returns:
        An embedder callable.
    """

    def embed(texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        if record is not None:
            record.append((tuple(texts), input_type))
        if fail is not None:
            raise fail
        n = count_override if count_override is not None else len(texts)
        return [[0.1] * dimensions for _ in range(n)]

    return embed


# --- the width contract -------------------------------------------------------


def test_a_correctly_sized_vector_is_returned() -> None:
    vector = embed_query("fault F0001", embedder=fake_embedder())

    assert len(vector) == EMBEDDING_DIMENSIONS


def test_a_vector_of_the_wrong_width_is_refused() -> None:
    # The failure this check exists for: a model swap that changes output
    # width. `EMBEDDING_DIMENSIONS` is written into the index mapping, so a
    # 1536-wide vector is either rejected by OpenSearch at query time or — on a
    # freshly built index — accepted and quietly wrong.
    with pytest.raises(EmbeddingError, match="dimensions"):
        embed_query("fault F0001", embedder=fake_embedder(dimensions=1536))


def test_the_refusal_says_a_re_index_is_needed() -> None:
    # So whoever hits it does not "fix" it by editing the constant, which
    # would invalidate every vector already stored.
    with pytest.raises(EmbeddingError, match="re-index"):
        embed_query("fault F0001", embedder=fake_embedder(dimensions=512))


def test_a_short_vector_is_refused_too() -> None:
    # Not just longer-than-expected: a truncated vector is equally unusable and
    # equally silent.
    with pytest.raises(EmbeddingError, match="dimensions"):
        embed_texts(["a"], input_type="document", embedder=fake_embedder(dimensions=1))


def test_every_vector_in_a_batch_is_checked() -> None:
    # Checking only the first would let one bad vector through in a batch of
    # hundreds, which is exactly how a partial provider failure presents.
    def mixed(texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        del input_type
        return [[0.1] * EMBEDDING_DIMENSIONS if i == 0 else [0.1] * 8 for i in range(len(texts))]

    with pytest.raises(EmbeddingError, match="vector 1"):
        embed_texts(["a", "b"], input_type="document", embedder=mixed)


# --- failure must not degrade into a zero vector -----------------------------


def test_a_provider_failure_raises_rather_than_returning_zeros() -> None:
    # A zero vector is a legal kNN input. Substituting one would turn an
    # outage into confidently wrong retrieval — the same passages every time,
    # chosen arbitrarily, with citations attached.
    with pytest.raises(EmbeddingError, match="embedding failed"):
        embed_query("fault F0001", embedder=fake_embedder(fail=RuntimeError("boom")))


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("boom"), ValueError("bad"), TimeoutError(), MemoryError()],
    ids=["RuntimeError", "ValueError", "TimeoutError", "MemoryError"],
)
def test_any_provider_exception_is_caught(failure: Exception) -> None:
    # Deliberately broad: a vendor client can raise anything, and every one of
    # them means "no usable vector".
    with pytest.raises(EmbeddingError):
        embed_query("q", embedder=fake_embedder(fail=failure))


def test_the_failure_names_the_underlying_cause() -> None:
    # So an outage is diagnosable rather than just "embedding failed".
    with pytest.raises(EmbeddingError, match="TimeoutError"):
        embed_query("q", embedder=fake_embedder(fail=TimeoutError()))


def test_a_mismatched_vector_count_is_refused() -> None:
    # Vectors are matched to texts by position. A provider returning fewer
    # would silently shift every chunk's embedding onto the wrong chunk.
    with pytest.raises(EmbeddingError, match="1 vectors for 3 texts"):
        embed_texts(
            ["a", "b", "c"], input_type="document", embedder=fake_embedder(count_override=1)
        )


# --- document and query encoding are not interchangeable ----------------------


def test_a_query_is_embedded_as_a_query() -> None:
    calls: list[tuple[tuple[str, ...], InputType]] = []
    embed_query("fault F0001", embedder=fake_embedder(record=calls))

    assert calls == [(("fault F0001",), "query")]


def test_documents_are_embedded_as_documents() -> None:
    # Using the query encoding for stored passages costs recall in a way no
    # test would notice: every search still returns something, just worse.
    calls: list[tuple[tuple[str, ...], InputType]] = []
    embed_documents(["passage one", "passage two"], embedder=fake_embedder(record=calls))

    assert calls == [(("passage one", "passage two"), "document")]


def test_the_two_paths_do_not_share_an_input_type() -> None:
    # Pinned because the distinction is invisible at the call site and the
    # cost of getting it wrong is silent.
    query_calls: list[tuple[tuple[str, ...], InputType]] = []
    doc_calls: list[tuple[tuple[str, ...], InputType]] = []
    embed_query("q", embedder=fake_embedder(record=query_calls))
    embed_documents(["d"], embedder=fake_embedder(record=doc_calls))

    assert query_calls[0][1] != doc_calls[0][1]


# --- edges --------------------------------------------------------------------


def test_an_empty_query_is_refused() -> None:
    # There is no meaningful embedding of nothing, and a zero vector would
    # return arbitrary neighbours rather than no results.
    with pytest.raises(EmbeddingError, match="empty query"):
        embed_query("   ", embedder=fake_embedder())


def test_an_empty_batch_short_circuits_without_calling_the_provider() -> None:
    # A crawl that produced no chunks should not bill an API call.
    calls: list[tuple[tuple[str, ...], InputType]] = []
    result = embed_texts([], input_type="document", embedder=fake_embedder(record=calls))

    assert result == []
    assert calls == []


def test_batch_order_is_preserved() -> None:
    # Vectors are matched to chunks by position, so a reordering provider
    # would attach every embedding to the wrong passage.
    def indexed(texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        del input_type
        return [[float(i)] * EMBEDDING_DIMENSIONS for i in range(len(texts))]

    vectors = embed_texts(["a", "b", "c"], input_type="document", embedder=indexed)

    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
