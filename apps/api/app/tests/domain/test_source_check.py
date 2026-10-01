"""Tests for `app/domain/source_check.py`.

The judgement is pure and tested directly. The run against the queue needs
Postgres for the queue's conditional updates; the index reads and the
clearance itself are replaced, because what is under test is which items get
cleared, not how a clearance publishes (that is `test_promotion.py`).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, PromotionError
from app.core.tenancy import cross_tenant_info
from app.domain import promotion, source_check, verification_queue
from app.domain.source_check import Verdict, judge, pdfs_by_hash, review_staged, words
from app.domain.verification_queue import QueueError
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.verification import VerificationLabel
from app.models.tables.ingestion import VerificationItemRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

PAGE = "The drive trips on overcurrent when output current exceeds the trip limit."


def _chunk(**fields: Any) -> dict[str, Any]:
    chunk = {
        "page": 3,
        "section": "5 Fault tracing",
        "document_title": "ACS880 firmware manual",
        "source_url": "https://library.abb.com/acs880.pdf",
        "brand": "ABB",
        "content": PAGE,
        "ingested_by": "system",
        "content_hash": "hash-1",
    }
    chunk.update(fields)
    return chunk


# --- judge --------------------------------------------------------------------


def test_a_passage_on_its_cited_page_is_grounded() -> None:
    assert judge(_chunk(), page_words={3: words(PAGE)}, page_count=10) is Verdict.GROUNDED


def test_a_passage_running_onto_the_next_pages_is_grounded() -> None:
    first, rest = PAGE.split(" when ")
    pages = {3: words(first), 5: words(rest)}

    assert judge(_chunk(), page_words=pages, page_count=10) is Verdict.GROUNDED


def test_the_window_ends_two_pages_after_the_cited_one() -> None:
    first, rest = PAGE.split(" when ")
    pages = {3: words(first), 6: words(rest)}

    assert judge(_chunk(), page_words=pages, page_count=10) is not Verdict.GROUNDED


@pytest.mark.parametrize(
    ("page_text", "expected"),
    [
        # 8 of the 10 words: some is missing, enough to point a person at it.
        ("The drive trips on overcurrent when output current exceeds", Verdict.WEAK),
        ("Nothing about this passage appears here", Verdict.UNGROUNDED),
    ],
)
def test_a_passage_not_on_its_page_is_not_cleared(page_text: str, expected: Verdict) -> None:
    assert judge(_chunk(), page_words={3: words(page_text)}, page_count=10) is expected


@pytest.mark.parametrize(
    "fields",
    [
        {"content": "Overcurrent ........ 112\nEarth fault ........ 113\nAppendix"},
        {"section": "Index"},
        {"section": "Contents"},
    ],
)
def test_contents_lists_and_indexes_are_navigation(fields: dict[str, Any]) -> None:
    chunk = _chunk(**fields)
    pages = {3: words(chunk["content"])}

    assert judge(chunk, page_words=pages, page_count=10) is Verdict.NAVIGATION


@pytest.mark.parametrize("name", ["section", "document_title", "source_url", "ingested_by"])
def test_a_missing_citation_field_is_never_cleared(name: str) -> None:
    chunk = _chunk(**{name: ""})

    assert judge(chunk, page_words={3: words(PAGE)}, page_count=10) is Verdict.MISSING_FIELDS


@pytest.mark.parametrize("page", [0, 11])
def test_a_page_the_pdf_does_not_have_is_out_of_range(page: int) -> None:
    chunk = _chunk(page=page)

    assert judge(chunk, page_words={}, page_count=10) in {
        Verdict.PAGE_OUT_OF_RANGE,
        Verdict.MISSING_FIELDS,  # page 0 is no page at all
    }


def test_short_words_are_not_evidence() -> None:
    assert words("A 10 kW drive, of the ACS880 range") == {"drive", "the", "acs880", "range"}


# --- pdfs_by_hash -------------------------------------------------------------


def test_pdfs_are_found_by_content_not_by_name(tmp_path: Path) -> None:
    nested = tmp_path / "abb" / "manuals"
    nested.mkdir(parents=True)
    (nested / "renamed.pdf").write_bytes(b"%PDF-1.4 one")
    (tmp_path / "notes.txt").write_bytes(b"not a pdf")

    found = pdfs_by_hash([tmp_path])

    assert found == {hashlib.sha256(b"%PDF-1.4 one").hexdigest(): nested / "renamed.pdf"}


# --- review_staged ------------------------------------------------------------


def _reviewer(**fields: Any) -> CurrentUser:
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "email": "reviewer@example.com",
        "tenant_id": str(uuid.uuid4()),
        "roles": frozenset({Role.ENGINEER, Role.REVIEWER}),
    }
    values.update(fields)
    return CurrentUser(**values)


def test_only_a_reviewer_may_run_the_check() -> None:
    engineer = _reviewer(roles=frozenset({Role.ENGINEER}))

    with pytest.raises(AuthorizationError):
        review_staged(session=None, reviewer=engineer, pdf_folders=[])  # type: ignore[arg-type]


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM verification_items LIMIT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture
def db() -> Iterator[Session]:
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine, info=cross_tenant_info("tests work the review queue"))()
    tag = uuid.uuid4().hex[:8]
    tenant = TenantRow(slug=f"source-check-{tag}", name="Source check tests")
    session.add(tenant)
    session.flush()
    for name in ("reviewer", "other"):
        user = User(email=f"{name}-{tag}@test.invalid", tenant_id=tenant.id)
        session.add(user)
        session.flush()
        session.info[name] = user.id
    session.commit()
    session.info["tag"] = tag
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            text("DELETE FROM verification_items WHERE chunk_id LIKE :p"), {"p": f"{tag}-%"}
        )
        session.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": tenant.id})
        session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant.id})
        session.commit()
        session.close()
        engine.dispose()


def _reviewer_in(db: Session) -> CurrentUser:
    return _reviewer(id=str(db.info["reviewer"]))


@requires_db
def test_grounded_pending_items_are_cleared_as_the_reviewer(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tag = db.info["tag"]
    reviewer = _reviewer_in(db)
    someone_else = db.info["other"]
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + tag.encode())
    content_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()

    rows = {
        "grounded": VerificationItemRow(chunk_id=f"{tag}-grounded", status="pending"),
        "weak": VerificationItemRow(chunk_id=f"{tag}-weak", status="pending"),
        "taken": VerificationItemRow(
            chunk_id=f"{tag}-taken", status="pending", assigned_to_id=someone_else
        ),
        "done": VerificationItemRow(chunk_id=f"{tag}-done", status="labeled"),
        "orphan": VerificationItemRow(chunk_id=f"{tag}-orphan", status="pending"),
    }
    db.add_all(rows.values())
    db.commit()

    staged = {
        f"{tag}-grounded": _chunk(content_hash=content_hash),
        f"{tag}-weak": _chunk(content_hash=content_hash, content=PAGE + " plus words nowhere"),
        f"{tag}-taken": _chunk(content_hash=content_hash),
        f"{tag}-done": _chunk(content_hash=content_hash),
        f"{tag}-orphan": _chunk(content_hash="no-pdf-for-this"),
    }
    monkeypatch.setattr(
        verification_queue,
        "staged_chunks",
        lambda ids: {i: staged[i] for i in ids if i in staged},
    )
    monkeypatch.setattr(source_check, "page_texts", lambda _path, _pages: ({3: PAGE}, 10))
    cleared: list[tuple[uuid.UUID, VerificationLabel, str]] = []

    def clear(*, session: Session, reviewer: CurrentUser, item_id: uuid.UUID, **kw: Any) -> None:
        cleared.append((item_id, kw["label"], reviewer.email))

    monkeypatch.setattr(promotion, "clear_item", clear)

    report = review_staged(session=db, reviewer=reviewer, pdf_folders=[tmp_path])

    assert report.verdicts == {
        f"{tag}-grounded": Verdict.GROUNDED,
        f"{tag}-weak": Verdict.WEAK,
        f"{tag}-orphan": Verdict.NO_SOURCE_PDF,
    }
    assert cleared == [(rows["grounded"].id, VerificationLabel.CORRECT, "reviewer@example.com")]
    assert report.cleared == 1
    claimed = db.execute(
        select(VerificationItemRow.assigned_to_id).where(
            VerificationItemRow.id == rows["grounded"].id
        )
    ).scalar_one()
    assert claimed == uuid.UUID(reviewer.id)


@requires_db
def test_a_dry_run_clears_nothing_and_a_refusal_is_counted(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tag = db.info["tag"]
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + tag.encode())
    content_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    db.add(VerificationItemRow(chunk_id=f"{tag}-one", status="pending"))
    db.commit()
    monkeypatch.setattr(
        verification_queue,
        "staged_chunks",
        lambda ids: {i: _chunk(content_hash=content_hash) for i in ids if i == f"{tag}-one"},
    )
    monkeypatch.setattr(source_check, "page_texts", lambda _path, _pages: ({3: PAGE}, 10))

    def refuse(**_kw: Any) -> None:
        raise PromotionError("the reviewer staged this document")

    monkeypatch.setattr(promotion, "clear_item", refuse)

    dry = review_staged(session=db, reviewer=_reviewer_in(db), pdf_folders=[tmp_path], dry_run=True)
    assert dry.verdicts == {f"{tag}-one": Verdict.GROUNDED}
    assert dry.cleared == 0

    report = review_staged(session=db, reviewer=_reviewer_in(db), pdf_folders=[tmp_path])
    assert report.cleared == 0
    assert sum(report.failed.values()) == 1
    status = db.execute(
        select(VerificationItemRow.status).where(VerificationItemRow.chunk_id == f"{tag}-one")
    ).scalar_one()
    assert status == "pending"


@requires_db
def test_a_queue_refusal_is_counted_not_raised(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tag = db.info["tag"]
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + tag.encode())
    content_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    db.add(VerificationItemRow(chunk_id=f"{tag}-one", status="pending"))
    db.commit()
    monkeypatch.setattr(
        verification_queue,
        "staged_chunks",
        lambda ids: {i: _chunk(content_hash=content_hash) for i in ids if i == f"{tag}-one"},
    )
    monkeypatch.setattr(source_check, "page_texts", lambda _path, _pages: ({3: PAGE}, 10))

    def refuse(**_kw: Any) -> None:
        raise QueueError("already labelled")

    monkeypatch.setattr(promotion, "clear_item", refuse)

    report = review_staged(session=db, reviewer=_reviewer_in(db), pdf_folders=[tmp_path])

    assert report.failed == {"QueueError: already labelled": 1}


# --- fetch_source_pdf -----------------------------------------------------------


def test_a_crawled_manual_is_fetched_again_and_kept_when_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.ingestion.crawler import DocumentCheck

    body = b"%PDF-1.4 staged revision"
    digest = hashlib.sha256(body).hexdigest()
    asked: list[tuple[str, list[str]]] = []

    def check(source_id: str, urls: list[str]) -> list[DocumentCheck]:
        asked.append((source_id, list(urls)))
        return [DocumentCheck(urls[0], "fetched", digest, body)]

    monkeypatch.setattr(source_check, "check_documents", check)

    path = source_check.fetch_source_pdf(
        source_id="abb", url="https://library.abb.com/a.pdf", content_hash=digest, into=tmp_path
    )

    assert asked == [("abb", ["https://library.abb.com/a.pdf"])]
    assert path == tmp_path / f"{digest}.pdf"
    assert path.read_bytes() == body


@pytest.mark.parametrize(
    "served",
    [
        ("fetched", hashlib.sha256(b"a newer revision").hexdigest(), b"a newer revision"),
        ("disallowed", None, None),
    ],
)
def test_a_changed_or_refused_manual_is_not_kept(
    served: tuple[str, str | None, bytes | None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion.crawler import DocumentCheck

    monkeypatch.setattr(
        source_check, "check_documents", lambda _s, urls: [DocumentCheck(urls[0], *served)]
    )

    path = source_check.fetch_source_pdf(
        source_id="abb",
        url="https://library.abb.com/a.pdf",
        content_hash=hashlib.sha256(b"the staged revision").hexdigest(),
        into=tmp_path,
    )

    assert path is None
    assert not list(tmp_path.iterdir())


@requires_db
def test_only_crawled_manuals_missing_locally_are_fetched(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow

    tag = db.info["tag"]
    crawled, supplied = f"{tag}crawled".ljust(64, "0"), f"{tag}supplied".ljust(64, "0")
    jobs: list[uuid.UUID] = []
    for content_hash, request in (
        (crawled, {"seed_urls": []}),
        (supplied, {"local_folder": "/data/abb"}),
    ):
        job = CrawlJobRow(source_id="abb", status="succeeded", request=request)
        db.add(job)
        db.flush()
        jobs.append(job.id)
        db.add(
            StagedDocumentRow(
                crawl_job_id=job.id,
                source_url=f"https://library.abb.com/{content_hash[:12]}.pdf",
                content_hash=content_hash,
            )
        )
        db.add(VerificationItemRow(chunk_id=f"{tag}-{content_hash[:12]}", status="pending"))
    db.commit()
    staged = {
        f"{tag}-{h[:12]}": _chunk(
            content_hash=h, source_url=f"https://library.abb.com/{h[:12]}.pdf"
        )
        for h in (crawled, supplied)
    }
    monkeypatch.setattr(
        verification_queue,
        "staged_chunks",
        lambda ids: {i: staged[i] for i in ids if i in staged},
    )
    fetched: list[tuple[str, str]] = []

    def fetch(*, source_id: str, url: str, content_hash: str, into: Path) -> Path | None:
        fetched.append((source_id, url))
        return None

    monkeypatch.setattr(source_check, "fetch_source_pdf", fetch)

    try:
        report = review_staged(
            session=db,
            reviewer=_reviewer_in(db),
            pdf_folders=[tmp_path],
            dry_run=True,
            fetch_into=tmp_path / "_fetched",
        )
    finally:
        db.rollback()
        # Their staged documents go with them (ON DELETE CASCADE).
        db.execute(delete(CrawlJobRow).where(CrawlJobRow.id.in_(jobs)))
        db.commit()

    assert fetched == [("abb", f"https://library.abb.com/{crawled[:12]}.pdf")]
    assert set(report.verdicts.values()) == {Verdict.NO_SOURCE_PDF}
