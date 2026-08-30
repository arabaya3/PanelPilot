"""Tests for `app/ingestion/known_documents.py`.

Mirrors the module 1:1.

This list is a hand-maintained literal, which is exactly the kind of thing that
rots quietly: a typo in a URL, a duplicate entry, or a document attributed to a
source that no longer exists all look fine on the page and fail at crawl time —
or worse, succeed and stage the wrong thing.

Nothing here reaches the network. Whether a URL still resolves is a fact about
the internet today, not about this code, and a test that fetched would fail in
CI for reasons no commit caused. The URLs were verified by hand when added and
the entry records that date; what these tests hold is the shape, the
attribution, and the properties a crawl depends on.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.ingestion.known_documents import KNOWN_DOCUMENTS, documents_for, urls_for
from app.ingestion.sources import CRAWLERS


def test_every_document_names_a_registered_source() -> None:
    """A curated URL for an unregistered source can never be crawled.

    `create_crawl_job` refuses anything not on the allow-list, so such an entry
    would sit here looking staged-and-ready while being unreachable.
    """
    for document in KNOWN_DOCUMENTS:
        assert document.source_id in CRAWLERS, f"{document.url} names unknown source"


def test_every_url_is_absolute_and_https() -> None:
    """The crawler fetches these directly.

    The crawler fetches these directly; a relative URL has no host to
    resolve against, and plaintext http would be a downgrade nobody asked
    for.
    """
    for document in KNOWN_DOCUMENTS:
        parsed = urlparse(document.url)
        assert parsed.scheme == "https", f"{document.url} is not https"
        assert parsed.netloc, f"{document.url} has no host"


def test_every_url_points_at_a_document_not_a_landing_page() -> None:
    """The whole point is bypassing discovery.

    A landing page would put us back where we started — ABB's are JavaScript
    applications that serve no links at all.
    """
    for document in KNOWN_DOCUMENTS:
        assert document.url.lower().endswith(".pdf"), f"{document.url} is not a PDF URL"


def test_there_are_no_duplicate_urls() -> None:
    """A duplicate stages nothing twice — content hashing catches it — but it.

    A duplicate stages nothing twice — content hashing catches it — but it
    does waste a fetch and makes the list harder to read.
    """
    urls = [document.url for document in KNOWN_DOCUMENTS]

    assert len(urls) == len(set(urls))


def test_every_document_carries_a_real_title() -> None:
    """The title reaches the review queue.

    A reviewer deciding whether to promote a chunk needs to know which manual
    it came from; a filename or an empty string tells them nothing.
    """
    for document in KNOWN_DOCUMENTS:
        assert document.title.strip(), f"{document.url} has no title"
        assert not document.title.lower().endswith(
            ".pdf"
        ), f"{document.url} uses its filename as a title"


def test_every_document_records_when_it_was_verified() -> None:
    """So a stale entry is visible as stale rather than as a mystery 404."""
    for document in KNOWN_DOCUMENTS:
        assert len(document.verified) == 10, f"{document.url} has no ISO verified date"
        assert document.verified.count("-") == 2


def test_documents_for_filters_by_source() -> None:
    abb = documents_for("abb")

    assert abb
    assert all(document.source_id == "abb" for document in abb)


def test_documents_for_an_unknown_source_is_empty() -> None:
    """Not an error.

    Not an error: most sources legitimately have no curated list, and a
    crawl asking for one must not fail because of that.
    """
    assert documents_for("no-such-source") == ()


def test_urls_for_returns_just_the_urls() -> None:
    urls = urls_for("abb")

    assert urls == [document.url for document in documents_for("abb")]


def test_abb_has_curated_documents() -> None:
    """The case this module was built for.

    ABB's listing is a JavaScript application serving no links, while its PDFs
    sit on an asset host that permits `/public/`. If this ever empties, the
    fallback has silently stopped existing.
    """
    assert urls_for("abb"), "ABB has no curated documents"
