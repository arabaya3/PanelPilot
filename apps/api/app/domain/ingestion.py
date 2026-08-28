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

import structlog
from sqlalchemy.orm import Session

from app.ai.retrieval.client import stage_chunk
from app.ai.retrieval.embedding import embed_documents
from app.ai.retrieval.mappings import INDEXED_FIELDS
from app.core.errors import AuthorizationError, ValidationError
from app.domain.ingestion_wiring import chunk_ids_from_bodies, make_staging_hook
from app.ingestion.crawler import crawl_source
from app.ingestion.sources import crawler_for
from app.ingestion.staging_pipeline import prepare_documents
from app.ingestion.structure import UnreadableDocumentError, extract_structure
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.documents import CrawlResult, SourceDefinition, SourceDocument
from app.models.schemas.ingestion import (
    CrawlJobRequest,
    CrawlJobResponse,
    CrawlJobStatus,
    VerificationQueuePage,
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


def create_crawl_job(
    *,
    session: Session,
    user: CurrentUser,
    request: CrawlJobRequest,
) -> CrawlJobResponse:
    """Queue a crawl of a manufacturer documentation source.

    Args:
        session: Open database session.
        user: The authenticated caller; must hold the ingestion role.
        request: Source identifier, seed URLs, and crawl depth.

    Returns:
        The queued job with its identifier and initial status.

    Raises:
        AuthorizationError: If the caller lacks the ingestion role.
        ValidationError: If the source is not on the allowed-source list, or
            carries no seed URLs.
        RobotsDisallowedError: If robots.txt forbids a URL the crawl needs.
        RobotsUnavailableError: If robots.txt could not be read at all.

    **Runs the crawl inline rather than queueing it.** The name says queue and
    the status field has a ``QUEUED`` member, both of which predate there being
    anything to run; there is no task broker in this deployment, and inventing
    one to satisfy a verb would be a larger change than the crawl itself. The
    job row still records the lifecycle honestly -- ``RUNNING`` while it works,
    then ``SUCCEEDED`` or ``FAILED`` -- so moving this behind a broker later
    changes the caller and not the record.

    **Nothing here can make content live.** Chunks are written to staging and
    queued for human review. Promotion is a separate, reviewer-roled path that
    this function has no way to reach: see ADR 0001, and
    ``test_only_the_promotion_module_writes_production`` which enforces it.
    """
    if not user.has_role(Role.INGESTION):
        raise AuthorizationError(f"{user.email} does not hold the ingestion role")

    # Checked before the job row is written, so a rejected source leaves no
    # trace of an attempt that never ran.
    if crawler_for(request.source_id) is None:
        raise ValidationError(f"source {request.source_id!r} is not on the allow-list")

    if not request.seed_urls:
        # `crawl_source` would raise on this too, but only after the job row
        # exists and the status has moved to RUNNING. Catching it here keeps a
        # configuration mistake from looking like a failed crawl.
        raise ValidationError(f"source {request.source_id!r} has no seed URLs to crawl")

    job = CrawlJobRow(source_id=request.source_id, status=CrawlJobStatus.RUNNING.value)
    session.add(job)
    session.flush()

    try:
        staged_count = _run_crawl_into_staging(
            session=session,
            user=user,
            job=job,
            request=request,
        )
    except Exception:
        # The status is part of the record, so it must survive the failure that
        # set it. `rollback` would discard the job row along with the partial
        # work, leaving no evidence the crawl was attempted -- which is exactly
        # what someone debugging a source that stopped returning documents
        # needs to see.
        session.rollback()
        failed = CrawlJobRow(source_id=request.source_id, status=CrawlJobStatus.FAILED.value)
        session.add(failed)
        session.commit()
        logger.exception("crawl.failed", source_id=request.source_id, job_id=str(failed.id))
        return CrawlJobResponse(id=str(failed.id), status=CrawlJobStatus.FAILED)

    job.status = CrawlJobStatus.SUCCEEDED.value
    session.commit()
    logger.info(
        "crawl.succeeded",
        source_id=request.source_id,
        job_id=str(job.id),
        staged_chunks=staged_count,
    )
    return CrawlJobResponse(id=str(job.id), status=CrawlJobStatus.SUCCEEDED)


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

    staged = _stage_bodies(session=session, user=user, job=job, result=result, bodies=bodies)

    # The AI-013 seam. Called with every chunk this run produced, after they
    # are in the index -- a queue item pointing at a chunk that is not staged
    # yet is an item a reviewer opens to nothing.
    make_staging_hook(session=session)(chunk_ids_from_bodies(bodies))
    return staged


def _stage_bodies(
    *,
    session: Session,
    user: CurrentUser,
    job: CrawlJobRow,
    result: CrawlResult,
    bodies: dict[str, list[dict[str, object]]],
) -> int:
    """Embed and write one run's chunk bodies to the staging index.

    Args:
        session: Open database session. The caller commits.
        user: The ingester of record.
        job: The job row these documents belong to.
        result: The crawl result, for each document's source URL and hash.
        bodies: Chunk bodies keyed by document id.

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

        # One call per document rather than per chunk: the provider bills and
        # rate-limits per request, and a fifty-chunk manual is fifty round
        # trips done the naive way.
        texts = [str(body.get("text", "")) for body in chunks]
        vectors = embed_documents(texts)

        session.add(
            StagedDocumentRow(
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


def list_verification_queue(
    *,
    session: Session,
    user: CurrentUser,
    limit: int,
    cursor: str | None,
) -> VerificationQueuePage:
    """List staged documents awaiting human verification.

    Args:
        session: Open database session.
        user: The authenticated caller; must hold the reviewer role.
        limit: Maximum number of items to return.
        cursor: Opaque pagination cursor from a previous page.

    Returns:
        A page of pending items, newest first.

    Raises:
        AuthorizationError: If the caller lacks the reviewer role.
    """
    raise NotImplementedError
