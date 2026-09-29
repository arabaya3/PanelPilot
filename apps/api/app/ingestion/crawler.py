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

Every request also passes ``app.ingestion.url_guard`` first -- the source's own
domain, https, and a publicly routable address -- including each redirect hop,
which is followed here by hand rather than by httpx. The crawler runs inside
our network; without that, a URL or a redirect naming an internal address makes
it a proxy into that network whose responses get staged for a reviewer to read.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

import httpx
import structlog

from app.core.errors import ValidationError
from app.ingestion import robots as robots_module
from app.ingestion import url_guard
from app.ingestion.sources import (
    DiscoveredDocument,
    ResponseLimitError,
    SourceCrawler,
    crawler_for,
    http_client,
    read_limited,
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

#: The longest ``Crawl-delay`` we will honour. Above it the run fails rather
#: than clamping -- see ``robots.CrawlDelayTooLongError`` for why neither
#: clamping nor obeying is acceptable. A minute between requests still lets a
#: capped run finish within a few hours.
MAX_CRAWL_DELAY_S = 60.0

#: A document larger than this is not read into memory. Manufacturer manuals
#: run to a few tens of megabytes; something far past that is a mistake or a
#: trap, and either way not worth an unbounded read.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

#: Wall-clock budget for one document, redirects included. Generous for 64 MB
#: over a slow link; what it exists to stop is a server that keeps a
#: connection alive by dripping bytes just inside the per-read timeout.
DOCUMENT_DEADLINE_S = 300.0

#: Redirect hops followed per URL. Download portals bounce through a CDN or a
#: signed-URL service once or twice; more than this is a loop or a maze.
MAX_REDIRECTS = 5

#: Requests one run may make, counting every fetch -- robots.txt, listings,
#: documents, redirect hops -- and not only documents that turn out to be new.
#: ``max_documents`` caps what is *staged*, so a run over a large library of
#: unchanged files would otherwise fetch every one of them, however many the
#: listings link to.
MAX_FETCHES_PER_RUN = 200


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


class _FetchBudgetExhaustedError(Exception):
    """The run has made ``max_fetches`` requests and must stop.

    Internal: raised from wherever the next request would have been made and
    caught once in ``_run``, which ends the run with what it has. An
    exception because the request sites are several calls deep, and threading
    a "stop" result back through each of them is how one gets missed.
    """

    def __init__(self, url: str) -> None:
        """Record the URL that would have been fetched next.

        Args:
            url: The request that was not made.
        """
        self.url = url
        super().__init__(f"fetch budget exhausted before {url}")


def _host_of(url: str) -> str:
    """Return the scheme and host a URL belongs to.

    Args:
        url: An absolute URL.

    Returns:
        The ``scheme://host`` prefix, used to key one robots policy per host.
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class _Fetcher:
    """Everything one run needs to make a request safely.

    Holds the per-run state -- robots policies by host, the pacer, the fetch
    budget -- so that every request, whether for robots.txt, a listing, a
    document or a redirect hop, goes through the same checks in the same
    order. They were previously applied by each caller, and the one caller
    that forgot (listing-discovered documents checked against the *seed's*
    robots.txt) is the bug this shape exists to prevent.
    """

    def __init__(
        self,
        *,
        source_id: str,
        host_suffix: str,
        client: httpx.Client,
        resolve: url_guard.Resolver,
        sleep: Callable[[float], None],
        max_fetches: int,
    ) -> None:
        self.source_id = source_id
        self.host_suffix = host_suffix
        self.client = client
        self.resolve = resolve
        self.remaining = max_fetches
        self.policies: dict[str, robots_module.RobotsPolicy] = {}
        # Replaced once the origin's robots.txt says how slow to go; our own
        # floor until then.
        self.pacer = _Pacer(DEFAULT_DELAY_S, sleep=sleep)
        self._sleep = sleep

    def pace_for(self, policy: robots_module.RobotsPolicy) -> None:
        """Set the gap between requests from the source's robots policy.

        Args:
            policy: The origin host's policy.
        """
        self.pacer = _Pacer(max(policy.crawl_delay_s or 0.0, DEFAULT_DELAY_S), sleep=self._sleep)

    def _spend(self, url: str) -> None:
        """Count one request against the run's budget.

        Args:
            url: The URL about to be requested.

        Raises:
            _FetchBudgetExhaustedError: If the budget is already spent.
        """
        if self.remaining <= 0:
            raise _FetchBudgetExhaustedError(url)
        self.remaining -= 1

    def _redirect_allowed(self, url: str) -> bool:
        """Report whether robots.txt fetching may follow a redirect here.

        Args:
            url: The redirect target.

        Returns:
            ``True`` if the target passes every ``url_guard`` check.
        """
        try:
            url_guard.require_fetchable(url, host_suffix=self.host_suffix, resolve=self.resolve)
        except url_guard.UnsafeUrlError:
            return False
        return True

    def policy_for(self, url: str) -> robots_module.RobotsPolicy:
        """Return the robots policy governing a URL, fetching it once per host.

        Args:
            url: The URL about to be fetched.

        Returns:
            The policy for that URL's host.

        Raises:
            UnsafeUrlError: If the host resolves to a non-public address; the
                robots.txt request would itself reach it.
            RobotsUnavailableError: If that host's robots.txt cannot be read.
            CrawlDelayTooLongError: If it asks for more than
                ``MAX_CRAWL_DELAY_S`` between requests.
            _FetchBudgetExhaustedError: If the run has no requests left.

        Per host rather than per run, because a curated document list
        legitimately points at a different host from the listing pages: ABB's
        documents live on ``library.e.abb.com`` while its portal is
        ``library.abb.com``. Reusing the portal's policy for the asset host
        would be checking the wrong file — and the direction of that mistake is
        permitting a fetch nobody authorised.
        """
        host = _host_of(url)
        cached = self.policies.get(host)
        if cached is not None:
            return cached

        url_guard.require_public_host(url, resolve=self.resolve)
        self._spend(robots_module.robots_url_for(url))
        policy = robots_module.fetch_policy(
            source_id=self.source_id,
            seed_url=url,
            client=self.client,
            allow_redirect=self._redirect_allowed,
        )
        if policy.crawl_delay_s is not None and policy.crawl_delay_s > MAX_CRAWL_DELAY_S:
            logger.error(
                "crawl.crawl_delay_too_long",
                source_id=self.source_id,
                host=host,
                crawl_delay_s=policy.crawl_delay_s,
                limit_s=MAX_CRAWL_DELAY_S,
            )
            raise robots_module.CrawlDelayTooLongError(
                self.source_id, url, policy.crawl_delay_s, MAX_CRAWL_DELAY_S
            )
        self.policies[host] = policy
        return policy

    def _refuse_redirect(self, url: str) -> str | None:
        """Decide whether a redirect target may be requested.

        Args:
            url: Where the source redirected us.

        Returns:
            ``None`` if it may, otherwise why not, for the log.

        A refusal is a per-document outcome rather than a failed run. Unlike a
        disallow on a URL we chose, a redirect is the source's decision about
        one document, and failing the run over it would discard every other
        document for the sake of one link. It is still logged at error level,
        so a portal that has moved its files somewhere we will not follow is
        visible rather than quietly empty.
        """
        try:
            url_guard.require_source_url(url, host_suffix=self.host_suffix)
            policy = self.policy_for(url)
        except (url_guard.UnsafeUrlError, robots_module.RobotsUnavailableError) as exc:
            return str(exc)
        if not policy.allows(url):
            return "robots.txt disallows the redirect target"
        return None

    def get(self, url: str) -> tuple[bytes | None, str | None]:
        """Fetch one URL, following redirects by hand.

        Args:
            url: Absolute URL to fetch. Its own robots check is the caller's,
                because a disallow on a URL we chose is fatal, not a skip.

        Returns:
            ``(body, None)`` on success, or ``(None, reason)`` where reason is
            a short ``skipped_reason`` code: ``unreachable``, ``unsafe-url``,
            ``redirect-rejected``, ``too-many-redirects``, ``too-large`` or
            ``timed-out``.

        Raises:
            _FetchBudgetExhaustedError: If the run runs out of requests.
            CrawlDelayTooLongError: If a redirect host's robots.txt asks for
                more delay than we honour.
        """
        deadline = time.monotonic() + DOCUMENT_DEADLINE_S
        target = url
        for hop in range(MAX_REDIRECTS + 1):
            if hop:
                refusal = self._refuse_redirect(target)
                if refusal is not None:
                    logger.error(
                        "crawl.redirect_rejected",
                        source_id=self.source_id,
                        url=url,
                        target=target,
                        reason=refusal,
                    )
                    return None, "redirect-rejected"
            try:
                # Before every hop, not once: the name is resolved again for
                # each connection, and a redirect can name a new host.
                url_guard.require_public_host(target, resolve=self.resolve)
            except url_guard.UnsafeUrlError as exc:
                logger.error(
                    "crawl.unsafe_url", source_id=self.source_id, url=target, error=str(exc)
                )
                return None, "redirect-rejected" if hop else "unsafe-url"
            if time.monotonic() > deadline:
                return None, "timed-out"

            self._spend(target)
            self.pacer.wait()
            try:
                with self.client.stream("GET", target, follow_redirects=False) as response:
                    if response.is_redirect:
                        target = str(response.url.join(response.headers["Location"]))
                        continue
                    if response.status_code >= 400:
                        logger.warning(
                            "crawl.fetch_status", url=target, status_code=response.status_code
                        )
                        return None, "unreachable"
                    body = read_limited(response, max_bytes=MAX_DOCUMENT_BYTES, deadline=deadline)
            except httpx.HTTPError as exc:
                logger.warning("crawl.fetch_failed", url=target, error=str(exc))
                return None, "unreachable"
            except ResponseLimitError as exc:
                logger.warning("crawl.fetch_limit", url=target, reason=exc.reason, error=str(exc))
                return None, exc.reason
            return body, None

        logger.warning("crawl.too_many_redirects", source_id=self.source_id, url=url)
        return None, "too-many-redirects"


def crawl_source(
    source: SourceDefinition,
    *,
    max_documents: int | None = None,
    known_hashes: Iterable[str] = (),
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    payloads: dict[str, bytes] | None = None,
    resolve: url_guard.Resolver | None = None,
    max_fetches: int = MAX_FETCHES_PER_RUN,
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
        resolve: Resolves a hostname to addresses for the private-address
            check. Defaults to the system resolver; injected so tests never
            depend on DNS.
        max_fetches: Most requests this run may make; see
            ``MAX_FETCHES_PER_RUN``. Reaching it ends the run with what it
            has, recorded as a ``fetch-budget-exhausted`` outcome.

    Returns:
        The fetched documents and per-URL outcomes.

    Raises:
        ValidationError: If the source is not on the allow-list, or a seed or
            document URL is not https on the source's own domain, or the
            origin host resolves to a non-public address.
        RobotsDisallowedError: If robots.txt forbids a URL this crawl needs.
            Deliberately fatal — see ``app.ingestion.robots``.
        RobotsUnavailableError: If robots.txt could not be read at all.
        CrawlDelayTooLongError: If robots.txt asks for more than
            ``MAX_CRAWL_DELAY_S`` between requests.
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
            # Looked up at call time rather than bound as a default, so a test
            # replacing `url_guard.system_resolver` reaches every caller.
            resolve=resolve if resolve is not None else url_guard.system_resolver,
            max_fetches=max_fetches,
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
    resolve: url_guard.Resolver,
    max_fetches: int,
) -> CrawlResult:
    """Drive one source's crawl. See ``crawl_source`` for the contract."""
    if not source.seed_urls and not source.document_urls:
        return CrawlResult(source_id=source.id, documents=[], outcomes=[])

    # The domain refuses these before a job exists; checked again here because
    # the crawler is callable on its own, and it is the layer that would make
    # the request.
    for url in (*source.seed_urls, *source.document_urls):
        url_guard.require_source_url(url, host_suffix=crawler.host_suffix)

    fetcher = _Fetcher(
        source_id=source.id,
        host_suffix=crawler.host_suffix,
        client=client,
        resolve=resolve,
        sleep=sleep,
        max_fetches=max_fetches,
    )
    documents: list[SourceDocument] = []
    outcomes: list[CrawlOutcome] = []
    try:
        _crawl(
            crawler=crawler,
            source=source,
            fetcher=fetcher,
            max_documents=max_documents,
            known=known,
            documents=documents,
            outcomes=outcomes,
            # A local sink when the caller did not supply one, so `_crawl_one`
            # always has somewhere to put the bytes and the optional parameter
            # stays optional.
            payloads={} if payloads is None else payloads,
        )
    except _FetchBudgetExhaustedError as exc:
        # Ends the run rather than failing it: what was fetched so far is as
        # good as it would have been, and the next scheduled run carries on.
        logger.warning(
            "crawl.fetch_budget_exhausted",
            source_id=source.id,
            max_fetches=max_fetches,
            next_url=exc.url,
        )
        outcomes.append(
            CrawlOutcome(url=exc.url, fetched=False, skipped_reason="fetch-budget-exhausted")
        )
    return CrawlResult(source_id=source.id, documents=documents, outcomes=outcomes)


def _crawl(
    *,
    crawler: SourceCrawler,
    source: SourceDefinition,
    fetcher: _Fetcher,
    max_documents: int | None,
    known: set[str],
    documents: list[SourceDocument],
    outcomes: list[CrawlOutcome],
    payloads: dict[str, bytes],
) -> None:
    """Crawl direct documents, then listings, appending to the accumulators.

    Args:
        crawler: The source's crawler.
        source: The source being crawled.
        fetcher: Makes every request for this run.
        max_documents: Optional cap on new documents.
        known: Content hashes already seen.
        documents: Accumulator for new documents.
        outcomes: Accumulator for per-URL outcomes.
        payloads: Filled with fetched bytes, keyed by document id.
    """
    # Checked once per run, before anything is fetched. A source that has
    # closed to us should cost one request to discover, not a whole crawl.
    #
    # Derived from whichever entry point exists: a direct-document run has no
    # listing to start from, and robots.txt lives at the host root either way.
    # Every other URL is checked against its own host's policy through
    # `fetcher.policy_for`, so a run whose documents sit on a different host
    # from its seeds is still checked against the host it is fetching from.
    origin_url = source.seed_urls[0] if source.seed_urls else source.document_urls[0]
    fetcher.pace_for(fetcher.policy_for(origin_url))

    seen_urls: set[str] = set()

    # Directly-supplied documents first, so a run carrying both still fetches
    # the known-good URLs when a listing turns out to be unreachable.
    for direct_url in source.document_urls:
        if max_documents is not None and len(documents) >= max_documents:
            break
        if direct_url in seen_urls:
            continue
        seen_urls.add(direct_url)
        outcomes.append(
            _crawl_one(
                source=source,
                # The filename is the only title available without opening the
                # PDF. The curated list carries a real one, but the crawler is
                # not the layer that knows about it.
                document=DiscoveredDocument(url=direct_url, title=direct_url.rsplit("/", 1)[-1]),
                fetcher=fetcher,
                known=known,
                documents=documents,
                payloads=payloads,
            )
        )

    for listing_url in crawler.listing_urls(source.seed_urls):
        # Its own host's policy, not the origin's: a source with seeds on two
        # hosts has two robots files.
        robots_module.require_allowed(
            fetcher.policy_for(listing_url), source_id=source.id, url=listing_url
        )
        listing_body, reason = fetcher.get(listing_url)
        if listing_body is None:
            outcomes.append(
                CrawlOutcome(url=listing_url, fetched=False, skipped_reason=f"listing-{reason}")
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
                return
            if document.url in seen_urls:
                continue
            seen_urls.add(document.url)

            outcomes.append(
                _crawl_one(
                    source=source,
                    document=document,
                    fetcher=fetcher,
                    known=known,
                    documents=documents,
                    payloads=payloads,
                )
            )


def _crawl_one(
    *,
    source: SourceDefinition,
    document: DiscoveredDocument,
    fetcher: _Fetcher,
    known: set[str],
    documents: list[SourceDocument],
    payloads: dict[str, bytes],
) -> CrawlOutcome:
    """Fetch and hash one document, appending it when it is new.

    Args:
        source: The source being crawled.
        document: The discovered document to fetch.
        fetcher: Makes the request, under this run's robots, pacing, address
            and budget rules.
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
    try:
        # The document's own host, whether it came from the curated list or a
        # listing. Discovered documents were once checked against the seed
        # host's policy, which for a PDF on another subdomain is the wrong
        # file -- and the direction of that mistake is fetching what its own
        # host forbids.
        policy = fetcher.policy_for(document.url)
    except url_guard.UnsafeUrlError as exc:
        logger.error("crawl.unsafe_url", source_id=source.id, url=document.url, error=str(exc))
        return CrawlOutcome(url=document.url, fetched=False, skipped_reason="unsafe-url")

    # Checked per document, not only per listing: a portal can permit its
    # index and disallow the files it links to.
    robots_module.require_allowed(policy, source_id=source.id, url=document.url)

    body, reason = fetcher.get(document.url)
    if body is None:
        return CrawlOutcome(url=document.url, fetched=False, skipped_reason=reason)

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
