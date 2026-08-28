"""Manufacturer documentation crawler.

Fetches and parses source documents. Writes nothing to any index directly — its
output goes to the staging pipeline. See
docs/adr/0001-staging-vs-production-index.md.

The shape of this module is set by two requirements that pull in opposite
directions. It has to keep the knowledge base current without manual
re-uploading, and it has to respect each source's terms while doing it — so
every fetch is gated on robots.txt, rate-limited, and skipped entirely when the
content has not changed since last time.

Change detection is where the acceptance criterion lives: a changed document is
queued within one run, and an unchanged one produces *zero* new staging entries
on a repeat run. That is done by hashing content rather than trusting
timestamps or ETags, because a portal that regenerates its PDFs nightly will
change every header it serves while changing nothing an engineer would read.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable

import httpx
import structlog

from app.core.errors import ValidationError
from app.ingestion import robots as robots_module
from app.ingestion.sources import (
    DiscoveredDocument,
    SourceCrawler,
    crawler_for,
    http_client,
)
from app.models.schemas.documents import (
    CrawlOutcome,
    CrawlResult,
    SourceDefinition,
    SourceDocument,
)

logger = structlog.get_logger(__name__)

#: Our own politeness floor, used when robots.txt asks for nothing slower.
#: One request per second is well below what any of these portals would
#: notice, and the crawl is a scheduled background job with no deadline.
DEFAULT_DELAY_S = 1.0

#: A document larger than this is not read into memory. Manufacturer manuals
#: run to a few tens of megabytes; something far past that is a mistake or a
#: trap, and either way not worth an unbounded read.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024


def content_hash(data: bytes) -> str:
    """Hash a document's bytes.

    Args:
        data: The document as fetched.

    Returns:
        Hex SHA-256, matching the 64-character ``content_hash`` column on
        ``staged_documents``.
    """
    return hashlib.sha256(data).hexdigest()


class _Pacer:
    """Enforces a minimum gap between requests to one source.

    A wall-clock sleep rather than a token bucket: there is exactly one crawl
    in flight per source, so the simple thing is also the correct thing, and a
    bucket would only add state that could be wrong.
    """

    def __init__(self, delay_s: float, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._delay_s = delay_s
        self._sleep = sleep
        self._last: float | None = None

    def wait(self, *, now: Callable[[], float] = time.monotonic) -> None:
        """Block until the next request is due."""
        current = now()
        if self._last is not None:
            elapsed = current - self._last
            if elapsed < self._delay_s:
                self._sleep(self._delay_s - elapsed)
        self._last = now()


def _fetch(client: httpx.Client, url: str) -> bytes | None:
    """Fetch one URL, returning ``None`` when it is not usable.

    Args:
        client: The HTTP client.
        url: Absolute URL to fetch.

    Returns:
        The response body, or ``None`` if the fetch failed or was too large.
    """
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("crawl.fetch_failed", url=url, error=str(exc))
        return None

    if response.status_code >= 400:
        logger.warning("crawl.fetch_status", url=url, status_code=response.status_code)
        return None

    body = response.content
    if len(body) > MAX_DOCUMENT_BYTES:
        logger.warning("crawl.too_large", url=url, bytes=len(body))
        return None
    return body


def crawl_source(
    source: SourceDefinition,
    *,
    max_documents: int | None = None,
    known_hashes: Iterable[str] = (),
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    payloads: dict[str, bytes] | None = None,
) -> CrawlResult:
    """Fetch documents from one allow-listed manufacturer source.

    Respects robots.txt and the configured concurrency limit, and skips
    documents whose content hash is already staged or in production.

    Args:
        source: The source definition, including seed URLs and crawl depth.
        max_documents: Optional cap for incremental or test runs.
        known_hashes: Content hashes already staged or promoted. A document
            hashing to one of these is not returned, which is what makes a
            repeat run over unchanged content produce nothing.
        client: HTTP client to use. Injected so a test can drive the whole
            loop — robots, pacing, hashing, change detection — without a
            network.
        payloads: Optional sink filled with each fetched document's raw bytes,
            keyed by document id. Supplied by a caller that needs the real file
            — the structure extractor opens it with pdfplumber, and
            ``SourceDocument.text`` cannot serve, being a lossy UTF-8 decode
            that replaces every binary byte with U+FFFD. Optional so the
            crawler still runs, and is still testable, without one.
        sleep: Injected for the same reason; a test should not spend real
            seconds proving the pacer works.

    Returns:
        The fetched documents and per-URL outcomes.

    Raises:
        ValidationError: If the source is not on the allow-list.
        RobotsDisallowedError: If robots.txt forbids a URL this crawl needs.
            Deliberately fatal — see ``app.ingestion.robots``.
        RobotsUnavailableError: If robots.txt could not be read at all.
    """
    crawler = crawler_for(source.id)
    if crawler is None:
        # Not a warning-and-continue: an unrecognised source is a
        # configuration error, and crawling something we have no crawler for
        # is precisely what the allow-list exists to prevent.
        raise ValidationError(f"source {source.id!r} is not on the allow-list")

    owns_client = client is None
    active = client if client is not None else http_client(user_agent=robots_module.USER_AGENT)
    try:
        return _run(
            crawler=crawler,
            source=source,
            client=active,
            max_documents=max_documents,
            known=set(known_hashes),
            sleep=sleep,
            payloads=payloads,
        )
    finally:
        if owns_client:
            active.close()


def _run(
    *,
    payloads: dict[str, bytes] | None = None,
    crawler: SourceCrawler,
    source: SourceDefinition,
    client: httpx.Client,
    max_documents: int | None,
    known: set[str],
    sleep: Callable[[float], None],
) -> CrawlResult:
    """Drive one source's crawl. See ``crawl_source`` for the contract."""
    if not source.seed_urls:
        return CrawlResult(source_id=source.id, documents=[], outcomes=[])

    # Checked once per run, before anything is fetched. A source that has
    # closed to us should cost one request to discover, not a whole crawl.
    policy = robots_module.fetch_policy(
        source_id=source.id, seed_url=source.seed_urls[0], client=client
    )
    pacer = _Pacer(max(policy.crawl_delay_s or 0.0, DEFAULT_DELAY_S), sleep=sleep)

    documents: list[SourceDocument] = []
    outcomes: list[CrawlOutcome] = []
    seen_urls: set[str] = set()
    # A local sink when the caller did not supply one, so `_crawl_one` always
    # has somewhere to put the bytes and the optional parameter stays optional.
    sink: dict[str, bytes] = {} if payloads is None else payloads

    for listing_url in crawler.listing_urls(source.seed_urls):
        robots_module.require_allowed(policy, source_id=source.id, url=listing_url)
        pacer.wait()
        listing_body = _fetch(client, listing_url)
        if listing_body is None:
            outcomes.append(
                CrawlOutcome(url=listing_url, fetched=False, skipped_reason="listing-unreachable")
            )
            continue

        discovered = crawler.extract_documents(
            listing_url=listing_url, html=listing_body.decode("utf-8", errors="replace")
        )
        logger.info(
            "crawl.listing", source_id=source.id, listing_url=listing_url, found=len(discovered)
        )

        for document in discovered:
            if max_documents is not None and len(documents) >= max_documents:
                return CrawlResult(source_id=source.id, documents=documents, outcomes=outcomes)
            if document.url in seen_urls:
                continue
            seen_urls.add(document.url)

            outcome = _crawl_one(
                source=source,
                document=document,
                client=client,
                pacer=pacer,
                policy=policy,
                known=known,
                documents=documents,
                payloads=sink,
            )
            outcomes.append(outcome)

    return CrawlResult(source_id=source.id, documents=documents, outcomes=outcomes)


def _crawl_one(
    *,
    source: SourceDefinition,
    document: DiscoveredDocument,
    client: httpx.Client,
    pacer: _Pacer,
    policy: robots_module.RobotsPolicy,
    known: set[str],
    documents: list[SourceDocument],
    payloads: dict[str, bytes],
) -> CrawlOutcome:
    """Fetch and hash one document, appending it when it is new.

    Args:
        source: The source being crawled.
        document: The discovered document to fetch.
        client: HTTP client to fetch with.
        pacer: Enforces the delay between requests.
        policy: The robots policy for this source.
        known: Content hashes already seen; appended to as documents are kept.
        documents: Accumulator for documents that turned out to be new.
        payloads: Filled with the fetched bytes, keyed by document id.

    Returns:
        What happened to this URL, for the run's outcome list.

    The raw bytes are carried out rather than re-derived from
    ``SourceDocument.text``. That field is ``body.decode("utf-8",
    errors="replace")``, which for a PDF replaces every non-UTF-8 byte with
    U+FFFD -- the binary content is destroyed and cannot be recovered by
    re-encoding. The structure extractor opens the real file with pdfplumber,
    so without this the crawler and the extractor cannot be connected at all.
    """
    # Checked per document, not only per listing: a portal can permit its
    # index and disallow the files it links to.
    robots_module.require_allowed(policy, source_id=source.id, url=document.url)

    pacer.wait()
    body = _fetch(client, document.url)
    if body is None:
        return CrawlOutcome(url=document.url, fetched=False, skipped_reason="unreachable")

    digest = content_hash(body)
    if digest in known:
        # The acceptance criterion, in one branch. Unchanged content produces
        # no staging entry, so a nightly run over a static library is free.
        logger.info("crawl.unchanged", source_id=source.id, url=document.url)
        return CrawlOutcome(url=document.url, fetched=True, skipped_reason="unchanged")

    # Added to `known` immediately so two listings pointing at the same file
    # under different URLs do not both stage it.
    known.add(digest)
    payloads[digest[:32]] = body
    documents.append(
        SourceDocument(
            id=digest[:32],
            source_id=source.id,
            title=document.title,
            url=document.url,
            content_hash=digest,
            # Extraction to text belongs to the staging pipeline, which owns
            # parsing and chunking. The crawler's job ends at bytes.
            text=body.decode("utf-8", errors="replace"),
        )
    )
    logger.info("crawl.staged", source_id=source.id, url=document.url, content_hash=digest)
    return CrawlOutcome(url=document.url, fetched=True, skipped_reason=None)
