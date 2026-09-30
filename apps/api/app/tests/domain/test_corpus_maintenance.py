"""Tests for re-embedding staging and flagging stale sources."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.retrieval.client import PublishedSource
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.domain import corpus_maintenance
from app.domain.corpus_maintenance import (
    backfill_titles,
    dismiss_stale_document,
    expire_stale_sources,
    list_stale_documents,
    reindex_staging,
    retract_stale_document,
)
from app.ingestion.crawler import DocumentCheck
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.base import Base
from app.models.tables.ingestion import StaleDocumentRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

# --- reindex_staging ----------------------------------------------------------


def _fake_staging(
    monkeypatch: pytest.MonkeyPatch, chunks: dict[str, str]
) -> tuple[list[str | None], dict[str, list[float]]]:
    """Replace the staging index with ``chunks``; return the brands asked for and writes."""
    brands: list[str | None] = []
    written: dict[str, list[float]] = {}

    def iterate(*, brand: str | None, batch_size: int) -> Iterator[list[tuple[str, str]]]:
        brands.append(brand)
        items = list(chunks.items())
        for start in range(0, len(items), batch_size):
            yield items[start : start + batch_size]

    def restage(vectors: dict[str, list[float]]) -> int:
        written.update(vectors)
        return len(vectors)

    monkeypatch.setattr(corpus_maintenance, "iter_staged_contents", iterate)
    monkeypatch.setattr(corpus_maintenance, "restage_vectors", restage)
    return brands, written


def _length_embedder(texts: Sequence[str]) -> list[list[float]]:
    """A deterministic embedder: each vector records its text's length."""
    return [[float(len(t))] for t in texts]


def test_reindex_embeds_each_chunk_from_its_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _brands, written = _fake_staging(monkeypatch, {"a": "x", "b": "yyy", "c": "zz"})

    count = reindex_staging(embed=_length_embedder, batch_size=2)

    # Batched two at a time, and each vector still paired with its own chunk.
    assert count == 3
    assert written == {"a": [1.0], "b": [3.0], "c": [2.0]}


def test_reindex_limits_itself_to_one_sources_brand(monkeypatch: pytest.MonkeyPatch) -> None:
    brands, _written = _fake_staging(monkeypatch, {})
    reindex_staging(source_id="schneider", embed=_length_embedder)
    reindex_staging(embed=_length_embedder)
    assert brands == ["Schneider Electric", None]


def test_reindex_refuses_a_source_off_the_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    brands, _written = _fake_staging(monkeypatch, {"a": "x"})
    with pytest.raises(ValidationError, match="allow-list"):
        reindex_staging(source_id="nobody", embed=_length_embedder)
    assert brands == []


def test_a_failed_embedding_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _brands, written = _fake_staging(monkeypatch, {"a": "x"})

    def broken(_texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        reindex_staging(embed=broken)
    assert written == {}


# --- expire_stale_sources -----------------------------------------------------

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: flags are upserted against a unique source URL",
)

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
MANUAL = "https://library.abb.com/acs880.pdf"
GUIDE = "https://library.abb.com/guide.pdf"


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="session")
def _session(engine: Engine) -> Iterator[Session]:
    with sessionmaker(bind=engine)() as session:
        session.execute(text("TRUNCATE stale_documents"))
        session.commit()
        yield session
        session.rollback()


def _live(*sources: tuple[str, str, set[str]]) -> Any:
    """What production cites: ``(url, brand, hashes)`` triples."""
    return lambda: [
        PublishedSource(url, brand, frozenset(hashes)) for url, brand, hashes in sources
    ]


def _upstream(*results: DocumentCheck) -> Any:
    """A checker answering with ``results``, recording what it was asked."""
    by_url = {r.url: r for r in results}
    asked: list[tuple[str, list[str]]] = []

    def check(source_id: str, urls: Iterable[str]) -> list[DocumentCheck]:
        listed = list(urls)
        asked.append((source_id, listed))
        return [by_url[url] for url in listed]

    check.asked = asked  # type: ignore[attr-defined]
    return check


def _rows(session: Session) -> dict[str, StaleDocumentRow]:
    return {row.source_url: row for row in session.scalars(select(StaleDocumentRow))}


@requires_postgres
def test_a_changed_upstream_document_is_flagged_superseded(session: Session) -> None:
    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h2")),
        now=NOW,
    )
    session.commit()

    assert report.flagged == {MANUAL: "superseded"}
    row = _rows(session)[MANUAL]
    assert (row.source_id, row.reason, row.status) == ("abb", "superseded", "open")
    assert (row.published_hashes, row.upstream_hash) == ("h1", "h2")
    assert row.first_flagged_at == NOW


@requires_postgres
def test_a_404_is_withdrawn_and_an_outage_is_not_stale(session: Session) -> None:
    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"}), (GUIDE, "ABB", {"g1"})),
        check=_upstream(DocumentCheck(MANUAL, "gone"), DocumentCheck(GUIDE, "unreachable")),
        now=NOW,
    )
    session.commit()

    assert report.flagged == {MANUAL: "withdrawn"}
    assert report.unchecked == {GUIDE: "unreachable"}
    assert set(_rows(session)) == {MANUAL}
    assert _rows(session)[MANUAL].upstream_hash is None


@requires_postgres
def test_a_document_serving_any_verified_revision_is_current(session: Session) -> None:
    # Two revisions live: the upstream serving either one is not stale.
    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"old", "new"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", "old")),
        now=NOW,
    )
    assert (report.checked, report.flagged) == (1, {})
    assert _rows(session) == {}


@requires_postgres
def test_a_flag_clears_when_the_verified_revision_is_back(session: Session) -> None:
    live = _live((MANUAL, "ABB", {"h1"}))
    expire_stale_sources(
        session=session,
        sources=live,
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h2")),
        now=NOW,
    )
    session.commit()

    report = expire_stale_sources(
        session=session,
        sources=live,
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h1")),
        now=NOW + timedelta(days=1),
    )
    session.commit()

    assert report.cleared == [MANUAL]
    row = _rows(session)[MANUAL]
    assert (row.status, row.last_checked_at) == ("cleared", NOW + timedelta(days=1))


@requires_postgres
def test_a_still_stale_document_keeps_when_it_was_first_flagged(session: Session) -> None:
    live = _live((MANUAL, "ABB", {"h1"}))
    same = _upstream(DocumentCheck(MANUAL, "fetched", "h2"))
    expire_stale_sources(session=session, sources=live, check=same, now=NOW)
    session.commit()
    expire_stale_sources(session=session, sources=live, check=same, now=NOW + timedelta(days=1))
    session.commit()

    row = _rows(session)[MANUAL]
    assert (row.first_flagged_at, row.last_checked_at) == (NOW, NOW + timedelta(days=1))

    # A different upstream revision is a new change, flagged from now.
    expire_stale_sources(
        session=session,
        sources=live,
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h3")),
        now=NOW + timedelta(days=2),
    )
    session.commit()
    session.expire_all()
    assert _rows(session)[MANUAL].first_flagged_at == NOW + timedelta(days=2)


@requires_postgres
def test_each_source_is_asked_about_its_own_documents(session: Session) -> None:
    siemens = "https://support.industry.siemens.com/g120.pdf"
    check = _upstream(
        DocumentCheck(MANUAL, "fetched", "h1"), DocumentCheck(siemens, "fetched", "s1")
    )
    report = expire_stale_sources(
        session=session,
        sources=_live(
            (MANUAL, "ABB", {"h1"}),
            (siemens, "Siemens", {"s1"}),
            ("https://example.com/x.pdf", "Unknown GmbH", {"x"}),
        ),
        check=check,
        now=NOW,
    )

    assert sorted(check.asked) == [("abb", [MANUAL]), ("siemens", [siemens])]
    assert report.unchecked == {"https://example.com/x.pdf": "not-allow-listed"}


# --- against a real index -----------------------------------------------------


def _opensearch_available() -> bool:
    try:
        from app.ai.retrieval.client import get_client

        return bool(get_client().ping())
    except Exception:
        return False


requires_opensearch = pytest.mark.skipif(
    not os.environ.get("OPENSEARCH_URL") or not _opensearch_available(),
    reason="needs a reachable OpenSearch; CI provides one as a service container",
)


def _chunk(*, brand: str, url: str, content_hash: str, content: str) -> dict[str, Any]:
    from app.ai.retrieval.mappings import EMBEDDING_DIMENSIONS

    return {
        "brand": brand,
        "model": "ACS880",
        "doc_type": "manual",
        "page": 1,
        "section": "Faults",
        "source_url": url,
        "verification_status": "unverified",
        "content": content,
        "content_hash": content_hash,
        "ingested_by": "ingester",
        "content_vector": [0.5] * EMBEDDING_DIMENSIONS,
    }


@pytest.fixture
def indices() -> Iterator[tuple[str, str]]:
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client

    client = get_client()
    staging = ensure_index(IndexTarget.STAGING, recreate=True)
    production = ensure_index(IndexTarget.PRODUCTION, recreate=True)
    try:
        yield staging, production
    finally:
        client.indices.delete(index=staging, ignore=[404])
        client.indices.delete(index=production, ignore=[404])


@requires_opensearch
def test_reindex_replaces_only_the_vector_of_one_brands_chunks(
    indices: tuple[str, str],
) -> None:
    from app.ai.retrieval.client import get_client
    from app.ai.retrieval.mappings import EMBEDDING_DIMENSIONS

    staging, production = indices
    client = get_client()
    abb = _chunk(brand="ABB", url=MANUAL, content_hash="h1", content="F0001 overcurrent")
    siemens = _chunk(brand="Siemens", url="https://s/x.pdf", content_hash="s1", content="F30001")
    client.index(index=staging, id="abb-1", body=abb, refresh=True)
    client.index(index=staging, id="siemens-1", body=siemens, refresh=True)
    client.index(index=production, id="abb-1", body=abb, refresh=True)

    def embed(texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]

    assert reindex_staging(source_id="abb", embed=embed) == 1

    restaged = client.get(index=staging, id="abb-1")["_source"]
    assert restaged["content_vector"][0] == 1.0
    # Everything promotion checks is as the crawl wrote it.
    assert {k: v for k, v in restaged.items() if k != "content_vector"} == {
        k: v for k, v in abb.items() if k != "content_vector"
    }
    assert client.get(index=staging, id="siemens-1")["_source"]["content_vector"][0] == 0.5
    # And production was never touched.
    assert client.get(index=production, id="abb-1")["_source"]["content_vector"][0] == 0.5


@requires_opensearch
def test_backfill_names_curated_staged_chunks_and_nothing_else(
    indices: tuple[str, str],
) -> None:
    from app.ai.retrieval.client import get_client
    from app.ingestion.known_documents import KNOWN_DOCUMENTS

    staging, production = indices
    client = get_client()
    curated = KNOWN_DOCUMENTS[0]
    old = _chunk(brand="ABB", url=curated.url, content_hash="h1", content="F0001")
    stranger = _chunk(brand="ABB", url="https://x/not-curated.pdf", content_hash="x", content="x")
    titled = {**old, "document_title": "Already titled"}
    client.index(index=staging, id="old", body=old, refresh=True)
    client.index(index=staging, id="stranger", body=stranger, refresh=True)
    client.index(index=staging, id="titled", body=titled, refresh=True)
    client.index(index=production, id="old", body=old, refresh=True)

    assert backfill_titles() == 1

    assert client.get(index=staging, id="old")["_source"]["document_title"] == curated.title
    assert "document_title" not in client.get(index=staging, id="stranger")["_source"]
    # A title already there is not overwritten.
    assert client.get(index=staging, id="titled")["_source"]["document_title"] == "Already titled"
    # Production changes only through promotion.
    assert "document_title" not in client.get(index=production, id="old")["_source"]


@requires_opensearch
def test_published_sources_lists_each_live_url_with_its_hashes(
    indices: tuple[str, str],
) -> None:
    from app.ai.retrieval.client import get_client, published_sources

    staging, production = indices
    client = get_client()
    for n, (url, content_hash) in enumerate([(MANUAL, "h1"), (MANUAL, "h2"), (GUIDE, "g1")]):
        body = _chunk(brand="ABB", url=url, content_hash=content_hash, content=f"chunk {n}")
        client.index(index=production, id=f"p{n}", body=body, refresh=True)
    # Staged-only content is not live, so it is nobody's business here.
    client.index(
        index=staging,
        id="s0",
        body=_chunk(
            brand="ABB", url="https://library.abb.com/new.pdf", content_hash="n1", content="x"
        ),
        refresh=True,
    )

    assert published_sources(page_size=1) == [
        PublishedSource(MANUAL, "ABB", frozenset({"h1", "h2"})),
        PublishedSource(GUIDE, "ABB", frozenset({"g1"})),
    ]


@requires_opensearch
def test_neither_job_fails_before_the_indices_exist(indices: tuple[str, str]) -> None:
    """A fresh deployment has no index until its first crawl or promotion.

    ``expire-stale-sources`` on a new install answered with a traceback.
    """
    from app.ai.retrieval.client import get_client, iter_staged_contents, published_sources

    client = get_client()
    for name in indices:
        client.indices.delete(index=name)

    assert published_sources() == []
    assert list(iter_staged_contents()) == []


# --- a reviewer's view of the flags -------------------------------------------


def _reviewer_row(session: Session) -> CurrentUser:
    """A real account holding the reviewer role, for the dismissal's foreign key."""
    tenant = TenantRow(slug=f"stale-{uuid.uuid4().hex[:8]}", name="Stale tests")
    session.add(tenant)
    session.flush()
    user = User(tenant_id=tenant.id, email=f"reviewer-{uuid.uuid4().hex[:8]}@test.invalid")
    session.add(user)
    session.flush()
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        tenant_id=str(tenant.id),
        roles=frozenset({Role.ENGINEER, Role.REVIEWER}),
    )


ENGINEER = CurrentUser(
    id=str(uuid.UUID(int=5)),
    email="engineer@test.invalid",
    tenant_id=str(uuid.UUID(int=6)),
    roles=frozenset({Role.ENGINEER}),
)


def _flag_manual(session: Session, upstream: str = "h2") -> StaleDocumentRow:
    expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", upstream)),
        now=NOW,
    )
    session.flush()
    return _rows(session)[MANUAL]


@requires_postgres
def test_a_dismissal_records_who_when_and_why(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)

    dismissed = dismiss_stale_document(
        session=session,
        reviewer=reviewer,
        document_id=flag.id,
        note="  cover page redesigned  ",
        now=NOW + timedelta(hours=1),
    )
    session.commit()

    assert (dismissed.status, dismissed.review_note) == ("dismissed", "cover page redesigned")
    assert (str(dismissed.reviewed_by_id), dismissed.reviewed_at) == (
        reviewer.id,
        NOW + timedelta(hours=1),
    )
    assert list_stale_documents(session=session, reviewer=reviewer) == []
    assert list_stale_documents(session=session, reviewer=reviewer, status="dismissed") == [
        dismissed
    ]


@requires_postgres
def test_a_dismissal_holds_while_the_source_serves_the_same_revision(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="typo")
    session.commit()

    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h2")),
        now=NOW + timedelta(days=1),
    )
    session.commit()

    # Not flagged again, so the job does not exit 1 for a decided change.
    assert (report.flagged, report.dismissed) == ({}, [MANUAL])
    row = _rows(session)[MANUAL]
    assert (row.status, row.last_checked_at) == ("dismissed", NOW + timedelta(days=1))


@requires_postgres
def test_a_further_change_reopens_a_dismissal_and_forgets_its_review(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="typo")
    session.commit()

    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h3")),
        now=NOW + timedelta(days=2),
    )
    session.commit()
    session.expire_all()

    assert report.flagged == {MANUAL: "superseded"}
    row = _rows(session)[MANUAL]
    assert (row.status, row.first_flagged_at) == ("open", NOW + timedelta(days=2))
    assert (row.reviewed_by_id, row.reviewed_at, row.review_note) == (None, None, None)


@requires_postgres
def test_a_withdrawal_reopens_a_dismissed_change(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="typo")
    session.commit()

    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "gone")),
        now=NOW + timedelta(days=3),
    )

    assert report.flagged == {MANUAL: "withdrawn"}
    assert _rows(session)[MANUAL].status == "open"


@requires_postgres
def test_a_dismissed_flag_clears_when_the_verified_revision_returns(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="typo")
    session.commit()

    report = expire_stale_sources(
        session=session,
        sources=_live((MANUAL, "ABB", {"h1"})),
        check=_upstream(DocumentCheck(MANUAL, "fetched", "h1")),
        now=NOW + timedelta(days=1),
    )

    assert report.cleared == [MANUAL]
    assert _rows(session)[MANUAL].status == "cleared"


@requires_postgres
def test_a_dismissal_is_refused_without_a_note_or_twice(session: Session) -> None:
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)

    with pytest.raises(ValidationError, match="note"):
        dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note=" ")
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="ok")
    with pytest.raises(ValidationError, match="dismissed, not open"):
        dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="ok")
    with pytest.raises(NotFoundError):
        dismiss_stale_document(
            session=session, reviewer=reviewer, document_id=uuid.uuid4(), note="ok"
        )


@requires_postgres
def test_the_database_refuses_a_dismissal_without_its_review(session: Session) -> None:
    # The constraint, not just the domain: a dismissal written some other way
    # still has to say who and why.
    flag = _flag_manual(session)
    flag.status = "dismissed"
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_a_reviewer_may_see_or_dismiss_flags() -> None:
    # Refused before any query, so no database is needed to prove it.
    with pytest.raises(AuthorizationError):
        list_stale_documents(session=None, reviewer=ENGINEER)  # type: ignore[arg-type]
    with pytest.raises(AuthorizationError):
        dismiss_stale_document(
            session=None,  # type: ignore[arg-type]
            reviewer=ENGINEER,
            document_id=uuid.uuid4(),
            note="x",
        )


@requires_postgres
def test_an_unknown_status_is_refused(session: Session) -> None:
    with pytest.raises(ValidationError):
        list_stale_documents(session=session, reviewer=_reviewer_row(session), status="bogus")


# --- retracting a flagged document ---------------------------------------------


@requires_opensearch
@requires_postgres
def test_retracting_a_flag_removes_its_live_passages_and_records_both(
    session: Session, indices: tuple[str, str]
) -> None:
    from app.ai.retrieval.client import get_client
    from app.models.tables.ingestion import RetractionAuditRow

    _, production = indices
    client = get_client()
    client.index(
        index=production,
        id="abb-1",
        body=_chunk(brand="ABB", url=MANUAL, content_hash="h1", content="F0001"),
        refresh=True,
    )
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)

    retracted = retract_stale_document(
        session=session, reviewer=reviewer, document_id=flag.id, note=" withdrawn by ABB "
    )
    session.commit()

    assert (retracted.status, retracted.review_note) == ("retracted", "withdrawn by ABB")
    assert not client.exists(index=production, id="abb-1")
    audit = session.scalars(
        select(RetractionAuditRow).where(RetractionAuditRow.source_url == MANUAL)
    ).one()
    assert (audit.chunk_ids, audit.reason) == (["abb-1"], "withdrawn by ABB")
    session.delete(audit)
    session.commit()


@requires_postgres
def test_a_retraction_that_fails_leaves_the_flag_open(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.errors import PromotionError
    from app.domain import promotion

    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    session.commit()

    def refused(**_kwargs: object) -> None:
        raise PromotionError("retraction failed; retry it")

    monkeypatch.setattr(promotion, "retract_source", refused)
    with pytest.raises(PromotionError):
        retract_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="x")
    session.rollback()

    assert _rows(session)[MANUAL].status == "open"


@requires_postgres
def test_a_dismissed_flag_can_still_be_retracted_but_a_closed_one_cannot(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain import promotion

    monkeypatch.setattr(promotion, "retract_source", lambda **_kw: None)
    reviewer = _reviewer_row(session)
    flag = _flag_manual(session)
    dismiss_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="typo")

    # Changing one's mind toward the safer decision is never refused.
    retract_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="wrong")
    assert flag.status == "retracted"
    with pytest.raises(ValidationError, match="nothing to retract"):
        retract_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note="x")
    with pytest.raises(ValidationError, match="note"):
        retract_stale_document(session=session, reviewer=reviewer, document_id=flag.id, note=" ")


def test_only_a_reviewer_may_retract_a_flag() -> None:
    with pytest.raises(AuthorizationError):
        retract_stale_document(
            session=None,  # type: ignore[arg-type]
            reviewer=ENGINEER,
            document_id=uuid.uuid4(),
            note="x",
        )
