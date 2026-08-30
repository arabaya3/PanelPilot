"""Hand-curated document URLs, for sources whose discovery is blocked.

Every manufacturer portal investigated so far fails the "real links in served
HTML" bar that decides whether a crawler can find documents on its own:

* **ABB Library** serves a 1429-byte SPA shell — no PDF hrefs at all.
* **Siemens SIOS** returns 403 to any non-browser client, at the CDN edge.
* **Schneider** disallows every path its documents live at, in robots.txt.

ABB is the interesting case, and the reason this module exists. Its *discovery*
is blocked, but the PDFs themselves sit on a plain asset host
(``library.e.abb.com``) that serves them to an ordinary client and whose
robots.txt permits ``/public/``. So the only missing piece is the list of URLs
— which a person can assemble by hand from a browser, once, and which does not
go stale the way a scraped listing would.

**These are addresses, not content.** Nothing here asserts what a document
says. A URL in this list still gets fetched, hashed, parsed, chunked, embedded,
staged and put in front of a human reviewer exactly like a crawled one; it
skips discovery and nothing else. In particular it does not skip robots.txt,
which is checked per document at fetch time.

Adding an entry means having opened it and confirmed three things: it resolves
to a PDF, it is the document the title claims, and the host's robots.txt allows
it. The verified date records when that was last true — a URL that 404s later
is a broken entry, not a silent gap, because the crawl reports it as an
unreachable outcome.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownDocument:
    """One manually verified document URL.

    Attributes:
        source_id: The registered source this belongs to, so the crawl applies
            the same allow-list and manufacturer attribution it would for a
            discovered document.
        url: Direct URL of the PDF itself, not a landing page.
        title: What the document is, for the staging record and the review
            queue. Taken from the document's own cover page.
        verified: ISO date the URL was last confirmed to resolve to this PDF.
    """

    source_id: str
    url: str
    title: str
    verified: str


#: The curated list.
#:
#: Small and explicit on purpose. This is a stopgap for sources whose discovery
#: is blocked, not a substitute for crawling — a list that grew to hundreds of
#: hand-maintained URLs would be a worse version of the crawler, with the
#: staleness problem moved from code into a literal.
KNOWN_DOCUMENTS: tuple[KnownDocument, ...] = (
    KnownDocument(
        source_id="abb",
        url=(
            "https://library.e.abb.com/public/b24019aa640f45bf83a14f04f53691fe/"
            "EN_ACS880_Primary_FW_manual_V_A4.pdf"
        ),
        title="ACS880 primary control program firmware manual",
        verified="2026-08-29",
    ),
    KnownDocument(
        source_id="abb",
        url=("https://library.e.abb.com/public/1d1d7475e72c4a2cb0c94743b0849cec/ABCF270x_en.pdf"),
        title="ACS880 brake control program firmware manual",
        verified="2026-08-29",
    ),
)


def documents_for(source_id: str) -> tuple[KnownDocument, ...]:
    """Return the curated documents registered for one source.

    Args:
        source_id: The source identifier.

    Returns:
        Its known documents, empty when the source has none.
    """
    return tuple(doc for doc in KNOWN_DOCUMENTS if doc.source_id == source_id)


def urls_for(source_id: str) -> list[str]:
    """Return just the URLs for one source, ready to pass to a crawl.

    Args:
        source_id: The source identifier.

    Returns:
        The document URLs, in registration order.
    """
    return [doc.url for doc in documents_for(source_id)]
