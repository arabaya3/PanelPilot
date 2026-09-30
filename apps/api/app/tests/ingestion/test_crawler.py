"""Tests for the documentation crawler.

The acceptance criterion is a two-run property: a manually-changed document is
detected and queued within one scheduled run, and an unchanged document
produces zero new staging entries on a repeat run. Both halves are exercised
here by running the real ``crawl_source`` loop twice against a fake transport,
rather than by testing the hash function and asserting the rest follows.

The other thing under test is the robots.txt behaviour, which the task is
unusually specific about: a disallowing source must **hard-fail with a clear
log entry, never silently skip or proceed anyway**. "Skip quietly" and "fail
loudly" produce the same empty result set, so only a test that distinguishes
them is worth having.
"""

from __future__ import annotations

import gzip

import httpx
import pytest

from app.core.errors import ValidationError
from app.ingestion import crawler as crawler_module
from app.ingestion import url_guard
from app.ingestion.crawler import (
    DEFAULT_DELAY_S,
    MAX_CRAWL_DELAY_S,
    DocumentCheck,
    check_documents,
    content_hash,
    crawl_source,
)
from app.ingestion.robots import (
    CrawlDelayTooLongError,
    RobotsDisallowedError,
    RobotsUnavailableError,
)
from app.models.schemas.documents import SourceDefinition

SEED = "https://library.abb.com/manuals"

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"


def listing(*hrefs: str) -> bytes:
    """A listing page linking to the given documents."""
    links = "".join(f'<a href="{href}">Manual {i}</a>' for i, href in enumerate(hrefs))
    return f"<html><body>{links}</body></html>".encode()


def transport(routes: dict[str, tuple[int, bytes]]) -> httpx.MockTransport:
    """A transport serving fixed responses, 404 for anything unrouted."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def client_for(routes: dict[str, tuple[int, bytes]]) -> httpx.Client:
    return httpx.Client(transport=transport(routes), follow_redirects=True)


def abb_source() -> SourceDefinition:
    return SourceDefinition(id="abb", manufacturer="ABB", seed_urls=[SEED], max_depth=1)


def routes_with(
    *documents: tuple[str, bytes], robots: str = ROBOTS_ALLOW_ALL
) -> dict[str, tuple[int, bytes]]:
    """Routes for a robots file, one listing, and the documents it links."""
    urls = [url for url, _ in documents]
    routes: dict[str, tuple[int, bytes]] = {
        "https://library.abb.com/robots.txt": (200, robots.encode()),
        SEED: (200, listing(*urls)),
    }
    for url, body in documents:
        routes[url] = (200, body)
    return routes


def no_sleep(_seconds: float) -> None:
    """Pacing is asserted separately; tests should not spend real seconds."""


#: A publicly routable address every test host "resolves" to, unless a test
#: says otherwise.
PUBLIC_ADDRESS = "93.184.215.14"


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every host to a public address, without touching DNS.

    The crawler refuses hosts resolving to internal addresses, which means it
    resolves every host it fetches from. A test suite that did that for real
    would depend on the network and on what these manufacturers' DNS says
    today.
    """
    monkeypatch.setattr(url_guard, "system_resolver", lambda _host: [PUBLIC_ADDRESS])


# --- the acceptance criterion, both halves ----------------------------------


def test_a_changed_document_is_queued_within_one_run() -> None:
    doc = "https://library.abb.com/acs880.pdf"
    result = crawl_source(
        abb_source(),
        client=client_for(routes_with((doc, b"revision A"))),
        sleep=no_sleep,
    )

    assert [d.url for d in result.documents] == [doc]
    assert result.documents[0].content_hash == content_hash(b"revision A")


def test_an_unchanged_document_produces_nothing_on_a_repeat_run() -> None:
    # The half that actually costs something to get wrong: a nightly crawl
    # over a static library must be free, or the review queue fills with
    # documents nobody changed and reviewers stop trusting it.
    doc = "https://library.abb.com/acs880.pdf"
    routes = routes_with((doc, b"revision A"))

    first = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert len(first.documents) == 1

    second = crawl_source(
        abb_source(),
        client=client_for(routes),
        known_hashes=[d.content_hash for d in first.documents],
        sleep=no_sleep,
    )

    assert second.documents == []
    assert [o.skipped_reason for o in second.outcomes] == ["unchanged"]
    # Fetched, but not staged — the distinction the outcome list exists for.
    assert second.outcomes[0].fetched is True


def test_a_changed_document_is_queued_while_its_unchanged_neighbour_is_not() -> None:
    # The realistic shape of a re-crawl: one file in a library of many moved.
    stable = "https://library.abb.com/stable.pdf"
    edited = "https://library.abb.com/edited.pdf"

    before = routes_with((stable, b"unchanged text"), (edited, b"revision A"))
    first = crawl_source(abb_source(), client=client_for(before), sleep=no_sleep)
    known = [d.content_hash for d in first.documents]
    assert len(known) == 2

    after = routes_with((stable, b"unchanged text"), (edited, b"revision B"))
    second = crawl_source(
        abb_source(), client=client_for(after), known_hashes=known, sleep=no_sleep
    )

    assert [d.url for d in second.documents] == [edited]


# --- robots.txt: fail, never skip -------------------------------------------


def test_a_disallowing_source_raises_rather_than_returning_empty() -> None:
    # The distinction the task names explicitly. An empty result and a refusal
    # look identical downstream; only one of them tells an operator that a
    # source they believe is being crawled has closed to us.
    doc = "https://library.abb.com/acs880.pdf"
    routes = routes_with((doc, b"body"), robots=ROBOTS_DISALLOW_ALL)

    with pytest.raises(RobotsDisallowedError) as caught:
        crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)

    assert caught.value.source_id == "abb"


def test_an_unreadable_robots_file_fails_rather_than_assuming_permission() -> None:
    # "We could not check" must not resolve to "so we proceeded".
    routes = {
        "https://library.abb.com/robots.txt": (403, b"forbidden"),
        SEED: (200, listing("https://library.abb.com/acs880.pdf")),
    }

    with pytest.raises(RobotsUnavailableError):
        crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)


def test_an_absent_robots_file_permits_the_crawl() -> None:
    # The one case where absence really does mean permission — the standard
    # says so, and treating a 404 as a failure would lock us out of compliant
    # sources.
    doc = "https://library.abb.com/acs880.pdf"
    routes = {
        SEED: (200, listing(doc)),
        doc: (200, b"body"),
    }

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert len(result.documents) == 1


def test_a_document_disallowed_beneath_an_allowed_listing_still_fails() -> None:
    # A portal can permit its index and disallow the files it links to, so the
    # check has to happen per document rather than once per run.
    doc = "https://library.abb.com/private/acs880.pdf"
    robots = "User-agent: *\nDisallow: /private/\n"
    routes = routes_with((doc, b"body"), robots=robots)

    with pytest.raises(RobotsDisallowedError):
        crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)


# --- the allow-list ----------------------------------------------------------


def test_an_unregistered_source_is_refused() -> None:
    unknown = SourceDefinition(id="acme", manufacturer="Acme", seed_urls=[SEED], max_depth=1)
    with pytest.raises(ValidationError, match="allow-list"):
        crawl_source(unknown, client=client_for({}), sleep=no_sleep)


def test_the_allow_list_covers_the_three_named_sources() -> None:
    from app.ingestion.sources import CRAWLERS

    assert set(CRAWLERS) == {"siemens", "abb", "schneider"}


# --- politeness --------------------------------------------------------------


def test_requests_are_paced() -> None:
    slept: list[float] = []
    doc_a = "https://library.abb.com/a.pdf"
    doc_b = "https://library.abb.com/b.pdf"

    crawl_source(
        abb_source(),
        client=client_for(routes_with((doc_a, b"a"), (doc_b, b"b"))),
        sleep=slept.append,
    )

    # Three fetches after the first: listing, then two documents. The exact
    # count matters less than that pacing happened at all between them.
    assert slept, "no delay was applied between requests"
    assert all(delay <= DEFAULT_DELAY_S for delay in slept)


def test_a_crawl_delay_in_robots_is_honoured_when_slower_than_ours() -> None:
    # Our politeness ceiling is not a reason to ignore a floor the source set.
    slept: list[float] = []
    doc = "https://library.abb.com/a.pdf"
    robots = "User-agent: *\nAllow: /\nCrawl-delay: 5\n"

    crawl_source(
        abb_source(),
        client=client_for(routes_with((doc, b"a"), robots=robots)),
        sleep=slept.append,
    )

    assert slept
    assert max(slept) > DEFAULT_DELAY_S


# --- what the crawler will not follow ----------------------------------------


def test_documents_on_another_host_are_not_followed() -> None:
    # Every politeness check was made against this host's robots.txt; a link
    # to a third-party mirror is a source we have not checked.
    offsite = "https://cdn.example.net/acs880.pdf"
    routes = {
        "https://library.abb.com/robots.txt": (200, ROBOTS_ALLOW_ALL.encode()),
        SEED: (200, listing(offsite)),
        offsite: (200, b"body"),
    }

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert result.documents == []


def test_non_document_links_are_ignored() -> None:
    routes = {
        "https://library.abb.com/robots.txt": (200, ROBOTS_ALLOW_ALL.encode()),
        SEED: (200, listing("https://library.abb.com/about.html")),
    }

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert result.documents == []


def test_one_document_linked_twice_is_staged_once() -> None:
    doc = "https://library.abb.com/acs880.pdf"
    routes = {
        "https://library.abb.com/robots.txt": (200, ROBOTS_ALLOW_ALL.encode()),
        SEED: (200, listing(doc, doc)),
        doc: (200, b"body"),
    }

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert len(result.documents) == 1


def test_two_urls_serving_identical_content_stage_once() -> None:
    # The same manual under a versioned and an unversioned URL. Hashing
    # content rather than URLs is what catches this.
    first = "https://library.abb.com/acs880.pdf"
    second = "https://library.abb.com/acs880-v2.pdf"
    routes = routes_with((first, b"identical"), (second, b"identical"))

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)
    assert len(result.documents) == 1


def test_an_unreachable_document_is_recorded_not_raised() -> None:
    # A single 500 is a bad day at the source, not a reason to abandon the
    # run — unlike robots, which is a standing instruction.
    good = "https://library.abb.com/good.pdf"
    bad = "https://library.abb.com/bad.pdf"
    routes = routes_with((good, b"body"))
    routes[SEED] = (200, listing(good, bad))

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)

    assert [d.url for d in result.documents] == [good]
    assert any(o.url == bad and o.skipped_reason == "unreachable" for o in result.outcomes)


def test_max_documents_caps_a_run() -> None:
    docs = [(f"https://library.abb.com/{i}.pdf", f"body {i}".encode()) for i in range(5)]
    result = crawl_source(
        abb_source(),
        client=client_for(routes_with(*docs)),
        max_documents=2,
        sleep=no_sleep,
    )
    assert len(result.documents) == 2


def test_a_source_with_no_seeds_does_nothing_rather_than_failing() -> None:
    empty = SourceDefinition(id="abb", manufacturer="ABB", seed_urls=[], max_depth=1)
    result = crawl_source(empty, client=client_for({}), sleep=no_sleep)
    assert result.documents == []
    assert result.outcomes == []


# --- documents supplied directly, bypassing discovery ------------------------
#
# For sources whose listings cannot be crawled while their documents plainly
# can. What must hold is that skipping discovery skips ONLY discovery: robots
# is still checked, hashing still dedupes, and the cap still binds.


def test_a_directly_supplied_document_is_fetched() -> None:
    """No listing involved, and none required."""
    routes = {
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.e.abb.com/public/a/manual.pdf": (200, b"%PDF-1.4 one"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=["https://library.e.abb.com/public/a/manual.pdf"],
    )

    result = crawl_source(source, client=client_for(routes), sleep=lambda _s: None)

    assert [document.url for document in result.documents] == [
        "https://library.e.abb.com/public/a/manual.pdf"
    ]


def test_a_directly_supplied_document_is_still_checked_against_robots() -> None:
    """The property that must not be lost.

    Bypassing discovery must not become bypassing permission — a curated list
    is a convenience for us, not a licence the operator granted.
    """
    routes = {
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nDisallow: /private/\n"),
        "https://library.e.abb.com/private/secret.pdf": (200, b"%PDF-1.4 no"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=["https://library.e.abb.com/private/secret.pdf"],
    )

    with pytest.raises(RobotsDisallowedError):
        crawl_source(source, client=client_for(routes), sleep=lambda _s: None)


def test_robots_is_read_from_the_documents_own_host() -> None:
    """A curated list legitimately points at a different host from the seeds.

    ABB's documents live on `library.e.abb.com` while its portal is
    `library.abb.com`. Checking the portal's robots.txt for an asset-host URL
    would be reading the wrong file, and the direction of that mistake is
    permitting a fetch nobody authorised.
    """
    routes = {
        # The seed host permits everything...
        "https://library.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.abb.com/listing": (200, b"<html></html>"),
        # ...while the asset host forbids the path we were handed.
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nDisallow: /public/\n"),
        "https://library.e.abb.com/public/a.pdf": (200, b"%PDF-1.4"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=["https://library.abb.com/listing"],
        document_urls=["https://library.e.abb.com/public/a.pdf"],
    )

    with pytest.raises(RobotsDisallowedError):
        crawl_source(source, client=client_for(routes), sleep=lambda _s: None)


def test_a_direct_document_already_known_is_skipped() -> None:
    """Change detection applies here too.

    Change detection applies here too: a re-run over an unchanged curated
    list must stage nothing.
    """
    body = b"%PDF-1.4 unchanged"
    routes = {
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.e.abb.com/public/a.pdf": (200, body),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=["https://library.e.abb.com/public/a.pdf"],
    )

    result = crawl_source(
        source,
        client=client_for(routes),
        sleep=lambda _s: None,
        known_hashes=[content_hash(body)],
    )

    assert result.documents == []


def test_direct_documents_and_listings_both_run() -> None:
    """A source may have both.

    A source may have both, and the curated URLs must not suppress a crawl
    that can still discover.
    """
    routes = {
        "https://library.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.abb.com/listing": (
            200,
            b'<a href="https://library.abb.com/found.pdf">Found</a>',
        ),
        "https://library.abb.com/found.pdf": (200, b"%PDF-1.4 discovered"),
        "https://library.abb.com/direct.pdf": (200, b"%PDF-1.4 direct"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=["https://library.abb.com/listing"],
        document_urls=["https://library.abb.com/direct.pdf"],
    )

    result = crawl_source(source, client=client_for(routes), sleep=lambda _s: None)

    assert {document.url for document in result.documents} == {
        "https://library.abb.com/direct.pdf",
        "https://library.abb.com/found.pdf",
    }


def test_the_document_cap_covers_direct_urls() -> None:
    """Otherwise a long curated list is an uncapped run by another name."""
    routes = {
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.e.abb.com/public/a.pdf": (200, b"%PDF-1.4 a"),
        "https://library.e.abb.com/public/b.pdf": (200, b"%PDF-1.4 b"),
        "https://library.e.abb.com/public/c.pdf": (200, b"%PDF-1.4 c"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=[
            "https://library.e.abb.com/public/a.pdf",
            "https://library.e.abb.com/public/b.pdf",
            "https://library.e.abb.com/public/c.pdf",
        ],
    )

    result = crawl_source(source, client=client_for(routes), sleep=lambda _s: None, max_documents=2)

    assert len(result.documents) == 2


def test_a_run_with_neither_seeds_nor_documents_fetches_nothing() -> None:
    source = SourceDefinition(id="abb", manufacturer="ABB", seed_urls=[], document_urls=[])

    result = crawl_source(source, client=client_for({}), sleep=lambda _s: None)

    assert result.documents == []
    assert result.outcomes == []


def test_a_repeated_direct_url_is_fetched_once() -> None:
    """A curated list is hand-maintained.

    A curated list is hand-maintained, so it will eventually contain the
    same URL twice.

    Content hashing means the duplicate stages nothing, which is why this needs
    its own assertion: without the URL check the second copy is still fetched
    and still reported as an outcome, so the waste is invisible in the staged
    result and visible only here.
    """
    routes = {
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nAllow: /\n"),
        "https://library.e.abb.com/public/a.pdf": (200, b"%PDF-1.4 a"),
    }
    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=[
            "https://library.e.abb.com/public/a.pdf",
            "https://library.e.abb.com/public/a.pdf",
        ],
    )

    result = crawl_source(source, client=client_for(routes), sleep=lambda _s: None)

    assert len(result.outcomes) == 1, "the same URL was fetched twice"
    assert len(result.documents) == 1


def test_each_host_is_asked_for_its_own_robots_once() -> None:
    """Two properties in one run: the right file, and only once per host.

    Keying the cache on anything but the host would either re-fetch robots.txt
    for every document -- a request per document, on a list built to avoid
    exactly that -- or serve one host's rules for another's, which is the
    permitting-a-fetch-nobody-authorised direction.
    """
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            fetched.append(url)
            return httpx.Response(200, content=b"User-agent: *\nAllow: /\n")
        return httpx.Response(200, content=b"%PDF-1.4")

    source = SourceDefinition(
        id="abb",
        manufacturer="ABB",
        seed_urls=[],
        document_urls=[
            "https://library.e.abb.com/public/a.pdf",
            "https://library.e.abb.com/public/b.pdf",
            "https://other.abb.com/public/c.pdf",
        ],
    )

    crawl_source(
        source,
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        sleep=lambda _s: None,
    )

    assert sorted(fetched) == [
        "https://library.e.abb.com/robots.txt",
        "https://other.abb.com/robots.txt",
    ]


# --- where the crawler will send a request -----------------------------------
#
# The crawler runs inside our network, so a URL naming an internal address --
# directly, or through a redirect the source chose -- makes it a proxy into
# that network whose responses get staged for a reviewer to read. Both
# reproductions from review are here.


def recording_client(
    routes: dict[str, httpx.Response], requested: list[str] | None = None
) -> httpx.Client:
    """A transport serving whole responses, recording every URL requested."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if requested is not None:
            requested.append(url)
        return routes.get(url, httpx.Response(404))

    # `follow_redirects=True` deliberately: the crawler must follow by hand
    # even when handed a client that would otherwise do it for it.
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def siemens_direct(*urls: str) -> SourceDefinition:
    return SourceDefinition(
        id="siemens", manufacturer="Siemens", seed_urls=[], document_urls=list(urls)
    )


SIEMENS_ROBOTS = "https://www.siemens.com/robots.txt"
SIEMENS_DOC = "https://www.siemens.com/manual.pdf"
METADATA = "http://169.254.169.254/latest/meta-data/iam"


def allow_all() -> httpx.Response:
    return httpx.Response(200, content=ROBOTS_ALLOW_ALL.encode())


def redirect_to(location: str) -> httpx.Response:
    return httpx.Response(302, headers={"Location": location})


def test_an_internal_document_url_is_refused_before_any_request() -> None:
    requested: list[str] = []
    source = siemens_direct("http://10.0.0.5:8080/admin.pdf")

    with pytest.raises(ValidationError, match="https"):
        crawl_source(source, client=recording_client({}, requested), sleep=no_sleep)

    assert requested == []


@pytest.mark.parametrize(
    "url",
    [
        "http://www.siemens.com/manual.pdf",
        "https://www.siemens.com:8443/manual.pdf",
        "https://evil.example/manual.pdf",
        "https://www.siemens.com@10.0.0.5/manual.pdf",
    ],
)
def test_a_seed_off_the_source_is_refused(url: str) -> None:
    source = SourceDefinition(id="siemens", manufacturer="Siemens", seed_urls=[url])

    with pytest.raises(ValidationError):
        crawl_source(source, client=recording_client({}), sleep=no_sleep)


def test_a_redirect_to_the_metadata_endpoint_is_not_followed() -> None:
    requested: list[str] = []
    routes = {SIEMENS_ROBOTS: allow_all(), SIEMENS_DOC: redirect_to(METADATA)}

    result = crawl_source(
        siemens_direct(SIEMENS_DOC), client=recording_client(routes, requested), sleep=no_sleep
    )

    assert result.documents == []
    assert [o.skipped_reason for o in result.outcomes] == ["redirect-rejected"]
    assert METADATA not in requested


def test_a_redirect_to_a_host_resolving_internally_is_not_followed() -> None:
    # On the source's own domain by name, internal by address: the name check
    # alone would pass it.
    cdn = "https://cdn.siemens.com/manual.pdf"
    requested: list[str] = []
    routes = {SIEMENS_ROBOTS: allow_all(), SIEMENS_DOC: redirect_to(cdn)}
    addresses = {"www.siemens.com": [PUBLIC_ADDRESS], "cdn.siemens.com": ["10.1.2.3"]}

    result = crawl_source(
        siemens_direct(SIEMENS_DOC),
        client=recording_client(routes, requested),
        sleep=no_sleep,
        resolve=lambda host: addresses[host],
    )

    assert [o.skipped_reason for o in result.outcomes] == ["redirect-rejected"]
    assert not any("cdn.siemens.com" in url for url in requested)


def test_a_redirect_within_the_source_is_followed_and_staged_under_the_original_url() -> None:
    cdn = "https://cdn.siemens.com/files/manual.pdf"
    routes = {
        SIEMENS_ROBOTS: allow_all(),
        "https://cdn.siemens.com/robots.txt": allow_all(),
        SIEMENS_DOC: redirect_to(cdn),
        cdn: httpx.Response(200, content=b"%PDF-1.4 real"),
    }

    result = crawl_source(
        siemens_direct(SIEMENS_DOC), client=recording_client(routes), sleep=no_sleep
    )

    assert [d.url for d in result.documents] == [SIEMENS_DOC]


def test_a_redirect_target_is_checked_against_its_own_robots() -> None:
    cdn = "https://cdn.siemens.com/private/manual.pdf"
    requested: list[str] = []
    routes = {
        SIEMENS_ROBOTS: allow_all(),
        "https://cdn.siemens.com/robots.txt": httpx.Response(
            200, content=b"User-agent: *\nDisallow: /private/\n"
        ),
        SIEMENS_DOC: redirect_to(cdn),
        cdn: httpx.Response(200, content=b"%PDF-1.4 forbidden"),
    }

    result = crawl_source(
        siemens_direct(SIEMENS_DOC), client=recording_client(routes, requested), sleep=no_sleep
    )

    assert [o.skipped_reason for o in result.outcomes] == ["redirect-rejected"]
    assert cdn not in requested


def test_a_redirect_loop_ends_as_an_outcome() -> None:
    routes = {SIEMENS_ROBOTS: allow_all(), SIEMENS_DOC: redirect_to(SIEMENS_DOC)}

    result = crawl_source(
        siemens_direct(SIEMENS_DOC), client=recording_client(routes), sleep=no_sleep
    )

    assert [o.skipped_reason for o in result.outcomes] == ["too-many-redirects"]


def test_a_document_host_resolving_internally_is_skipped() -> None:
    other = "https://intranet.siemens.com/manual.pdf"
    requested: list[str] = []
    routes = {SIEMENS_ROBOTS: allow_all(), SIEMENS_DOC: httpx.Response(200, content=b"ok")}
    addresses = {"www.siemens.com": [PUBLIC_ADDRESS], "intranet.siemens.com": ["127.0.0.1"]}

    result = crawl_source(
        siemens_direct(SIEMENS_DOC, other),
        client=recording_client(routes, requested),
        sleep=no_sleep,
        resolve=lambda host: addresses[host],
    )

    assert [d.url for d in result.documents] == [SIEMENS_DOC]
    assert (other, "unsafe-url") in [(o.url, o.skipped_reason) for o in result.outcomes]
    # Not even its robots.txt: that request would reach the internal host too.
    assert not any("intranet" in url for url in requested)


def test_an_origin_resolving_internally_fails_the_run() -> None:
    with pytest.raises(ValidationError, match="non-public"):
        crawl_source(
            siemens_direct(SIEMENS_DOC),
            client=recording_client({}),
            sleep=no_sleep,
            resolve=lambda _host: ["169.254.169.254"],
        )


# --- robots.txt of the right host for discovered documents -------------------


def test_a_discovered_document_is_checked_against_its_own_hosts_robots() -> None:
    # The listing's host permits everything; the PDF's subdomain does not.
    # Checking the PDF against the listing host's robots.txt reads the wrong
    # file and permits a fetch its own host forbids.
    pdf = "https://library.e.abb.com/public/a.pdf"
    routes = {
        "https://library.abb.com/robots.txt": (200, ROBOTS_ALLOW_ALL.encode()),
        SEED: (200, listing(pdf)),
        "https://library.e.abb.com/robots.txt": (200, b"User-agent: *\nDisallow: /public/\n"),
        pdf: (200, b"%PDF-1.4"),
    }

    with pytest.raises(RobotsDisallowedError) as caught:
        crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)

    assert caught.value.url == pdf


# --- bounded work ------------------------------------------------------------


def test_an_oversized_document_is_skipped_without_being_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crawler_module, "MAX_DOCUMENT_BYTES", 1000)
    big = "https://library.abb.com/big.pdf"
    small = "https://library.abb.com/small.pdf"
    routes = routes_with((big, b"x" * 1001), (small, b"y" * 1000))

    result = crawl_source(abb_source(), client=client_for(routes), sleep=no_sleep)

    assert [d.url for d in result.documents] == [small]
    assert (big, "too-large") in [(o.url, o.skipped_reason) for o in result.outcomes]


def test_a_compressed_document_is_measured_after_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reported shape: a small gzip body that inflates far past the cap.
    monkeypatch.setattr(crawler_module, "MAX_DOCUMENT_BYTES", 100_000)
    routes = {
        SIEMENS_ROBOTS: allow_all(),
        SIEMENS_DOC: httpx.Response(
            200,
            content=gzip.compress(b"\0" * 1_000_000),
            headers={"Content-Encoding": "gzip"},
        ),
    }

    result = crawl_source(
        siemens_direct(SIEMENS_DOC), client=recording_client(routes), sleep=no_sleep
    )

    assert [o.skipped_reason for o in result.outcomes] == ["too-large"]


def test_every_request_counts_against_the_fetch_budget() -> None:
    # Five unchanged documents: `max_documents` would never bind, because
    # nothing is new. The fetch budget does -- robots, listing, then two
    # documents, and the run ends with an outcome saying why.
    docs = [(f"https://library.abb.com/{i}.pdf", f"body {i}".encode()) for i in range(5)]
    known = [content_hash(body) for _, body in docs]

    result = crawl_source(
        abb_source(),
        client=client_for(routes_with(*docs)),
        known_hashes=known,
        max_fetches=4,
        sleep=no_sleep,
    )

    assert [o.skipped_reason for o in result.outcomes] == [
        "unchanged",
        "unchanged",
        "fetch-budget-exhausted",
    ]
    assert result.outcomes[-1].url == "https://library.abb.com/2.pdf"


def test_an_excessive_crawl_delay_fails_the_run_rather_than_being_clamped() -> None:
    robots = "User-agent: *\nAllow: /\nCrawl-delay: 3600\n"
    routes = routes_with(("https://library.abb.com/a.pdf", b"a"), robots=robots)
    slept: list[float] = []

    with pytest.raises(CrawlDelayTooLongError) as caught:
        crawl_source(abb_source(), client=client_for(routes), sleep=slept.append)

    assert caught.value.delay_s == 3600
    assert slept == []


def test_the_longest_permitted_crawl_delay_is_honoured() -> None:
    robots = f"User-agent: *\nAllow: /\nCrawl-delay: {int(MAX_CRAWL_DELAY_S)}\n"
    routes = routes_with(("https://library.abb.com/a.pdf", b"a"), robots=robots)
    slept: list[float] = []

    crawl_source(abb_source(), client=client_for(routes), sleep=slept.append)

    assert slept
    assert max(slept) > MAX_CRAWL_DELAY_S - 1


# --- check_documents: what a live document's URL serves now -----------------


def test_check_reports_the_hash_of_what_each_url_serves_now() -> None:
    first = "https://library.abb.com/a.pdf"
    second = "https://library.abb.com/b.pdf"
    routes = routes_with((first, b"revision 2"), (second, b"unchanged"))

    checks = check_documents("abb", [first, second], client=client_for(routes), sleep=no_sleep)

    assert checks == [
        DocumentCheck(first, "fetched", content_hash(b"revision 2")),
        DocumentCheck(second, "fetched", content_hash(b"unchanged")),
    ]


@pytest.mark.parametrize("status", [404, 410])
def test_check_tells_a_withdrawn_document_from_a_failing_source(status: int) -> None:
    # A crawl folds every 4xx and 5xx into `unreachable`. Expiry cannot: a
    # 404 means the manual was withdrawn, a 500 means nothing at all.
    gone = "https://library.abb.com/gone.pdf"
    broken = "https://library.abb.com/broken.pdf"
    routes = routes_with()
    routes[gone] = (status, b"")
    routes[broken] = (503, b"")

    checks = check_documents("abb", [gone, broken], client=client_for(routes), sleep=no_sleep)

    assert checks == [DocumentCheck(gone, "gone"), DocumentCheck(broken, "unreachable")]


def test_check_honours_robots_per_url_without_abandoning_the_rest() -> None:
    allowed = "https://library.abb.com/public/a.pdf"
    blocked = "https://library.abb.com/private/b.pdf"
    routes = routes_with(
        (allowed, b"body"), (blocked, b"secret"), robots="User-agent: *\nDisallow: /private/\n"
    )

    checks = check_documents("abb", [blocked, allowed], client=client_for(routes), sleep=no_sleep)

    assert checks == [
        DocumentCheck(blocked, "disallowed"),
        DocumentCheck(allowed, "fetched", content_hash(b"body")),
    ]


def test_check_refuses_a_url_off_the_source_domain() -> None:
    elsewhere = "https://attacker.example/a.pdf"
    checks = check_documents("abb", [elsewhere], client=client_for({}), sleep=no_sleep)
    assert checks == [DocumentCheck(elsewhere, "unsafe-url")]


def test_check_reports_urls_past_the_budget_instead_of_dropping_them() -> None:
    urls = [f"https://library.abb.com/{n}.pdf" for n in range(3)]
    routes = routes_with(*((url, b"x") for url in urls))

    # One request for robots.txt and one document; the rest are unchecked.
    checks = check_documents("abb", urls, client=client_for(routes), sleep=no_sleep, max_fetches=2)

    assert [c.status for c in checks] == [
        "fetched",
        "fetch-budget-exhausted",
        "fetch-budget-exhausted",
    ]


def test_check_keeps_the_gap_between_requests_to_one_host() -> None:
    # `pace_for` starts a fresh pacer; calling it before every URL would
    # forget the previous request and never wait.
    urls = [f"https://library.abb.com/{n}.pdf" for n in range(3)]
    routes = routes_with(*((url, b"x") for url in urls))
    slept: list[float] = []

    check_documents("abb", urls, client=client_for(routes), sleep=slept.append)

    assert len(slept) == len(urls) - 1


def test_check_refuses_a_source_off_the_allow_list() -> None:
    with pytest.raises(ValidationError):
        check_documents("nobody", [], client=client_for({}), sleep=no_sleep)
