"""Keeping the corpus current: re-embedding staging, and noticing stale sources.

Two scheduled jobs that ADR 0001 relies on and nothing did:

- **Re-embedding staging.** An embedding model change leaves every staged
  vector scored in a space that queries no longer use. ``reindex_staging``
  recomputes them in place from each chunk's own text. Production is not
  touched: its chunks are re-embedded on their way through promotion, the one
  write path into it.
- **Stale sources.** A manufacturer can replace a manual under the same URL,
  or withdraw it. ``expire_stale_sources`` asks each source whether it still
  serves the revision production was verified against and records the answer
  in ``stale_documents``. It flags; it never retracts. Pulling a verified
  answer on a hash change alone would trade a possibly-stale answer for a
  certain refusal, and retraction is a reviewed operation.

Re-chunking is deliberately not here. It needs the original file, which is
not kept -- only the text of each chunk is -- so a chunking change means a
fresh crawl of the source, which stages the new chunks for review like any
other.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval.client import (
    PublishedSource,
    iter_staged_contents,
    published_sources,
    restage_vectors,
)
from app.ai.retrieval.embedding import embed_documents
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.domain import promotion as promotion_domain
from app.domain.ingestion import EMBEDDING_BATCH_SIZE
from app.ingestion.crawler import DocumentCheck, check_documents
from app.ingestion.sources import CRAWLERS, crawler_for
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.ingestion import StaleDocumentRow

logger = structlog.get_logger(__name__)

#: ``stale_documents.status`` values.
OPEN = "open"
DISMISSED = "dismissed"
RETRACTED = "retracted"
CLEARED = "cleared"


def reindex_staging(
    *,
    source_id: str | None = None,
    embed: Callable[[Sequence[str]], list[list[float]]] = embed_documents,
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> int:
    """Re-embed every staged chunk from its own text, in place.

    Args:
        source_id: Only this allow-listed source's chunks; all when ``None``.
        embed: The embedding function; the configured provider by default.
        batch_size: Chunks per embedding request, the same bound a crawl uses.

    Returns:
        How many chunks were re-embedded.

    Raises:
        ValidationError: If ``source_id`` is not on the allow-list.
        EmbeddingError: If the provider fails. Fatal rather than skipped: a
            staging index half on the old model's vectors and half on the new
            one ranks chunks by which batch they were in.
    """
    brand: str | None = None
    if source_id is not None:
        crawler = crawler_for(source_id)
        if crawler is None:
            raise ValidationError(f"source {source_id!r} is not on the allow-list")
        brand = crawler.manufacturer

    total = 0
    for batch in iter_staged_contents(brand=brand, batch_size=batch_size):
        vectors = embed([content for _chunk_id, content in batch])
        total += restage_vectors(
            {chunk_id: vector for (chunk_id, _content), vector in zip(batch, vectors, strict=True)}
        )
        logger.info("reindex_staging.batch", source_id=source_id, reembedded=total)
    return total


@dataclass
class ExpiryReport:
    """What one ``expire_stale_sources`` run found.

    Attributes:
        checked: Live documents whose source answered.
        flagged: Source URLs newly flagged or still stale, with the reason.
        dismissed: Stale source URLs a reviewer has already judged harmless,
            still serving the revision they judged. Not flagged again.
        cleared: Source URLs flagged before that serve a verified revision again.
        unchecked: Source URLs that could not be asked, with why. Not stale --
            a source that is down says nothing about whether it changed.
    """

    checked: int = 0
    flagged: dict[str, str] = field(default_factory=dict)
    dismissed: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    unchecked: dict[str, str] = field(default_factory=dict)


def expire_stale_sources(
    *,
    session: Session,
    check: Callable[[str, Iterable[str]], list[DocumentCheck]] = check_documents,
    sources: Callable[[], list[PublishedSource]] = published_sources,
    now: datetime | None = None,
) -> ExpiryReport:
    """Flag live documents whose upstream source changed or was withdrawn.

    Args:
        session: Open database session. The caller commits.
        check: Fetches URLs again; the crawler's ``check_documents`` by default.
        sources: Lists what production cites; injected by tests.
        now: The run's timestamp; the current time by default.

    Returns:
        What was checked, flagged, cleared, and left unchecked.
    """
    moment = now or datetime.now(UTC)
    report = ExpiryReport()

    by_brand = {crawler.manufacturer: source_id for source_id, crawler in CRAWLERS.items()}
    grouped: dict[str, list[PublishedSource]] = {}
    for published in sources():
        source_id = by_brand.get(published.brand)
        if source_id is None:
            # Live content from a source that is no longer allow-listed cannot
            # be fetched again -- the allow-list is what permits the request.
            report.unchecked[published.source_url] = "not-allow-listed"
            continue
        grouped.setdefault(source_id, []).append(published)

    existing = {
        row.source_url: row
        for row in session.scalars(
            select(StaleDocumentRow).where(
                StaleDocumentRow.source_url.in_(
                    [p.source_url for group in grouped.values() for p in group]
                )
            )
        )
    }

    for source_id, group in grouped.items():
        live = {p.source_url: p.content_hashes for p in group}
        for result in check(source_id, list(live)):
            hashes = live[result.url]
            if result.status == "fetched" and result.content_hash in hashes:
                report.checked += 1
                row = existing.get(result.url)
                if row is not None and row.status != CLEARED:
                    row.status = CLEARED
                    row.last_checked_at = moment
                    report.cleared.append(result.url)
                continue
            if result.status == "fetched":
                reason = "superseded"
            elif result.status == "gone":
                reason = "withdrawn"
            else:
                report.unchecked[result.url] = result.status
                continue

            report.checked += 1
            row = existing.get(result.url)
            if (
                row is not None
                and row.status == DISMISSED
                and (row.reason, row.upstream_hash) == (reason, result.content_hash)
            ):
                # Already read and judged harmless, and nothing has moved
                # since. Flagging it again would make a dismissal last a day.
                row.last_checked_at = moment
                report.dismissed.append(result.url)
                continue
            report.flagged[result.url] = reason
            _flag(
                session,
                row,
                source_id=source_id,
                source_url=result.url,
                reason=reason,
                published_hashes=hashes,
                upstream_hash=result.content_hash,
                moment=moment,
            )

    logger.info(
        "expire_stale_sources.done",
        checked=report.checked,
        flagged=len(report.flagged),
        dismissed=len(report.dismissed),
        cleared=len(report.cleared),
        unchecked=len(report.unchecked),
    )
    return report


def _flag(
    session: Session,
    row: StaleDocumentRow | None,
    *,
    source_id: str,
    source_url: str,
    reason: str,
    published_hashes: frozenset[str],
    upstream_hash: str | None,
    moment: datetime,
) -> None:
    """Record one stale source URL, updating its row if it has one.

    Args:
        session: Open database session.
        row: The URL's existing row, if any.
        source_id: The allow-listed source.
        source_url: The document URL.
        reason: ``superseded`` or ``withdrawn``.
        published_hashes: The hashes production was verified against.
        upstream_hash: What the source serves now; ``None`` when withdrawn.
        moment: The run's timestamp.

    A row that was cleared or dismissed and goes stale again is re-opened with
    a fresh ``first_flagged_at``, and a dismissal's review is dropped: it is a
    new change upstream, not the one that was judged.
    """
    published = ",".join(sorted(published_hashes))
    if row is None:
        session.add(
            StaleDocumentRow(
                source_url=source_url,
                source_id=source_id,
                reason=reason,
                status=OPEN,
                published_hashes=published,
                upstream_hash=upstream_hash,
                first_flagged_at=moment,
                last_checked_at=moment,
            )
        )
        return
    if row.status != OPEN or row.upstream_hash != upstream_hash:
        row.first_flagged_at = moment
    row.status = OPEN
    row.reviewed_by_id = None
    row.reviewed_at = None
    row.review_note = None
    row.reason = reason
    row.published_hashes = published
    row.upstream_hash = upstream_hash
    row.last_checked_at = moment


def list_stale_documents(
    *, session: Session, reviewer: CurrentUser, status: str = OPEN
) -> list[StaleDocumentRow]:
    """Return the stale-document flags in one status, oldest change first.

    Args:
        session: Open database session.
        reviewer: The caller; must hold the reviewer role.
        status: ``open`` (the default), ``dismissed``, ``retracted`` or ``cleared``.

    Returns:
        The rows, ordered by when each change was first seen.

    Raises:
        AuthorizationError: If the caller is not a reviewer. The list says
            which live answers may be out of date, which is a reviewer's call
            to act on, not an engineer's to read.
        ValidationError: If ``status`` is not one of the four.
    """
    _require_reviewer(reviewer)
    if status not in (OPEN, DISMISSED, RETRACTED, CLEARED):
        raise ValidationError(f"unknown status {status!r}")
    return list(
        session.scalars(
            select(StaleDocumentRow)
            .where(StaleDocumentRow.status == status)
            .order_by(StaleDocumentRow.first_flagged_at, StaleDocumentRow.source_url)
        )
    )


def dismiss_stale_document(
    *,
    session: Session,
    reviewer: CurrentUser,
    document_id: uuid.UUID,
    note: str,
    now: datetime | None = None,
) -> StaleDocumentRow:
    """Record a reviewer's judgement that an upstream change is harmless.

    Args:
        session: Open database session. The caller commits.
        reviewer: The caller; must hold the reviewer role.
        document_id: The flag.
        note: Why the change does not matter. Required: a dismissal nobody can
            explain later is indistinguishable from one made to clear a list.
        now: The time of the decision; the current time by default.

    Returns:
        The dismissed row.

    Raises:
        AuthorizationError: If the caller is not a reviewer.
        NotFoundError: If there is no such flag.
        ValidationError: If the note is blank, or the flag is not open.
    """
    _require_reviewer(reviewer)
    if not note.strip():
        raise ValidationError("a dismissal needs a note saying why the change does not matter")
    row = session.get(StaleDocumentRow, document_id, with_for_update=True)
    if row is None:
        raise NotFoundError(f"no stale-document flag {document_id}")
    if row.status != OPEN:
        raise ValidationError(f"flag {document_id} is {row.status}, not open")
    row.status = DISMISSED
    row.reviewed_by_id = uuid.UUID(reviewer.id) if _is_uuid(reviewer.id) else None
    row.reviewed_at = now or datetime.now(UTC)
    row.review_note = note.strip()
    return row


def retract_stale_document(
    *,
    session: Session,
    reviewer: CurrentUser,
    document_id: uuid.UUID,
    note: str,
    now: datetime | None = None,
) -> StaleDocumentRow:
    """Take a stale document's live passages out of answers, and record it on the flag.

    Args:
        session: Open database session. The caller commits, so the retraction's
            audit row and this flag's new status land together.
        reviewer: The caller; must hold the reviewer role.
        document_id: The flag.
        note: Why. Recorded on the flag and, as the reason, on the retraction.
        now: The time of the decision; the current time by default.

    Returns:
        The flag, now ``retracted``.

    Raises:
        AuthorizationError: If the caller is not a reviewer.
        NotFoundError: If there is no such flag, or nothing live cites it.
        ValidationError: If the note is blank, or the flag was already
            retracted or cleared.
        PromotionError: If the production delete fails; nothing is recorded.
    """
    _require_reviewer(reviewer)
    if not note.strip():
        raise ValidationError("a retraction needs a note saying why the document is withdrawn")
    row = session.get(StaleDocumentRow, document_id, with_for_update=True)
    if row is None:
        raise NotFoundError(f"no stale-document flag {document_id}")
    # A dismissed flag may still be retracted: a reviewer can change their
    # mind, and the safer decision should never be the one that is refused.
    if row.status not in (OPEN, DISMISSED):
        raise ValidationError(f"flag {document_id} is {row.status}; nothing to retract")

    promotion_domain.retract_source(
        session=session, reviewer=reviewer, source_url=row.source_url, reason=note
    )
    row.status = RETRACTED
    row.reviewed_by_id = uuid.UUID(reviewer.id) if _is_uuid(reviewer.id) else None
    row.reviewed_at = now or datetime.now(UTC)
    row.review_note = note.strip()
    return row


def _require_reviewer(user: CurrentUser) -> None:
    """Refuse anyone without the reviewer role.

    Args:
        user: The caller.

    Raises:
        AuthorizationError: If they do not hold it.
    """
    if not user.has_role(Role.REVIEWER):
        raise AuthorizationError(f"{user.email} does not hold the reviewer role")


def _is_uuid(value: str) -> bool:
    """Report whether a subject id names a ``users`` row.

    Args:
        value: The caller's subject id.

    Returns:
        ``True`` for a UUID. A synthetic principal's id is not one, and has no
        row for the foreign key to point at.
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
