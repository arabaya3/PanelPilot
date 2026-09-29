"""Tests for `app/domain/staging_maintenance.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Run against a real OpenSearch and Postgres: re-embedding is a scroll and a
rewrite of a real index, and staleness is a join between index documents and
crawl rows. Fakes of either would test a reimplementation of the thing under
test. Skipped locally when either is missing; CI's integration job has both.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.domain import staging_maintenance
from app.models.tables import calculations, diagnostics, escalation, user  # noqa: F401
from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow

_SOURCE = "maintenance-test"


def _services_available() -> bool:
    if not os.environ.get("DATABASE_URL") or not os.environ.get("OPENSEARCH_URL"):
        return False
    try:
        from app.ai.retrieval.client import get_client
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect():
            pass
        return bool(get_client().ping())
    except Exception:
        return False


requires_services = pytest.mark.skipif(
    not _services_available(),
    reason="needs a migrated Postgres and a reachable OpenSearch; CI provides both",
)


@pytest.fixture
def db() -> Iterator[Session]:
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            text(
                "DELETE FROM staged_documents WHERE crawl_job_id IN "
                "(SELECT id FROM crawl_jobs WHERE source_id LIKE :s)"
            ),
            {"s": f"{_SOURCE}%"},
        )
        session.execute(
            text("DELETE FROM crawl_jobs WHERE source_id LIKE :s"), {"s": f"{_SOURCE}%"}
        )
        session.commit()
        session.close()


@pytest.fixture
def indices() -> Iterator[tuple[str, str]]:
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client

    staging = ensure_index(IndexTarget.STAGING, recreate=True)
    production = ensure_index(IndexTarget.PRODUCTION, recreate=True)
    try:
        yield staging, production
    finally:
        get_client().indices.delete(index=staging, ignore=[404])
        get_client().indices.delete(index=production, ignore=[404])


def _crawled(
    db: Session,
    *,
    url: str,
    content_hash: str,
    source: str = _SOURCE,
    at: datetime | None = None,
) -> None:
    """Record one crawl of a document, as `_stage_bodies` does."""
    job = CrawlJobRow(source_id=source, status="succeeded")
    db.add(job)
    db.flush()
    row = StagedDocumentRow(crawl_job_id=job.id, source_url=url, content_hash=content_hash)
    if at is not None:
        row.created_at = at
    db.add(row)
    db.commit()


def _chunk(
    content_hash: str, *, content: str = "F0001 OVERCURRENT", vector: float = 0.1
) -> dict[str, Any]:
    return {
        "brand": "ABB",
        "model": "ACS880",
        "doc_type": "manual",
        "page": 3,
        "section": "Faults",
        "source_url": "https://example.invalid/m.pdf#page=3",
        "verification_status": "unverified",
        "content": content,
        "content_vector": [vector] * 1024,
        "content_hash": content_hash,
        "ingested_by": "system",
    }


def _put(index: str, chunk_id: str, body: dict[str, Any]) -> None:
    from app.ai.retrieval.client import get_client

    get_client().index(index=index, id=chunk_id, body=body, refresh=True)


def _get(index: str, chunk_id: str) -> dict[str, Any]:
    from app.ai.retrieval.client import get_client

    return dict(get_client().get(index=index, id=chunk_id)["_source"])


class _Embedder:
    """Records batches and returns a recognisable vector per text."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[0.9] * 1024 for _ in texts]


# --- re-embedding -------------------------------------------------------------


@requires_services
def test_every_staging_chunk_gets_a_new_vector_and_nothing_else_changes(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, _ = indices
    embedder = _Embedder()
    monkeypatch.setattr(staging_maintenance, "embed_documents", embedder)
    original = _chunk("h1")
    _put(staging, "doc#0001-a", original)
    _put(staging, "doc#0002-b", _chunk("h1", content="F0002 OVERVOLTAGE"))

    count = staging_maintenance.reembed_staging(session=db)

    assert count == 2
    rewritten = _get(staging, "doc#0001-a")
    assert rewritten["content_vector"] == [0.9] * 1024
    assert {k: v for k, v in rewritten.items() if k != "content_vector"} == {
        k: v for k, v in original.items() if k != "content_vector"
    }


@requires_services
def test_production_is_never_touched(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live chunks keep their vectors until a reviewer re-promotes them."""
    staging, production = indices
    monkeypatch.setattr(staging_maintenance, "embed_documents", _Embedder())
    _put(staging, "doc#0001-a", _chunk("h1"))
    _put(production, "doc#0001-a", _chunk("h1"))

    staging_maintenance.reembed_staging(session=db)

    assert _get(production, "doc#0001-a")["content_vector"] == [0.1] * 1024


@requires_services
def test_a_source_limits_the_run_to_its_own_documents(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, _ = indices
    monkeypatch.setattr(staging_maintenance, "embed_documents", _Embedder())
    ours, theirs = f"ours-{uuid.uuid4().hex}", f"theirs-{uuid.uuid4().hex}"
    _crawled(db, url="https://a.invalid/1", content_hash=ours)
    _crawled(db, url="https://b.invalid/1", content_hash=theirs, source=f"{_SOURCE}-other")
    _put(staging, "ours#0001-a", _chunk(ours))
    _put(staging, "theirs#0001-a", _chunk(theirs))

    count = staging_maintenance.reembed_staging(session=db, source_id=_SOURCE)

    assert count == 1
    assert _get(staging, "ours#0001-a")["content_vector"] == [0.9] * 1024
    assert _get(staging, "theirs#0001-a")["content_vector"] == [0.1] * 1024


@requires_services
def test_a_source_with_no_documents_re_embeds_nothing(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty filter must not widen to "everything"."""
    staging, _ = indices
    embedder = _Embedder()
    monkeypatch.setattr(staging_maintenance, "embed_documents", embedder)
    _put(staging, "doc#0001-a", _chunk("h1"))

    assert staging_maintenance.reembed_staging(session=db, source_id="never-crawled") == 0
    assert embedder.batches == []


@requires_services
def test_chunks_are_embedded_in_batches(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One request per batch, not per chunk: the provider rate-limits requests."""
    staging, _ = indices
    embedder = _Embedder()
    monkeypatch.setattr(staging_maintenance, "embed_documents", embedder)
    for i in range(5):
        _put(staging, f"doc#{i:04d}-x", _chunk("h1", content=f"F000{i}"))

    staging_maintenance.reembed_staging(session=db, batch_size=2)

    assert sorted(len(batch) for batch in embedder.batches) == [1, 2, 2]


@requires_services
def test_a_provider_failure_stops_the_run_loudly(
    db: Session, indices: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chunk left on the old model's vector is silently mis-ranked."""
    from app.ai.retrieval.embedding import EmbeddingError

    staging, _ = indices

    def _down(_texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(staging_maintenance, "embed_documents", _down)
    _put(staging, "doc#0001-a", _chunk("h1"))

    with pytest.raises(EmbeddingError):
        staging_maintenance.reembed_staging(session=db)


def test_a_non_positive_batch_size_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        list(staging_maintenance._batched(iter([]), 0))


# --- stale live content ---------------------------------------------------------


@requires_services
def test_a_live_chunk_from_a_since_changed_document_is_flagged(
    db: Session, indices: tuple[str, str]
) -> None:
    _, production = indices
    old, new = f"v1-{uuid.uuid4().hex}", f"v2-{uuid.uuid4().hex}"
    then = datetime.now(UTC) - timedelta(days=30)
    _crawled(db, url="https://m.invalid/acs880.pdf", content_hash=old, at=then)
    _crawled(db, url="https://m.invalid/acs880.pdf", content_hash=new)
    _put(production, "acs880#0001-a", _chunk(old))

    stale = staging_maintenance.find_superseded(session=db)

    assert stale == [
        staging_maintenance.SupersededChunk(
            chunk_id="acs880#0001-a",
            source_url="https://m.invalid/acs880.pdf",
            live_hash=old,
            latest_hash=new,
        )
    ]


@requires_services
def test_a_live_chunk_from_the_latest_crawl_is_not_flagged(
    db: Session, indices: tuple[str, str]
) -> None:
    _, production = indices
    old, new = f"v1-{uuid.uuid4().hex}", f"v2-{uuid.uuid4().hex}"
    _crawled(
        db,
        url="https://m.invalid/a.pdf",
        content_hash=old,
        at=datetime.now(UTC) - timedelta(days=1),
    )
    _crawled(db, url="https://m.invalid/a.pdf", content_hash=new)
    _put(production, "a#0001-a", _chunk(new))

    assert staging_maintenance.find_superseded(session=db) == []


@requires_services
def test_a_live_chunk_with_no_crawl_record_is_not_guessed_at(
    db: Session, indices: tuple[str, str]
) -> None:
    _, production = indices
    _put(production, "orphan#0001-a", _chunk(f"unknown-{uuid.uuid4().hex}"))

    assert staging_maintenance.find_superseded(session=db) == []


@requires_services
def test_the_staleness_check_writes_nothing(db: Session, indices: tuple[str, str]) -> None:
    """Flags only: retraction is a reviewed operation, not a scheduled one."""
    _, production = indices
    old, new = f"v1-{uuid.uuid4().hex}", f"v2-{uuid.uuid4().hex}"
    _crawled(
        db,
        url="https://m.invalid/b.pdf",
        content_hash=old,
        at=datetime.now(UTC) - timedelta(days=2),
    )
    _crawled(db, url="https://m.invalid/b.pdf", content_hash=new)
    before = _chunk(old)
    _put(production, "b#0001-a", before)

    staging_maintenance.find_superseded(session=db)

    assert _get(production, "b#0001-a") == before


@requires_services
def test_an_empty_production_index_has_nothing_stale(db: Session, indices: tuple[str, str]) -> None:
    assert staging_maintenance.find_superseded(session=db) == []
