"""Tests for `app/domain/ingestion.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This module orchestrates the crawl: fetch, parse, embed, stage, queue. It sits
on the staging/production boundary, so most of what follows is about what it
must NOT do. The components it drives are tested in their own files; what is
tested here is the wiring, and the wiring is where the dangerous mistakes are:

* nothing this path does may make content live, or reach production at all;
* a failure must leave a recorded job rather than a silent gap;
* a re-crawl of unchanged content must stage nothing, or reviewers re-clear
  work they already cleared;
* every staged chunk must carry an ingester, because promotion's four-eyes
  rule reads that field and an absent one would let the crawler's own
  principal approve its content.

No network and no OpenSearch: the crawl client is a `MockTransport`, and the
staging write and embedder are injected at their seams.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, ValidationError
from app.domain import ingestion as ingestion_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.ingestion import CrawlJobRequest, CrawlJobStatus
from app.models.schemas.structure import BlockKind, StructuralBlock, StructureMap

_INGESTER_ID = "11111111-1111-1111-1111-111111111111"
_TENANT_ID = "22222222-2222-2222-2222-222222222222"

_LISTING = "https://library.abb.com/listing"
_PDF = "https://library.abb.com/manual.pdf"


def _user(*roles: Role) -> CurrentUser:
    return CurrentUser(
        id=_INGESTER_ID,
        email="ingest@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset(roles or {Role.INGESTION}),
    )


def _routes(pdf_body: bytes = b"%PDF-1.4 fake") -> dict[str, tuple[int, bytes]]:
    """Robots, a listing linking one PDF, and the PDF itself."""
    return {
        "https://library.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        _LISTING: (200, f'<a href="{_PDF}">ACS880 manual</a>'.encode()),
        _PDF: (200, pdf_body),
    }


def _client(routes: dict[str, tuple[int, bytes]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


#: Bytes the extractor stub was handed, so a test can assert the crawl passed
#: the real file rather than a lossy decode of it.
_SEEN_BYTES: list[bytes] = []


def _structure(data: bytes, *, document_id: str = "") -> StructureMap:
    """Stand in for pdfplumber, recording the bytes it was given.

    Matches `extract_structure`'s real signature — bytes, not a document —
    because that is the seam the domain calls. A stub taking a `SourceDocument`
    would pass while the production path handed pdfplumber a mojibake string.
    """
    del document_id
    _SEEN_BYTES.append(data)
    return StructureMap(
        blocks=[
            StructuralBlock(
                kind=BlockKind.PARAGRAPH,
                text="The drive trips on DC bus undervoltage when mains dips.",
                page=214,
                section="6.3 Fault tracing",
            )
        ]
    )


class _Recorder:
    """Captures staged chunks instead of writing to OpenSearch."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.staged: dict[str, dict[str, Any]] = {}
        self._fail_on = fail_on

    def __call__(self, *, chunk_id: str, document: dict[str, Any]) -> None:
        if self._fail_on is not None and chunk_id == self._fail_on:
            raise ValueError(f"refusing to stage {chunk_id!r}")
        self.staged[chunk_id] = document


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routes: dict[str, tuple[int, bytes]] | None = None,
    recorder: _Recorder | None = None,
    embedder: Any = None,
    queued: list[list[str]] | None = None,
) -> _Recorder:
    """Replace every outbound seam with an in-memory double."""
    recorder = recorder or _Recorder()
    client = _client(routes if routes is not None else _routes())

    monkeypatch.setattr(ingestion_domain, "stage_chunk", recorder)
    monkeypatch.setattr(ingestion_domain, "extract_structure", _structure)
    monkeypatch.setattr(
        ingestion_domain,
        "embed_documents",
        embedder or _fake_embedder,
    )

    # Bound to the module's ORIGINAL function, not to whatever is currently
    # patched in. Reading it back would double-wrap on a second `_wire` call --
    # which the re-crawl tests do -- and pass `client` twice.
    from app.ingestion.crawler import crawl_source as real_crawl

    monkeypatch.setattr(
        ingestion_domain,
        "crawl_source",
        lambda source, **kw: real_crawl(source, client=client, sleep=lambda _s: None, **kw),
    )

    if queued is not None:
        monkeypatch.setattr(
            ingestion_domain,
            "make_staging_hook",
            lambda **_kw: _capture_into(queued),
        )
    return recorder


def _fake_embedder(texts: Any) -> list[list[float]]:
    """One correctly-sized vector per text, without a provider call."""
    return [[0.1] * 1024 for _ in texts]


def _boom(_texts: Any) -> list[list[float]]:
    """An embedder that fails, standing in for a provider outage."""
    raise RuntimeError("provider down")


def _capture_into(sink: list[list[str]]) -> Any:
    """A staging hook that records the chunk ids it was handed."""

    def hook(chunk_ids: Any) -> None:
        sink.append(list(chunk_ids))

    return hook


def _request(**overrides: Any) -> CrawlJobRequest:
    payload: dict[str, Any] = {"source_id": "abb", "seed_urls": [_LISTING], "max_depth": 1}
    payload.update(overrides)
    return CrawlJobRequest(**payload)


# --- a real database ----------------------------------------------------------
#
# The same arrangement `test_diagnostics.py` uses: these rows carry foreign keys
# and a unique constraint on `content_hash`, and change detection is one of the
# properties under test — none of which a fake session can enforce.


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A real session, cleaned of anything this module created."""
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        # Ordered by dependency: verification items reference chunks staged by
        # these jobs, and staged documents reference the jobs themselves.
        session.execute(
            text(
                "DELETE FROM verification_items WHERE staged_document_id IN "
                "(SELECT id FROM staged_documents WHERE crawl_job_id IN "
                "(SELECT id FROM crawl_jobs WHERE source_id = :s))"
            ),
            {"s": "abb"},
        )
        session.execute(
            text(
                "DELETE FROM staged_documents WHERE crawl_job_id IN "
                "(SELECT id FROM crawl_jobs WHERE source_id = :s)"
            ),
            {"s": "abb"},
        )
        session.execute(text("DELETE FROM crawl_jobs WHERE source_id = :s"), {"s": "abb"})
        session.commit()
        session.close()


# --- authorisation and the allow-list ----------------------------------------


@requires_db
def test_a_caller_without_the_ingestion_role_is_refused(db_session: Session) -> None:
    with pytest.raises(AuthorizationError):
        ingestion_domain.create_crawl_job(
            session=db_session, user=_user(Role.REVIEWER), request=_request()
        )


@requires_db
def test_a_source_not_on_the_allow_list_is_refused(db_session: Session) -> None:
    """The allow-list is the whole point of having one.

    Refused before a job row exists, so a rejected source leaves no trace of an
    attempt that never ran.
    """
    with pytest.raises(ValidationError, match="allow-list"):
        ingestion_domain.create_crawl_job(
            session=db_session, user=_user(), request=_request(source_id="rittal")
        )


@requires_db
def test_a_source_with_no_seed_urls_is_refused(db_session: Session) -> None:
    """A configuration mistake, not a failed crawl.

    `crawl_source` would raise on this too, but only after the job had been
    recorded as RUNNING — which reads afterwards like the source went down.
    """
    with pytest.raises(ValidationError, match="seed URL"):
        ingestion_domain.create_crawl_job(
            session=db_session, user=_user(), request=_request(seed_urls=[])
        )


# --- the happy path ----------------------------------------------------------


@requires_db
def test_a_crawl_stages_chunks(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _wire(monkeypatch)

    response = ingestion_domain.create_crawl_job(
        session=db_session, user=_user(), request=_request()
    )

    assert response.status is CrawlJobStatus.SUCCEEDED
    assert recorder.staged, "the crawl staged nothing"


@requires_db
def test_every_staged_chunk_carries_its_embedding(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chunk with no vector is invisible to the dense leg of hybrid search.

    It would sit in the index looking ingested while being unfindable by
    exactly the queries it was crawled to answer.
    """
    recorder = _wire(monkeypatch)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    for body in recorder.staged.values():
        assert len(body["content_vector"]) == 1024


@requires_db
def test_every_staged_chunk_names_its_ingester(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion's four-eyes rule reads this field.

    An absent ingester means the check `staged["ingested_by"] == reviewer.id`
    is falsy for everyone, so the crawler's own principal could approve the
    content it just brought in.
    """
    recorder = _wire(monkeypatch)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    for body in recorder.staged.values():
        assert body["ingested_by"] == _INGESTER_ID


@requires_db
def test_staged_chunks_are_pending_verification(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing this path writes may be retrievable by answer generation."""
    recorder = _wire(monkeypatch)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    for body in recorder.staged.values():
        assert body["verification_status"] == "pending"


@requires_db
def test_the_run_queues_its_chunks_for_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AI-013 seam.

    The AI-013 seam. Chunks staged but never queued are content no verifier
    is shown, indistinguishable afterwards from content that was reviewed.
    """
    queued: list[list[str]] = []
    recorder = _wire(monkeypatch, queued=queued)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert queued, "nothing was queued for verification"
    assert set(queued[0]) == set(recorder.staged)


@requires_db
def test_the_job_row_records_success(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.tables.ingestion import CrawlJobRow

    _wire(monkeypatch)
    response = ingestion_domain.create_crawl_job(
        session=db_session, user=_user(), request=_request()
    )

    row = db_session.get(CrawlJobRow, uuid.UUID(response.id))
    assert row is not None
    assert row.status == CrawlJobStatus.SUCCEEDED.value


@requires_db
def test_a_staged_document_row_is_written(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So a re-crawl can tell what it has already seen."""
    from app.models.tables.ingestion import StagedDocumentRow

    _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    rows = db_session.query(StagedDocumentRow).all()
    assert len(rows) == 1
    assert rows[0].source_url == _PDF
    assert rows[0].content_hash


# --- production must stay untouched -------------------------------------------


@requires_db
def test_nothing_in_this_path_can_reach_production(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant ADR 0001 exists for, asserted behaviourally.

    `test_architecture.py` proves it structurally by refusing the spelling;
    this proves the running code never calls the production write helper even
    once, which is the property the structural test is a proxy for.
    """
    from app.ai.retrieval import client as retrieval_client

    calls: list[str] = []
    monkeypatch.setattr(
        retrieval_client,
        "index_chunk",
        lambda target, **_kw: calls.append(str(target)),
    )
    _wire(monkeypatch)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert calls == [], "the crawl path called the production write helper"


# --- failure handling ----------------------------------------------------------


@requires_db
def test_a_failed_crawl_is_recorded_rather_than_lost(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone debugging a source that stopped returning documents needs to see.

    Someone debugging a source that stopped returning documents needs to see
    that a run was attempted and failed.
    """
    from app.models.tables.ingestion import CrawlJobRow

    _wire(monkeypatch, recorder=_Recorder(fail_on="never"))
    monkeypatch.setattr(
        ingestion_domain,
        "embed_documents",
        _boom,
    )

    response = ingestion_domain.create_crawl_job(
        session=db_session, user=_user(), request=_request()
    )

    assert response.status is CrawlJobStatus.FAILED
    row = db_session.get(CrawlJobRow, uuid.UUID(response.id))
    assert row is not None
    assert row.status == CrawlJobStatus.FAILED.value


@requires_db
def test_a_failed_crawl_stages_nothing_partial(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-staged document in the review queue looks finished.

    A half-staged document in the review queue looks finished, and the
    missing half is invisible precisely because it is missing.
    """
    from app.models.tables.ingestion import StagedDocumentRow

    _wire(monkeypatch)
    monkeypatch.setattr(
        ingestion_domain,
        "embed_documents",
        _boom,
    )

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert db_session.query(StagedDocumentRow).all() == []


@requires_db
def test_an_embedding_failure_does_not_stage_vectorless_chunks(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately fatal rather than degrading.

    Staging without a vector would put content in the index that the dense leg
    can never return — ingested, unfindable, and indistinguishable from
    content that simply does not match.
    """
    recorder = _wire(monkeypatch)
    monkeypatch.setattr(
        ingestion_domain,
        "embed_documents",
        _boom,
    )

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert recorder.staged == {}


# --- re-crawling ----------------------------------------------------------------


@requires_db
def test_a_recrawl_of_unchanged_content_stages_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise every run re-presents documents reviewers have already.

    Otherwise every run re-presents documents reviewers have already
    cleared, and the queue never empties.
    """
    _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    second = _Recorder()
    _wire(monkeypatch, recorder=second)
    response = ingestion_domain.create_crawl_job(
        session=db_session, user=_user(), request=_request()
    )

    assert response.status is CrawlJobStatus.SUCCEEDED
    assert second.staged == {}, "a re-crawl re-staged unchanged content"


@requires_db
def test_changed_content_is_staged_again(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: change detection must not suppress a real revision."""
    _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    second = _Recorder()
    _wire(monkeypatch, routes=_routes(b"%PDF-1.4 revised"), recorder=second)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert second.staged, "a revised document was not re-staged"


@requires_db
def test_a_run_is_capped_at_a_document_limit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbounded first run against a manufacturer library would fetch.

    An unbounded first run against a manufacturer library would fetch
    thousands of PDFs, embed every chunk, and present a queue nobody can clear.

    Asserted on the argument actually passed rather than by serving a thousand
    documents: a run-size assertion over a handful of fixtures holds whether or
    not the cap exists, which is how the missing cap survived mutation.
    """
    seen: dict[str, Any] = {}
    from app.ingestion.crawler import crawl_source as real_crawl

    def capture(source: Any, **kw: Any) -> Any:
        seen.update(kw)
        return real_crawl(source, client=_client(_routes()), sleep=lambda _s: None, **kw)

    _wire(monkeypatch)
    monkeypatch.setattr(ingestion_domain, "crawl_source", capture)

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert seen["max_documents"] == ingestion_domain.DEFAULT_MAX_DOCUMENTS
    assert seen["max_documents"] is not None, "an uncapped run can fetch a whole library"


@requires_db
def test_the_extractor_receives_the_original_pdf_bytes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this wiring exists to avoid.

    `SourceDocument.text` is `body.decode("utf-8", errors="replace")`, which
    for a PDF replaces every binary byte with U+FFFD — unrecoverable by
    re-encoding. Handing that to pdfplumber yields "not a readable PDF" on
    every document, so the crawler and the extractor could not be connected at
    all. The real bytes are carried out of the crawl instead.
    """
    # Real PDF header bytes: the 0xE2 0xE3 0xCF 0xD3 marker is not valid UTF-8,
    # which is exactly what a lossy decode destroys.
    payload = bytes([0x25, 0x50, 0x44, 0x46, 0x2D, 0x31, 0x2E, 0x34, 0x0A, 0xE2, 0xE3, 0xCF, 0xD3])
    _SEEN_BYTES.clear()
    _wire(monkeypatch, routes=_routes(payload))

    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert _SEEN_BYTES, "the extractor was never called"
    assert _SEEN_BYTES[0] == payload


# --- the index contract -------------------------------------------------------
#
# Both of these were found by a real crawl against a real OpenSearch, not by the
# tests above: the staging write is injected here, so a body the index would
# reject looks fine to a recorder that accepts anything. They are pinned now
# because the failure mode is a crawl that runs, logs success, and stages
# nothing.


@requires_db
def test_a_staged_body_carries_the_fields_the_index_requires(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every required field the index declares is present.

    The pipeline calls the chunk text `text`; the mapping calls it `content`
    and also requires `content_hash`. A body carrying neither is refused by
    `stage_chunk`, so every document failed to stage.
    """
    from app.ai.retrieval.mappings import missing_required_fields

    recorder = _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert recorder.staged
    for body in recorder.staged.values():
        assert missing_required_fields(body) == []


@requires_db
def test_a_staged_body_carries_no_field_the_index_rejects(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping is `dynamic: strict`.

    `chunk_id` is the OpenSearch `_id` and `document_id`/`is_atomic` are
    pipeline bookkeeping; any of them in the body makes the write fail with
    `strict_dynamic_mapping_exception` rather than being ignored.
    """
    from app.ai.retrieval.mappings import INDEXED_FIELDS

    recorder = _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    assert recorder.staged
    for body in recorder.staged.values():
        assert set(body) <= INDEXED_FIELDS, f"unmapped fields: {set(body) - INDEXED_FIELDS}"


@requires_db
def test_the_chunk_text_reaches_the_index_as_content(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chunk text is renamed, not dropped.

    Renamed, not dropped: a chunk indexed without its text is retrievable by
    nothing and citable as nothing.
    """
    recorder = _wire(monkeypatch)
    ingestion_domain.create_crawl_job(session=db_session, user=_user(), request=_request())

    for body in recorder.staged.values():
        assert "undervoltage" in str(body["content"])
        assert "text" not in body
