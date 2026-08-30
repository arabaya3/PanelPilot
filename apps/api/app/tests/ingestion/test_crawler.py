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

import httpx
import pytest

from app.core.errors import ValidationError
from app.ingestion.crawler import DEFAULT_DELAY_S, content_hash, crawl_source
from app.ingestion.robots import RobotsDisallowedError, RobotsUnavailableError
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
