"""Ingestion service.

Schedules crawl jobs and exposes the human verification queue. Everything this
module writes lands in staging; it has no capability to touch production.

**Why the orchestration lives here and not in app/ingestion/.** A crawl has to
end with chunks written to the staging index and queued for review, and
``app/ingestion/`` is structurally forbidden from reaching any index-capable
symbol -- by name, by module, by attribute, and by raw client call, all
enforced in ``test_architecture.py``. That guard is the reason the crawler
modules are safe to change quickly. So the parts that touch an index sit in
``app/domain/``, and ``app/ingestion/`` keeps producing index-ready bodies that
it cannot itself write anywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.retrieval.client import stage_chunk
from app.ai.retrieval.embedding import embed_documents
from app.ai.retrieval.mappings import INDEXED_FIELDS
from app.core.errors import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.domain.ingestion_wiring import chunk_ids_from_bodies, make_staging_hook
from app.ingestion.crawler import crawl_source
from app.ingestion.known_documents import urls_for
from app.ingestion.sources import crawler_for
from app.ingestion.staging_pipeline import prepare_documents
from app.ingestion.structure import UnreadableDocumentError, extract_structure
from app.ingestion.url_guard import require_source_url
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.documents import CrawlResult, SourceDefinition, SourceDocument
from app.models.schemas.ingestion import (
    CrawlJobRequest,
    CrawlJobResponse,
    CrawlJobStatus,
)
from app.models.schemas.structure import StructureMap
from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow
from app.models.tables.user import User as UserRow

logger = structlog.get_logger(__name__)

#: How many documents one job will fetch unless the caller narrows it. A cap
#: rather than "everything the portal has": an unbounded first run against a
#: manufacturer library would fetch thousands of PDFs, embed every chunk, and
#: present a review queue nobody can clear -- and it is the kind of mistake
#: that is only visible after the bill.
DEFAULT_MAX_DOCUMENTS = 25

#: Most chunk texts sent to the embedding provider in one request. Providers
#: cap both the number of inputs and the total tokens per call; 128 chunks of
#: this pipeline's size stays well inside both, while still turning a manual's
#: worth of chunks into a handful of requests rather than hundreds.
EMBEDDING_BATCH_SIZE = 128


def create_crawl_job(
    *,
    session: Session,
    user: CurrentUser,
    request: CrawlJobRequest,
) -> CrawlJobResponse:
    """Queue a crawl of a manufacturer documentation source for the worker.

    Args:
        session: Open database session. The caller commits.
        user: The authenticated caller; must hold the ingestion role.
        request: Source identifier, seed URLs, and crawl depth.

    Returns:
        The queued job, with status ``QUEUED``.

    Raises:
        AuthorizationError: If the caller lacks the ingestion role.
        ValidationError: If the source is not on the allowed-source list,
            carries no seed URLs, or names a seed or document URL that is not
            https on the source's own domain.

    **Queues; does not run.** The crawl used to run right here, inside the
    HTTP request: fetching, PDF parsing, embedding and staging, for minutes,
    on a request thread holding a database transaction — the exact load ADR
    0002 keeps off the API runtime. Everything that can be refused is still
    refused here, synchronously, so a bad request is a 4xx and never a failed
    job; the crawl itself happens when the worker picks the job up
    (``run_next_crawl_job``).

    **Nothing here can make content live.** Chunks are written to staging and
    queued for human review. Promotion is a separate, reviewer-roled path that
    this module has no way to reach: see ADR 0001, and
    ``test_only_the_promotion_module_writes_production`` which enforces it.
    """
    if not user.has_role(Role.INGESTION):
        raise AuthorizationError(f"{user.email} does not hold the ingestion role")

    # Checked before the job row is written, so a rejected source leaves no
    # trace of an attempt that never ran.
    crawler = crawler_for(request.source_id)
    if crawler is None:
        raise ValidationError(f"source {request.source_id!r} is not on the allow-list")

    # A run needs somewhere to start: either listings to discover from, or
    # documents named outright. Curated URLs are the fallback for sources whose
    # discovery is blocked -- see `app.ingestion.known_documents`.
    document_urls = request.document_urls or urls_for(request.source_id)

    if not request.seed_urls and not document_urls:
        # Refused now rather than queued to fail later: a configuration
        # mistake should come back to whoever made it, not look like a crawl
        # that ran and broke.
        raise ValidationError(
            f"source {request.source_id!r} has no seed URLs and no known document URLs to crawl"
        )

    # Every URL the caller named is a request the crawler will make from inside
    # our network. One naming an internal address, or any host that is not the
    # source's own, is refused here -- before a job row exists -- so a probe
    # leaves no run behind and never reaches the network. The crawler checks
    # again, and additionally checks where each host resolves, per request.
    for url in (*request.seed_urls, *document_urls):
        require_source_url(url, host_suffix=crawler.host_suffix)

    job = CrawlJobRow(
        source_id=request.source_id,
        status=CrawlJobStatus.QUEUED.value,
        request=request.model_dump(mode="json"),
        requested_by=user.id,
    )
    session.add(job)
    session.flush()
    logger.info("crawl.queued", source_id=request.source_id, job_id=str(job.id))
    return _job_response(job)


def get_crawl_job(*, session: Session, user: CurrentUser, job_id: str) -> CrawlJobResponse:
    """Report where a queued crawl has got to.

    Args:
        session: Open database session.
        user: The authenticated caller; must hold the ingestion role.
        job_id: The job, as ``create_crawl_job`` returned it.

    Returns:
        Its status, and why it failed if it did.

    Raises:
        AuthorizationError: If the caller lacks the ingestion role.
        NotFoundError: If there is no such job.
    """
    if not user.has_role(Role.INGESTION):
        raise AuthorizationError(f"{user.email} does not hold the ingestion role")
    try:
        job = session.get(CrawlJobRow, uuid.UUID(job_id))
    except ValueError:
        job = None
    if job is None:
        raise NotFoundError(f"no crawl job {job_id!r}")
    return _job_response(job)


#: A running job whose worker has not finished it in this long is presumed
#: dead. Comfortably past the longest a crawl can take — the per-run fetch
#: budget times the per-document deadline — so a slow crawl is never failed
#: while it is still working.
ABANDONED_AFTER = timedelta(hours=6)


def run_next_crawl_job(*, session: Session, now: datetime | None = None) -> CrawlJobResponse | None:
    """Claim the oldest queued crawl and run it to completion.

    One job per call, and so per worker process, like every worker job: the
    scheduler decides how often and how many at once.

    Args:
        session: A session able to see every tenant's accounts (the worker's
            is cross-tenant). Committed here: the claim must be visible to
            other workers before the slow part starts.
        now: Current time; injected for tests.

    Returns:
        The finished job, or ``None`` if nothing was queued.

    **Claimed with ``FOR UPDATE SKIP LOCKED``,** so two workers started at
    once take two different jobs instead of both running the first. The claim
    is committed as ``RUNNING`` before the crawl starts, which is also what
    lets a job whose worker died be found afterwards: it stays ``RUNNING``
    past ``ABANDONED_AFTER``, and the next call marks it failed rather than
    leaving it looking busy forever.
    """
    moment = now or datetime.now(UTC)
    _fail_abandoned_jobs(session=session, now=moment)

    job = session.execute(
        select(CrawlJobRow)
        .where(CrawlJobRow.status == CrawlJobStatus.QUEUED.value)
        .order_by(CrawlJobRow.created_at, CrawlJobRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        session.commit()
        return None
    return _run_claimed_job(session=session, job=job, now=moment)


def run_crawl_job(*, session: Session, job_id: str) -> CrawlJobResponse:
    """Run one specific queued job now, as the ``crawl`` command does.

    Args:
        session: A cross-tenant session, as ``run_next_crawl_job``'s.
        job_id: A job in ``QUEUED``.

    Returns:
        The finished job.

    Raises:
        NotFoundError: If the job does not exist or is no longer queued —
            another worker got to it first.
    """
    job = session.execute(
        select(CrawlJobRow)
        .where(
            CrawlJobRow.id == uuid.UUID(job_id),
            CrawlJobRow.status == CrawlJobStatus.QUEUED.value,
        )
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError(f"no queued crawl job {job_id!r}")
    return _run_claimed_job(session=session, job=job, now=datetime.now(UTC))


def _fail_abandoned_jobs(*, session: Session, now: datetime) -> None:
    """Mark running jobs whose worker evidently died as failed.

    Args:
        session: Open database session.
        now: Current time.
    """
    abandoned = session.execute(
        update(CrawlJobRow)
        .where(
            CrawlJobRow.status == CrawlJobStatus.RUNNING.value,
            CrawlJobRow.started_at < now - ABANDONED_AFTER,
        )
        .values(
            status=CrawlJobStatus.FAILED.value,
            error="abandoned: the worker running it stopped without finishing",
            finished_at=now,
        )
    )
    count = getattr(abandoned, "rowcount", 0)
    if count:
        logger.warning("crawl.abandoned_jobs_failed", count=count)


def _run_claimed_job(*, session: Session, job: CrawlJobRow, now: datetime) -> CrawlJobResponse:
    """Run a job this session holds locked, recording how it ended.

    Args:
        session: Open database session holding the job's row lock.
        job: The claimed job.
        now: When it was claimed.

    Returns:
        The finished job.
    """
    job.status = CrawlJobStatus.RUNNING.value
    job.started_at = now
    session.commit()

    request = CrawlJobRequest.model_validate(job.request or {"source_id": job.source_id})
    ingester = CurrentUser(
        id=job.requested_by or "",
        email="",
        tenant_id="",
        roles=frozenset({Role.INGESTION}),
    )
    try:
        staged_count = _run_crawl_into_staging(
            session=session, user=ingester, job=job, request=request
        )
    except Exception as exc:
        # The partial work is rolled back and the job row kept: its status is
        # the record someone debugging a source that stopped returning
        # documents needs, and now it can also say why.
        session.rollback()
        job.status = CrawlJobStatus.FAILED.value
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.finished_at = datetime.now(UTC)
        session.commit()
        logger.exception("crawl.failed", source_id=job.source_id, job_id=str(job.id))
        return _job_response(job)

    job.status = CrawlJobStatus.SUCCEEDED.value
    job.finished_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "crawl.succeeded",
        source_id=job.source_id,
        job_id=str(job.id),
        staged_chunks=staged_count,
    )
    return _job_response(job)


def _job_response(job: CrawlJobRow) -> CrawlJobResponse:
    """Render a job row as the API returns it."""
    return CrawlJobResponse(id=str(job.id), status=CrawlJobStatus(job.status), error=job.error)


def _run_crawl_into_staging(
    *,
    session: Session,
    user: CurrentUser,
    job: CrawlJobRow,
    request: CrawlJobRequest,
) -> int:
    """Fetch, parse, embed, stage and queue one source's documents.

    Args:
        session: Open database session. The caller commits.
        user: The ingester of record, recorded on every staged chunk.
        job: The job row this run belongs to.
        request: Source identifier, seed URLs, and crawl depth.

    Returns:
        How many chunks were written to staging.

    Raises:
        EmbeddingError: If the embedding provider fails. Deliberately fatal
            rather than staging vectorless chunks: a chunk with no
            ``content_vector`` is invisible to the dense leg of hybrid search,
            so it would sit in the index looking ingested while being
            unfindable by exactly the queries it was crawled to answer.
    """
    source = SourceDefinition(
        id=request.source_id,
        manufacturer=crawler_for(request.source_id).manufacturer,  # type: ignore[union-attr]
        seed_urls=request.seed_urls,
        # Falls back to the curated list when the caller named none, so a
        # scheduled `crawl abb` picks up the known documents without anyone
        # having to paste URLs into a cron entry.
        document_urls=request.document_urls or urls_for(request.source_id),
        max_depth=request.max_depth,
    )

    # Hashes already staged, so a re-crawl of unchanged content stages nothing
    # and queues nothing. Without this every run would re-present the same
    # documents to reviewers who have already cleared them.
    known = {
        row.content_hash
        for row in session.query(StagedDocumentRow.content_hash).distinct()
        if row.content_hash
    }

    # The extractor needs the real file. `SourceDocument.text` is a lossy
    # UTF-8 decode of the PDF -- every binary byte becomes U+FFFD and cannot be
    # recovered -- so the bytes are carried out of the crawl alongside it.
    payloads: dict[str, bytes] = {}
    result = crawl_source(
        source,
        max_documents=DEFAULT_MAX_DOCUMENTS,
        known_hashes=known,
        payloads=payloads,
    )

    def structure_for(document: SourceDocument) -> StructureMap:
        """Extract one crawled document's structure from its original bytes.

        Args:
            document: The crawled document.

        Returns:
            Its structural blocks.

        Raises:
            UnreadableDocumentError: If the bytes are missing or unparseable.
                Missing bytes are treated as unreadable rather than as an empty
                document: an empty `StructureMap` would stage a manual with no
                chunks and report success, which reads afterwards as a document
                that genuinely had nothing in it.
        """
        data = payloads.get(document.id)
        if data is None:
            raise UnreadableDocumentError(f"no fetched bytes for document {document.id!r}")
        return extract_structure(data, document_id=document.id)

    batch, bodies = prepare_documents(
        result,
        extract_structure=structure_for,
        brand=source.manufacturer,
    )

    if batch.failures:
        # Logged rather than raised: one unparseable PDF in a run of twenty is
        # a bad document, not a broken crawl, and failing the whole job would
        # discard nineteen good ones.
        logger.warning(
            "crawl.documents_failed",
            source_id=request.source_id,
            failures=batch.failures,
        )

    staged_documents: dict[str, uuid.UUID] = {}
    staged = _stage_bodies(
        session=session,
        user=user,
        job=job,
        result=result,
        bodies=bodies,
        staged_documents=staged_documents,
    )

    # The AI-013 seam. Called with every chunk this run produced, after they
    # are in the index -- a queue item pointing at a chunk that is not staged
    # yet is an item a reviewer opens to nothing. Each chunk carries its staged
    # document: promotion refuses an item that names none, and without it no
    # crawled passage could ever be published.
    make_staging_hook(session=session)(
        chunk_ids_from_bodies(bodies),
        {
            str(body["chunk_id"]): staged_documents[document_id]
            for document_id, chunks in bodies.items()
            if document_id in staged_documents
            for body in chunks
        },
    )
    return staged


def _stage_bodies(
    *,
    session: Session,
    user: CurrentUser,
    job: CrawlJobRow,
    result: CrawlResult,
    bodies: dict[str, list[dict[str, object]]],
    staged_documents: dict[str, uuid.UUID] | None = None,
) -> int:
    """Embed and write one run's chunk bodies to the staging index.

    Args:
        session: Open database session. The caller commits.
        user: The ingester of record.
        job: The job row these documents belong to.
        result: The crawl result, for each document's source URL and hash.
        bodies: Chunk bodies keyed by document id.
        staged_documents: Filled with each staged document's row id, keyed
            by document id.

    Returns:
        How many chunks were written.

    Raises:
        EmbeddingError: If embedding fails; see ``_run_crawl_into_staging``.
    """
    by_id = {document.id: document for document in result.documents}
    written = 0

    for document_id, chunks in bodies.items():
        if not chunks:
            continue

        document = by_id.get(document_id)
        if document is None:  # pragma: no cover - unreachable by construction
            # `prepare_documents` keys its bodies by the documents it was
            # given, and `by_id` is built from that same list, so this branch
            # cannot be reached today. Mutating it to `continue` survives the
            # suite for exactly that reason -- an equivalent mutant, recorded
            # here rather than papered over with a test that fakes an
            # impossible input.
            #
            # It stays because the alternative to raising is skipping, and a
            # chunk whose source URL cannot be resolved is a citation nobody
            # can check. If the two ever drift apart, this fails loudly instead
            # of staging content that looks verifiable and is not.
            raise ValidationError(f"staged chunk for unknown document {document_id!r}")

        # Batched rather than per chunk: the provider bills and rate-limits per
        # request, and a fifty-chunk manual is fifty round trips done the naive
        # way. But bounded rather than one call per document, because a
        # thousand-page manual is thousands of chunks, and a single request
        # that size exceeds what the provider accepts and fails the whole run.
        # Order is preserved batch by batch, so the zip below still pairs each
        # chunk with its own vector.
        texts = [str(body.get("text", "")) for body in chunks]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            vectors.extend(embed_documents(texts[start : start + EMBEDDING_BATCH_SIZE]))

        staged_id = uuid.uuid4()
        if staged_documents is not None:
            staged_documents[document_id] = staged_id
        session.add(
            StagedDocumentRow(
                id=staged_id,
                crawl_job_id=job.id,
                source_url=document.url,
                content_hash=document.content_hash,
                # Only when the ingester is a real `users` row. The column is a
                # nullable FK with ON DELETE SET NULL, so a null here is a state
                # the schema already expects -- and the worker's system actor is
                # a fixed synthetic principal with no row, by design, since a
                # scheduled crawl must not depend on someone having created an
                # account first. The authoritative ingester is on the chunk body
                # (`ingested_by`), which is what promotion's four-eyes rule
                # actually reads; this column is a convenience join.
                ingested_by_id=_known_user_id(session=session, user=user),
            )
        )

        for body, vector in zip(chunks, vectors, strict=True):
            staged_body = dict(body)
            staged_body["content_vector"] = vector
            # The pipeline names the chunk text `text`; the index mapping calls
            # it `content` and additionally requires `content_hash`. Translated
            # here rather than in `chunk_body`, because `app/ingestion/` is
            # deliberately blind to the index schema -- it cannot import the
            # mapping without gaining the capability the architecture tests
            # exist to deny it. Renamed rather than duplicated so a chunk
            # carries one copy of its text.
            staged_body["content"] = staged_body.pop("text", "")
            # The DOCUMENT's hash, so promotion can tell whether live content
            # changed underneath an existing citation (BE-004). Per document
            # rather than per chunk: re-crawling a manual whose text shifted by
            # one line must invalidate its chunks together, not leave some
            # promotable and some not.
            staged_body["content_hash"] = document.content_hash
            # The index mapping is `dynamic: strict`, so anything it does not
            # declare is rejected outright rather than stored and ignored.
            # `chunk_id` is the OpenSearch `_id` and `document_id`/`is_atomic`
            # are pipeline bookkeeping; keeping them here would fail every
            # write. Filtered against the mapping rather than a hand-listed
            # set, so a field added to either side cannot drift out of step.
            staged_body = {
                key: value for key, value in staged_body.items() if key in INDEXED_FIELDS
            }
            # The four-eyes rule reads this at promotion time: whoever brought
            # the content in cannot also bless it. Recorded here because this
            # is the only moment the ingester is known.
            staged_body["ingested_by"] = user.id
            stage_chunk(chunk_id=str(body["chunk_id"]), document=staged_body)
            written += 1

    return written


def _known_user_id(*, session: Session, user: CurrentUser) -> uuid.UUID | None:
    """Return the ingester's id if it names a real user row, else ``None``.

    Args:
        session: Open database session.
        user: The authenticated caller.

    Returns:
        The user id as a UUID, or ``None`` when no such user exists.

    Checked rather than assumed. ``staged_documents.ingested_by_id`` is a
    foreign key into ``users``, and the worker's system actor is deliberately
    not a row there -- an unattended job that required an account to exist
    first would fail at 3am for a reason nobody would guess. Inserting the id
    blindly raises a ForeignKeyViolation that aborts the whole crawl after the
    documents have been fetched and embedded, which is a lot of wasted work for
    a column that is a convenience join.
    """
    try:
        identifier = uuid.UUID(user.id)
    except ValueError:
        # Not an error: the id is used as an opaque string on the chunk body,
        # which is the field promotion actually reads.
        return None

    exists = session.query(UserRow.id).filter(UserRow.id == identifier).first()
    return identifier if exists is not None else None
