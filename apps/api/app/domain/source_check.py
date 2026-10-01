"""Check staged chunks against their source PDFs, and clear the ones that hold up.

A reviewer's first question about a staged chunk is mechanical: is this text
really on the page it cites? This module asks it of every pending chunk at
once, by reading the cited page of the original PDF:

* every citation field is present;
* the cited page exists in the PDF;
* the chunk is not navigation (a contents list or an index);
* at least ``GROUNDED_COVERAGE`` of its words are on the cited page, or on
  the next ``SPILL_PAGES`` pages, where a chunk runs over a page break.

Chunks that pass are labelled correct **by a named human reviewer**, through
the same clearance path the review console uses (``promotion.clear_item``):
claim, four-eyes, audit row, production write. Everything else stays pending
for a person to read. Nothing here runs on a schedule; the operator who runs
it does so as a reviewer, and the reviewer's name is on every clearance.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, PanelPilotError
from app.domain import promotion
from app.domain import verification_queue as queue_domain
from app.ingestion.crawler import check_documents
from app.ingestion.structure import page_texts
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.verification import VerificationLabel
from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow, VerificationItemRow

logger = structlog.get_logger(__name__)

#: Share of a chunk's words that must be on its cited pages to clear it.
GROUNDED_COVERAGE = 0.9
#: Below this a chunk is reported as ungrounded rather than weak.
WEAK_COVERAGE = 0.6
#: Pages after the cited one a chunk may run onto.
SPILL_PAGES = 2
#: Share of lines ending in dot leaders above which a chunk is a contents list.
NAVIGATION_LEADER_SHARE = 0.3
#: Chunks read from staging per request.
BATCH_SIZE = 500

#: What the clearance records as the reviewer's reasoning.
CLEARANCE_NOTE = (
    "Checked against the source PDF: citation fields present, and the passage's "
    "words are on the cited page."
)

_CITATION_FIELDS = (
    "page",
    "section",
    "document_title",
    "source_url",
    "brand",
    "content",
    "ingested_by",
)
_WORD = re.compile(r"[a-z0-9]{3,}")
_LEADERS = re.compile(r"\.{5,}|…{3,}")
_NAVIGATION_SECTION = re.compile(r"(index|contents|table of contents)")


class Verdict(StrEnum):
    """What the check concluded about one staged chunk."""

    GROUNDED = "grounded"
    WEAK = "weak"
    UNGROUNDED = "ungrounded"
    NAVIGATION = "navigation"
    MISSING_FIELDS = "missing_fields"
    NO_SOURCE_PDF = "no_source_pdf"
    PAGE_OUT_OF_RANGE = "page_out_of_range"


@dataclass
class SourceCheckReport:
    """The outcome of one run.

    Attributes:
        verdicts: Each checked chunk's verdict, by chunk id.
        by_document: Verdict counts per document title.
        cleared: Chunks labelled correct and published.
        failed: Clearance failures, by reason; those items stay pending.
    """

    verdicts: dict[str, Verdict] = field(default_factory=dict)
    by_document: dict[str, Counter[Verdict]] = field(default_factory=lambda: defaultdict(Counter))
    cleared: int = 0
    failed: Counter[str] = field(default_factory=Counter)

    def totals(self) -> Counter[Verdict]:
        """Count the verdicts across every document.

        Returns:
            Chunks per verdict.
        """
        return Counter(self.verdicts.values())


def words(text: str) -> set[str]:
    """Return the distinct words of three or more letters or digits.

    Short tokens are dropped: "a", "of" and "1" are on every page, so they
    would make any chunk look grounded.

    Args:
        text: Any text.

    Returns:
        Its lower-cased words.
    """
    return set(_WORD.findall(text.lower()))


def judge(
    chunk: Mapping[str, Any], *, page_words: Mapping[int, set[str]], page_count: int
) -> Verdict:
    """Decide whether one staged chunk is on the pages it cites.

    Args:
        chunk: The staged chunk's stored fields.
        page_words: Words of the source PDF's pages, by 1-based page number;
            must hold the cited page and the ``SPILL_PAGES`` after it.
        page_count: How many pages the source PDF has.

    Returns:
        The verdict. Only ``Verdict.GROUNDED`` may be cleared.
    """
    if any(not chunk.get(name) for name in _CITATION_FIELDS):
        return Verdict.MISSING_FIELDS
    page = int(chunk["page"])
    if not 1 <= page <= page_count:
        return Verdict.PAGE_OUT_OF_RANGE
    content = str(chunk["content"])
    lines = [line for line in content.splitlines() if line.strip()]
    leaders = sum(1 for line in lines if _LEADERS.search(line))
    if (lines and leaders / len(lines) > NAVIGATION_LEADER_SHARE) or _NAVIGATION_SECTION.match(
        str(chunk["section"]).lower()
    ):
        return Verdict.NAVIGATION
    chunk_words = words(content)
    if not chunk_words:
        return Verdict.UNGROUNDED
    window: set[str] = set()
    for number in range(page, page + SPILL_PAGES + 1):
        window |= page_words.get(number, set())
    coverage = len(chunk_words & window) / len(chunk_words)
    if coverage >= GROUNDED_COVERAGE:
        return Verdict.GROUNDED
    return Verdict.WEAK if coverage >= WEAK_COVERAGE else Verdict.UNGROUNDED


def pdfs_by_hash(folders: Iterable[Path]) -> dict[str, Path]:
    """Index the PDFs under some folders by their SHA-256.

    Staged chunks carry the hash of the file they were cut from, which is
    what ties a chunk to its PDF whatever the file is called locally.

    Args:
        folders: Folders searched recursively.

    Returns:
        Each PDF's path by hash.
    """
    found: dict[str, Path] = {}
    for folder in folders:
        for path in sorted(Path(folder).rglob("*.pdf")):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while block := handle.read(1 << 20):
                    digest.update(block)
            found[digest.hexdigest()] = path
    return found


def fetch_source_pdf(*, source_id: str, url: str, content_hash: str, into: Path) -> Path | None:
    """Fetch a crawled manual again, for checking chunks against it.

    A crawl reads a manual in memory and keeps only its chunks, so on a fresh
    machine the PDF a chunk was cut from is nowhere on disk. It is fetched
    through the crawler -- the source's allow-list, robots rules and pacing
    -- and kept only if it is byte-for-byte the file that was staged: a
    revised upload would have different pages.

    Args:
        source_id: The allow-listed source the manual was crawled from.
        url: The manual's URL.
        content_hash: The hash of the staged file.
        into: Folder the PDF is kept in, as ``<content_hash>.pdf``.

    Returns:
        The kept PDF, or ``None`` if it could not be fetched or has changed.
    """
    (check,) = check_documents(source_id, [url])
    if check.status != "fetched" or check.content_hash != content_hash or check.body is None:
        logger.warning(
            "source_check.source_pdf_unavailable",
            url=url,
            status=check.status,
            changed=check.status == "fetched",
        )
        return None
    into.mkdir(parents=True, exist_ok=True)
    path = into / f"{content_hash}.pdf"
    path.write_bytes(check.body)
    return path


def _crawled_sources(session: Session, hashes: Iterable[str]) -> dict[str, str]:
    """Name the source each crawled manual came from, by content hash.

    Manuals supplied as files (``ingest-files``) are left out: their URL is
    where a person found them, often a product page, not a file to fetch.

    Args:
        session: Open database session.
        hashes: Content hashes of staged manuals.

    Returns:
        The source id of each crawled one.
    """
    rows = session.execute(
        select(StagedDocumentRow.content_hash, CrawlJobRow.source_id, CrawlJobRow.request)
        .join(CrawlJobRow, StagedDocumentRow.crawl_job_id == CrawlJobRow.id)
        .where(StagedDocumentRow.content_hash.in_(list(hashes)))
    ).all()
    return {
        content_hash: source_id
        for content_hash, source_id, request in rows
        if not (request or {}).get("local_folder")
    }


def review_staged(
    *,
    session: Session,
    reviewer: CurrentUser,
    pdf_folders: Sequence[Path],
    dry_run: bool = False,
    fetch_into: Path | None = None,
    progress: Callable[[str, Counter[Verdict]], None] | None = None,
) -> SourceCheckReport:
    """Check every pending staged chunk, and clear the grounded ones as ``reviewer``.

    Works the verification queue as it stands: pending items that are
    unassigned or already the reviewer's. Each grounded item is claimed and
    cleared in its own transaction -- committed here, one clearance at a time,
    because each one writes the production index too, and a run of thousands
    must not leave published chunks behind a rolled-back label. A failed
    clearance (four-eyes, an incomplete citation) is counted and leaves its
    item pending.

    Args:
        session: Open database session, able to see the queue.
        reviewer: Who is clearing; must hold the reviewer role.
        pdf_folders: Where the source PDFs are.
        dry_run: Report verdicts only; clear nothing.
        fetch_into: Where to keep crawled manuals fetched again because no
            folder has them (``fetch_source_pdf``); ``None`` fetches nothing.
        progress: Called with each document's title and verdict counts as it
            is checked.

    Returns:
        The verdicts and what was cleared.

    Raises:
        AuthorizationError: If ``reviewer`` lacks the reviewer role.
    """
    if not reviewer.has_role(Role.REVIEWER):
        raise AuthorizationError(f"{reviewer.email} does not hold the reviewer role")
    reviewer_id = uuid.UUID(reviewer.id)

    items = session.execute(
        select(VerificationItemRow.id, VerificationItemRow.chunk_id).where(
            VerificationItemRow.status == queue_domain.STATUS_PENDING,
            VerificationItemRow.chunk_id.is_not(None),
            VerificationItemRow.assigned_to_id.is_(None)
            | (VerificationItemRow.assigned_to_id == reviewer_id),
        )
    ).all()
    item_of = {str(chunk_id): item_id for item_id, chunk_id in items}

    by_document: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    chunk_ids = list(item_of)
    for start in range(0, len(chunk_ids), BATCH_SIZE):
        for chunk_id, chunk in queue_domain.staged_chunks(
            chunk_ids[start : start + BATCH_SIZE]
        ).items():
            by_document[str(chunk.get("content_hash", ""))][chunk_id] = chunk

    pdfs = pdfs_by_hash(pdf_folders)
    if fetch_into is not None:
        missing = [h for h in by_document if h not in pdfs]
        for content_hash, source_id in _crawled_sources(session, missing).items():
            url = str(next(iter(by_document[content_hash].values())).get("source_url", ""))
            fetched = fetch_source_pdf(
                source_id=source_id, url=url, content_hash=content_hash, into=fetch_into
            )
            if fetched is not None:
                pdfs[content_hash] = fetched
    report = SourceCheckReport()
    for content_hash, chunks in by_document.items():
        title = str(next(iter(chunks.values())).get("document_title", content_hash))
        counts = report.by_document[title]
        page_words: dict[int, set[str]] = {}
        page_count = 0
        if content_hash in pdfs:
            wanted = {
                page
                for chunk in chunks.values()
                for page in range(int(chunk.get("page") or 0), int(chunk.get("page") or 0) + 3)
            }
            texts, page_count = page_texts(pdfs[content_hash], wanted)
            page_words = {number: words(text) for number, text in texts.items()}
        for chunk_id, chunk in chunks.items():
            if content_hash not in pdfs:
                verdict = Verdict.NO_SOURCE_PDF
            else:
                verdict = judge(chunk, page_words=page_words, page_count=page_count)
            report.verdicts[chunk_id] = verdict
            counts[verdict] += 1
        if progress is not None:
            progress(title, counts)

    if dry_run:
        return report
    for chunk_id, verdict in report.verdicts.items():
        if verdict is not Verdict.GROUNDED:
            continue
        item_id = item_of[chunk_id]
        try:
            queue_domain.claim_item(session=session, item_id=item_id, verifier_id=reviewer_id)
            promotion.clear_item(
                session=session,
                reviewer=reviewer,
                item_id=item_id,
                label=VerificationLabel.CORRECT,
                note=CLEARANCE_NOTE,
            )
            session.commit()
            report.cleared += 1
        except (PanelPilotError, queue_domain.QueueError) as exc:
            session.rollback()
            report.failed[f"{type(exc).__name__}: {exc}"[:120]] += 1
    logger.info(
        "source_check.done",
        checked=len(report.verdicts),
        cleared=report.cleared,
        failed=sum(report.failed.values()),
    )
    return report
