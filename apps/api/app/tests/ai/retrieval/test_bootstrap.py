"""Tests for `app/ai/retrieval/bootstrap.py`.

Mirrors the module 1:1.

The bug this module exists to close: `ensure_index` is the only thing that
registers the per-query-type search pipelines, and nothing in production code
called it. A fresh cluster therefore had no indices and no pipelines, and
OpenSearch answered every hybrid query with
`illegal_argument_exception: Pipeline panelpilot-hybrid-symptom_description is
not defined`. So the properties worth pinning are not "it calls a function" but
the two that made the failure hard to see: that *both* index targets are
prepared, and that a cluster which cannot be prepared stops the container
rather than letting it serve traffic that will fail one query at a time.

No network — `ensure_index` is patched at the point of use.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval import bootstrap
from app.ai.retrieval.client import IndexTarget


def _record_into(calls: list[IndexTarget]) -> object:
    """Build a stand-in for `ensure_index` that records its target."""

    def ensure(target: IndexTarget, *, recreate: bool = False) -> str:
        del recreate
        calls.append(target)
        return f"panelpilot-{target.value}"

    return ensure


def bootstrap_client() -> object:
    """The module `bootstrap_opensearch` imports `ensure_index` from.

    Imported inside the function under test, so patching must target the
    defining module rather than a name bound at import time.
    """
    from app.ai.retrieval import client

    return client


def test_every_index_target_is_prepared(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both corpora, not just the one a developer happens to query.

    Staging is where ingestion writes and production is where the API reads;
    preparing only one leaves the other failing exactly as before, at whichever
    later moment someone first touches it.
    """
    calls: list[IndexTarget] = []
    monkeypatch.setattr(bootstrap_client(), "ensure_index", _record_into(calls))

    bootstrap.bootstrap_opensearch()

    assert set(calls) == set(IndexTarget)


def test_the_created_index_names_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the names of the indices that were prepared.

    So the compose command can print what it prepared, and a wrong index
    name is visible in the log rather than discovered at the first query.
    """
    monkeypatch.setattr(bootstrap_client(), "ensure_index", _record_into([]))

    names = bootstrap.bootstrap_opensearch()

    assert sorted(names) == ["panelpilot-production", "panelpilot-staging"]


def test_a_cluster_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliberately not swallowed.

    Returning quietly would leave the API serving traffic against a cluster
    with no pipelines — the original bug, reintroduced behind a step that
    claims to have prevented it.
    """

    def boom(target: IndexTarget, *, recreate: bool = False) -> str:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bootstrap_client(), "ensure_index", boom)

    with pytest.raises(RuntimeError):
        bootstrap.bootstrap_opensearch()


def test_main_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap_client(), "ensure_index", _record_into([]))

    assert bootstrap.main() == 0


def test_main_returns_nonzero_when_the_cluster_cannot_be_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit code is the whole point of the entry point.

    `&&` in the compose command is what stops uvicorn from starting, and it
    reads this. A zero here would start the API against an unprepared cluster.
    """

    def boom(target: IndexTarget, *, recreate: bool = False) -> str:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bootstrap_client(), "ensure_index", boom)

    assert bootstrap.main() == 1


def test_main_reports_the_failure_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report the cause on stderr.

    Whoever runs `docker compose logs` needs the cause, not just a
    container that exited.
    """

    def boom(target: IndexTarget, *, recreate: bool = False) -> str:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bootstrap_client(), "ensure_index", boom)
    bootstrap.main()

    assert "connection refused" in capsys.readouterr().err


def test_running_it_twice_is_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running the bootstrap twice must be a no-op.

    Containers restart. `ensure_index` is idempotent and this must stay so:
    a second run that recreated indices would delete the ingested corpus.
    """
    calls: list[IndexTarget] = []
    monkeypatch.setattr(bootstrap_client(), "ensure_index", _record_into(calls))

    bootstrap.bootstrap_opensearch()
    bootstrap.bootstrap_opensearch()

    assert len(calls) == 2 * len(IndexTarget)


def test_indices_are_never_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Indices must never be recreated here.

    `recreate=True` drops the index. On a bootstrap that runs on every
    container start, that would silently empty production on each restart.
    """
    seen: list[bool] = []

    def ensure(target: IndexTarget, *, recreate: bool = False) -> str:
        seen.append(recreate)
        return target.value

    monkeypatch.setattr(bootstrap_client(), "ensure_index", ensure)

    bootstrap.bootstrap_opensearch()

    assert seen == [False, False]
