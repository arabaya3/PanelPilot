"""Per-source crawlers, behind one interface.

The task asks for "one function per source implementing a shared
``SourceCrawler`` interface, so adding a fourth brand later means implementing
one new class, not touching shared pipeline code". That is the whole design
constraint here, and it is worth taking literally: everything that differs
between Siemens, ABB and Schneider lives in a subclass, and everything that is
the same — robots, rate limiting, hashing, change detection — lives in the
crawler that drives them.

What differs per source is narrow and predictable: how you find the list of
documents, and how you recognise a document URL when you see one. Those are the
two methods below. A fourth brand implements them and is done.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

# Document formats worth ingesting. Manufacturer documentation is
# overwhelmingly PDF; HTML pages on these portals are navigation, not content.
DOCUMENT_SUFFIXES = (".pdf",)


@dataclass(frozen=True)
class DiscoveredDocument:
    """One document a source listing pointed at.

    Attributes:
        url: Absolute URL of the document itself.
        title: Best available title, for the staging record.
    """

    url: str
    title: str


class SourceCrawler(ABC):
    """One manufacturer documentation source.

    Subclasses describe *where* documents are and *what counts as one*. They do
    not fetch, hash, rate-limit or stage anything — that belongs to the shared
    crawl loop, which is what keeps a fourth brand from having to touch it.
    """

    #: Matches the ``source_id`` on a ``SourceDefinition``.
    source_id: str

    #: Shown in logs and in the source-health record (BE-006).
    manufacturer: str

    @abstractmethod
    def listing_urls(self, seed_urls: list[str]) -> list[str]:
        """Return the pages that list documents for this source.

        Args:
            seed_urls: Configured entry points for the source.

        Returns:
            Absolute URLs of listing pages to scan. Often the seeds
            themselves; a source paginating its library expands them here.
        """

    @abstractmethod
    def extract_documents(self, *, listing_url: str, html: str) -> list[DiscoveredDocument]:
        """Find the documents a listing page links to.

        Args:
            listing_url: The page these links were found on, for resolving
                relative hrefs.
            html: The listing page's markup.

        Returns:
            Documents discovered on this page, in page order.
        """


def _links(html: str) -> list[tuple[str, str]]:
    """Pull ``(href, text)`` pairs out of a page.

    A regex rather than a parser, deliberately: the alternative is another
    dependency for a job that is finding anchor hrefs on three known portals,
    and the crawler treats anything it extracts as untrusted regardless.

    Args:
        html: The page markup.

    Returns:
        Every anchor's href and its visible text, stripped of nested tags.
    """
    pattern = re.compile(
        r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    found: list[tuple[str, str]] = []
    for href, inner in pattern.findall(html):
        text = re.sub(r"<[^>]+>", " ", inner)
        found.append((href.strip(), " ".join(text.split())))
    return found


def _documents_from_links(
    *,
    listing_url: str,
    html: str,
    host_suffix: str | None = None,
) -> list[DiscoveredDocument]:
    """Shared link-to-document extraction.

    Args:
        listing_url: Page the links came from, for resolving relative hrefs.
        html: The page markup.
        host_suffix: When set, only keep documents served from this host
            suffix — a portal linking to a third-party mirror is not a source
            we have checked robots.txt for.

    Returns:
        Discovered documents, de-duplicated by URL, in page order.
    """
    seen: set[str] = set()
    documents: list[DiscoveredDocument] = []

    for href, text in _links(html):
        absolute = urljoin(listing_url, href)
        path = urlparse(absolute).path.lower()
        if not path.endswith(DOCUMENT_SUFFIXES):
            continue
        if host_suffix and not urlparse(absolute).netloc.endswith(host_suffix):
            # Deliberately dropped rather than followed. Every politeness
            # check we made was against *this* host's robots.txt.
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        # Falling back to the filename keeps a titleless link usable in the
        # review queue instead of showing a reviewer an empty row.
        title = text or urlparse(absolute).path.rsplit("/", 1)[-1]
        documents.append(DiscoveredDocument(url=absolute, title=title))

    return documents


class SiemensCrawler(SourceCrawler):
    """Siemens Industry Online Support / SiePortal."""

    source_id = "siemens"
    manufacturer = "Siemens"

    def listing_urls(self, seed_urls: list[str]) -> list[str]:
        """Return the seeds unchanged; this portal paginates server-side."""
        return list(seed_urls)

    def extract_documents(self, *, listing_url: str, html: str) -> list[DiscoveredDocument]:
        """Find PDFs linked from a listing page, on this host only."""
        return _documents_from_links(listing_url=listing_url, html=html, host_suffix="siemens.com")


class AbbCrawler(SourceCrawler):
    """ABB Library."""

    source_id = "abb"
    manufacturer = "ABB"

    def listing_urls(self, seed_urls: list[str]) -> list[str]:
        """Return the seeds unchanged; this portal paginates server-side."""
        return list(seed_urls)

    def extract_documents(self, *, listing_url: str, html: str) -> list[DiscoveredDocument]:
        """Find PDFs linked from a listing page, on this host only."""
        return _documents_from_links(listing_url=listing_url, html=html, host_suffix="abb.com")


class SchneiderCrawler(SourceCrawler):
    """Schneider Electric Download Center."""

    source_id = "schneider"
    manufacturer = "Schneider Electric"

    def listing_urls(self, seed_urls: list[str]) -> list[str]:
        """Return the seeds unchanged; this portal paginates server-side."""
        return list(seed_urls)

    def extract_documents(self, *, listing_url: str, html: str) -> list[DiscoveredDocument]:
        """Find PDFs linked from a listing page, on this host only."""
        return _documents_from_links(listing_url=listing_url, html=html, host_suffix="se.com")


#: The allow-list. A source not registered here cannot be crawled, which is
#: what makes "the source is not on the allow-list" a real check rather than a
#: docstring promise.
CRAWLERS: dict[str, SourceCrawler] = {
    crawler.source_id: crawler for crawler in (SiemensCrawler(), AbbCrawler(), SchneiderCrawler())
}


def crawler_for(source_id: str) -> SourceCrawler | None:
    """Look up the crawler for a source.

    Args:
        source_id: The source identifier.

    Returns:
        The registered crawler, or ``None`` if the source is not allow-listed.
    """
    return CRAWLERS.get(source_id)


def http_client(*, user_agent: str) -> httpx.Client:
    """Build the HTTP client the crawl loop uses.

    Args:
        user_agent: Identifies us to the source.

    Returns:
        A client that follows redirects and identifies itself.
    """
    return httpx.Client(
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        timeout=30.0,
    )
