"""Hand-curated document URLs, for sources whose discovery is blocked.

Every manufacturer portal investigated so far fails the "real links in served
HTML" bar that decides whether a crawler can find documents on its own:

* **ABB Library** serves a 1429-byte SPA shell — no PDF hrefs at all.
* **Siemens SIOS** returns 403 to any non-browser client, at the CDN edge --
  but, like ABB, its PDFs sit on a plain asset host
  (``cache.industry.siemens.com``) whose robots.txt permits ``/dl/files/``,
  and which serves them to our user agent. So Siemens is curated here too.
* **Schneider** answers our user agent with 403 at the edge, for its document
  pages on ``www.se.com`` and for ``robots.txt`` on its download host alike
  (checked 2026-09-30). Presenting a browser's user agent to get past that
  would be evading a refusal, not crawling, so Schneider has no entries: its
  documents need adding by hand, from a browser, by someone entitled to.

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
    # Siemens. Each opened, confirmed a PDF served to our user agent, its title
    # read off the document itself, and cache.industry.siemens.com's robots.txt
    # checked to allow /dl/files/.
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/922/109817922/att_1133717/v1/"
            "G120C_list_man_0223_en-US.pdf"
        ),
        title="SINAMICS G120C List Manual, edition 02/2023",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/919/109817919/att_1133702/v1/"
            "G120_CU240BE-2_list_man_0223_en-US.pdf"
        ),
        title="SINAMICS G120 Control Units CU240B-2/CU240E-2 List Manual, edition 02/2023",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/920/109817920/att_1133705/v1/"
            "G120_CU230P-2_List_Manual_0223_en-US.pdf"
        ),
        title="SINAMICS G120 CU230P-2 Control Units List Manual, edition 02/2023",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/111/109811111/att_1105033/v1/"
            "V20_op_instr_0522_en-US.pdf"
        ),
        title="SINAMICS V20 Low voltage converters Operating Instructions",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/915/109977915/att_1309695/v1/"
            "V20_cmpct_op_instr_1224_en-US.pdf"
        ),
        title="SINAMICS V20 Converter Compact Operating Instructions, 12/2024",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="siemens",
        url=(
            "https://cache.industry.siemens.com/dl/files/293/109988293/att_1327503/v1/"
            "S71200_G2_system_manual_en-US_en-US.pdf"
        ),
        title="SIMATIC S7-1200 G2 System Manual",
        verified="2026-09-30",
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


def title_for(url: str) -> str | None:
    """Return a curated document's title, if the URL is one.

    Args:
        url: A document URL.

    Returns:
        Its curated title, or ``None`` for a URL not on the list.
    """
    for document in KNOWN_DOCUMENTS:
        if document.url == url:
            return document.title
    return None


def urls_for(source_id: str) -> list[str]:
    """Return just the URLs for one source, ready to pass to a crawl.

    Args:
        source_id: The source identifier.

    Returns:
        The document URLs, in registration order.
    """
    return [doc.url for doc in documents_for(source_id)]
