"""Tests for the per-source crawlers.

The design constraint the task set is that adding a fourth brand means writing
one class, not touching shared pipeline code. These check that the seam holds:
each source decides only *where documents are* and *what counts as one*, and
everything else is shared.

The host check gets the most attention here. Every politeness decision the
crawler makes was taken against one host's robots.txt, so a link to a
third-party mirror is a source nobody checked — following it would be a
politeness violation dressed up as thoroughness.
"""

from __future__ import annotations

import gzip

import httpx
import pytest

from app.ingestion.sources import (
    CRAWLERS,
    AbbCrawler,
    DanfossCrawler,
    DeltaCrawler,
    MitsubishiCrawler,
    OmronCrawler,
    ResponseLimitError,
    RockwellCrawler,
    SchneiderCrawler,
    SiemensCrawler,
    SourceCrawler,
    WegCrawler,
    YaskawaCrawler,
    crawler_for,
    http_client,
    read_limited,
)


def page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{h}">Manual</a>' for h in hrefs) + "</body></html>"


# --- the registry ------------------------------------------------------------


def test_the_named_sources_are_registered() -> None:
    assert set(CRAWLERS) == {
        "siemens",
        "abb",
        "schneider",
        "danfoss",
        "yaskawa",
        "rockwell",
        "mitsubishi",
        "weg",
        "omron",
        "delta",
        "lselectric",
        "inovance",
        "hitachi",
        "fuji",
        "nidec",
        "sew",
        "invertek",
        "lenze",
        "phoenixcontact",
        "weidmueller",
        "br",
    }


def test_each_crawler_is_registered_under_its_own_id() -> None:
    # A mismatch here would route ABB listings through the Siemens host check
    # and silently drop every document.
    for source_id, crawler in CRAWLERS.items():
        assert crawler.source_id == source_id


def test_every_crawler_names_a_manufacturer() -> None:
    # Shown in logs and in BE-006's source-health record; a blank one makes an
    # operator guess which source is failing.
    for crawler in CRAWLERS.values():
        assert crawler.manufacturer.strip()


def test_an_unregistered_source_has_no_crawler() -> None:
    assert crawler_for("acme") is None


def test_the_interface_is_abstract() -> None:
    # A fourth brand must implement both methods; inheriting a silent default
    # would produce a crawler that finds nothing and says nothing.
    with pytest.raises(TypeError):
        SourceCrawler()  # type: ignore[abstract]


# --- finding documents -------------------------------------------------------


@pytest.mark.parametrize(
    ("crawler", "host"),
    [
        (SiemensCrawler(), "https://support.industry.siemens.com"),
        (AbbCrawler(), "https://library.abb.com"),
        (SchneiderCrawler(), "https://www.se.com"),
        (DanfossCrawler(), "https://assets.danfoss.com"),
        (YaskawaCrawler(), "https://www.yaskawa.com"),
        (RockwellCrawler(), "https://literature.rockwellautomation.com"),
        (MitsubishiCrawler(), "https://dl.mitsubishielectric.com"),
        (WegCrawler(), "https://static.weg.net"),
        (OmronCrawler(), "https://assets.omron.eu"),
        (DeltaCrawler(), "https://downloadcenter.deltaww.com"),
    ],
)
def test_a_pdf_on_the_sources_own_host_is_found(crawler: SourceCrawler, host: str) -> None:
    listing = f"{host}/manuals"
    found = crawler.extract_documents(listing_url=listing, html=page(f"{host}/a.pdf"))

    assert [d.url for d in found] == [f"{host}/a.pdf"]


@pytest.mark.parametrize(
    ("crawler", "host"),
    [
        (SiemensCrawler(), "https://support.industry.siemens.com"),
        (AbbCrawler(), "https://library.abb.com"),
        (SchneiderCrawler(), "https://www.se.com"),
        (DanfossCrawler(), "https://assets.danfoss.com"),
        (YaskawaCrawler(), "https://www.yaskawa.com"),
        (RockwellCrawler(), "https://literature.rockwellautomation.com"),
        (MitsubishiCrawler(), "https://dl.mitsubishielectric.com"),
        (WegCrawler(), "https://static.weg.net"),
        (OmronCrawler(), "https://assets.omron.eu"),
        (DeltaCrawler(), "https://downloadcenter.deltaww.com"),
    ],
)
def test_a_pdf_on_another_host_is_not_followed(crawler: SourceCrawler, host: str) -> None:
    # Every politeness check was made against this host's robots.txt.
    listing = f"{host}/manuals"
    found = crawler.extract_documents(
        listing_url=listing, html=page("https://cdn.example.net/a.pdf")
    )

    assert found == []


def test_a_relative_href_is_resolved_against_the_listing() -> None:
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals/index.html",
        html=page("../drives/acs880.pdf"),
    )

    assert [d.url for d in found] == ["https://library.abb.com/drives/acs880.pdf"]


def test_non_pdf_links_are_ignored() -> None:
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals",
        html=page("https://library.abb.com/about.html", "https://library.abb.com/a.pdf"),
    )

    assert [d.url for d in found] == ["https://library.abb.com/a.pdf"]


def test_a_pdf_linked_twice_is_returned_once() -> None:
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals",
        html=page("https://library.abb.com/a.pdf", "https://library.abb.com/a.pdf"),
    )

    assert len(found) == 1


def test_a_query_string_does_not_hide_a_pdf() -> None:
    # Download portals routinely append tokens; matching on the path rather
    # than the whole URL is what makes those visible.
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals",
        html=page("https://library.abb.com/a.pdf?token=abc123"),
    )

    assert len(found) == 1


def test_the_link_text_becomes_the_title() -> None:
    html = '<a href="https://library.abb.com/a.pdf">ACS880 Firmware Manual</a>'
    found = AbbCrawler().extract_documents(listing_url="https://library.abb.com/m", html=html)

    assert found[0].title == "ACS880 Firmware Manual"


def test_a_titleless_link_falls_back_to_the_filename() -> None:
    # An empty row in the review queue tells a reviewer nothing about what
    # they are being asked to check.
    html = '<a href="https://library.abb.com/acs880.pdf"><img src="icon.png"></a>'
    found = AbbCrawler().extract_documents(listing_url="https://library.abb.com/m", html=html)

    assert found[0].title == "acs880.pdf"


def test_nested_markup_inside_the_link_is_stripped() -> None:
    html = '<a href="https://library.abb.com/a.pdf"><span>ACS880</span> <b>Manual</b></a>'
    found = AbbCrawler().extract_documents(listing_url="https://library.abb.com/m", html=html)

    assert found[0].title == "ACS880 Manual"


def test_listing_urls_default_to_the_seeds() -> None:
    seeds = ["https://library.abb.com/a", "https://library.abb.com/b"]
    assert AbbCrawler().listing_urls(seeds) == seeds


def test_listing_urls_does_not_alias_the_callers_list() -> None:
    # A crawler mutating the source definition's seed list would corrupt the
    # next run against the same configuration.
    seeds = ["https://library.abb.com/a"]
    returned = AbbCrawler().listing_urls(seeds)
    returned.append("https://library.abb.com/b")

    assert seeds == ["https://library.abb.com/a"]


# --- the client --------------------------------------------------------------


def test_the_client_identifies_itself() -> None:
    # A crawler that will not say who it is has already decided it might not
    # be welcome.
    with http_client(user_agent="PanelPilotBot", resolve=lambda _host: ["93.184.216.34"]) as client:
        assert client.headers["User-Agent"] == "PanelPilotBot"


def test_the_client_leaves_redirects_to_the_crawl_loop() -> None:
    # Download portals redirect to a CDN path constantly, so redirects must be
    # followed -- but by the crawl loop, one checked hop at a time. Left to
    # httpx, a manufacturer page answering 302 to the cloud metadata address
    # was fetched and staged. The crawler tests cover the following itself.
    with http_client(user_agent="PanelPilotBot", resolve=lambda _host: ["93.184.216.34"]) as client:
        assert client.follow_redirects is False


# --- bounded reads ------------------------------------------------------------


def _streamed(body: bytes, headers: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_read_limited_returns_a_body_within_its_caps() -> None:
    with (
        _streamed(b"abc") as client,
        client.stream("GET", "https://library.abb.com/a.pdf") as response,
    ):
        assert read_limited(response, max_bytes=3, deadline=float("inf")) == b"abc"


def test_read_limited_refuses_a_declared_length_over_the_cap() -> None:
    # Refused on the header, before a byte of the body is read.
    with (
        _streamed(b"abcd") as client,
        client.stream("GET", "https://library.abb.com/a.pdf") as response,
        pytest.raises(ResponseLimitError) as caught,
    ):
        read_limited(response, max_bytes=3, deadline=float("inf"))
    assert caught.value.reason == "too-large"


def test_read_limited_counts_decompressed_bytes() -> None:
    # The reported bug: a 204 KB gzip body became 200 MB in memory. Here the
    # encoded body -- and so Content-Length -- is tiny, and only a count over
    # what httpx actually hands back catches it.
    bomb = gzip.compress(b"\0" * 1_000_000)
    assert len(bomb) < 10_000
    with (
        _streamed(bomb, {"Content-Encoding": "gzip"}) as client,
        client.stream("GET", "https://library.abb.com/a.pdf") as response,
        pytest.raises(ResponseLimitError) as caught,
    ):
        read_limited(response, max_bytes=100_000, deadline=float("inf"))
    assert caught.value.reason == "too-large"


def test_read_limited_abandons_a_read_past_its_deadline() -> None:
    with (
        _streamed(b"abc") as client,
        client.stream("GET", "https://library.abb.com/a.pdf") as response,
        pytest.raises(ResponseLimitError) as caught,
    ):
        read_limited(response, max_bytes=100, deadline=5.0, now=lambda: 10.0)
    assert caught.value.reason == "timed-out"


@pytest.mark.parametrize(
    ("crawler", "suffix"),
    [(SiemensCrawler(), "siemens.com"), (AbbCrawler(), "abb.com"), (SchneiderCrawler(), "se.com")],
)
def test_each_crawler_names_its_own_domain(crawler: SourceCrawler, suffix: str) -> None:
    # The same suffix gates discovered links and, through `url_guard`, every
    # URL and redirect the crawl loop requests.
    assert crawler.host_suffix == suffix


@pytest.mark.parametrize(
    "host",
    [
        "evil-abb.com",
        "abb.com.attacker.net",
        "notabb.com",
        "abb.com.evil.co",
    ],
)
def test_a_lookalike_host_is_not_mistaken_for_the_source(host: str) -> None:
    # A bare `endswith` is not a domain check, and every earlier off-host test
    # used an obviously-foreign domain (`cdn.example.net`) that shares no
    # substring — so nothing constrained the *shape* of the check. A review
    # weakened it to a substring match and all 77 tests still passed.
    #
    # These hosts would then be fetched under a robots.txt policy fetched for
    # a different domain, with redirects followed.
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals", html=page(f"https://{host}/a.pdf")
    )

    assert found == []


def test_a_short_suffix_does_not_swallow_unrelated_domains() -> None:
    # `se.com` is short enough that a substring or bare-suffix check admits
    # real, entirely unrelated domains.
    found = SchneiderCrawler().extract_documents(
        listing_url="https://www.se.com/manuals", html=page("https://parse.com/a.pdf")
    )

    assert found == []


@pytest.mark.parametrize("host", ["abb.com", "library.abb.com", "new.library.abb.com"])
def test_the_domain_and_its_subdomains_are_admitted(host: str) -> None:
    # The check must not be so strict it drops the source's own CDN subdomain.
    found = AbbCrawler().extract_documents(
        listing_url="https://library.abb.com/manuals", html=page(f"https://{host}/a.pdf")
    )

    assert [d.url for d in found] == [f"https://{host}/a.pdf"]
