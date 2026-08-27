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

import pytest

from app.ingestion.sources import (
    CRAWLERS,
    AbbCrawler,
    SchneiderCrawler,
    SiemensCrawler,
    SourceCrawler,
    crawler_for,
    http_client,
)


def page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{h}">Manual</a>' for h in hrefs) + "</body></html>"


# --- the registry ------------------------------------------------------------


def test_the_three_named_sources_are_registered() -> None:
    assert set(CRAWLERS) == {"siemens", "abb", "schneider"}


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
    with http_client(user_agent="PanelPilotBot") as client:
        assert client.headers["User-Agent"] == "PanelPilotBot"


def test_the_client_follows_redirects() -> None:
    # Download portals redirect to a CDN path constantly; not following would
    # register every document as unreachable.
    with http_client(user_agent="PanelPilotBot") as client:
        assert client.follow_redirects is True
