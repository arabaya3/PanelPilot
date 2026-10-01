"""Hand-curated document URLs, for sources whose discovery is blocked.

Every manufacturer portal investigated so far fails the "real links in served
HTML" bar that decides whether a crawler can find documents on its own:

* **ABB Library** serves a 1429-byte SPA shell — no PDF hrefs at all.
* **Siemens SIOS** returns 403 to any non-browser client, at the CDN edge --
  but, like ABB, its PDFs sit on a plain asset host
  (``cache.industry.siemens.com``) whose robots.txt permits ``/dl/files/``,
  and which serves them to our user agent. So Siemens is curated here too.
* **Invertek** (admin.invertekdrives.com disallows everything), **Lenze**
  (download.lenze.com answers robots.txt with 403), and **Phoenix
  Contact**, **Weidmüller** and **B&R** (403 to our client) have no entries
  either; their manuals go in by hand.
* **Delta** serves its download center as a JavaScript application with no
  document links in the HTML, and its robots.txt disallows the ``/api/``
  that application reads from; like Schneider, it has no entries here.
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
    # Danfoss. assets.danfoss.com's robots.txt allows everything; each opened,
    # confirmed a PDF served to our user agent, title read off its cover.
    KnownDocument(
        source_id="danfoss",
        url="https://assets.danfoss.com/documents/latest/569514/AU275636650261en-003301.pdf",
        title="VLT AutomationDrive FC 301/FC 302 Programming Guide, software 10.00 and 49.1x",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="danfoss",
        url="https://assets.danfoss.com/documents/latest/275726/AQ267037727118en-000101.pdf",
        title="VLT AutomationDrive FC 301/FC 302 Operating Guide, 0.25-75 kW",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="danfoss",
        url="https://assets.danfoss.com/documents/latest/271209/AQ276736419659en-000101.pdf",
        title="VLT Micro Drive FC 51 Operating Guide",
        verified="2026-09-30",
    ),
    # Yaskawa. www.yaskawa.com's robots.txt disallows its download *pages*
    # (/downloads/-/document/...) and getAttachment's cmd=docurl form, not the
    # cmd=documents attachments used here; each opened, confirmed a PDF, title
    # read off its cover.
    KnownDocument(
        source_id="yaskawa",
        url=(
            "https://www.yaskawa.com/delegate/getAttachment?documentId=SIEPC71061752"
            "&cmd=documents&documentName=SIEPC71061752.pdf"
        ),
        title="GA500 Drive Versatile Compact Type Technical Reference (SIEPC71061752)",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="yaskawa",
        url=(
            "https://www.yaskawa.com/delegate/getAttachment?documentId=SIEPC71061737"
            "&cmd=documents&documentName=SIEPC71061737.pdf"
        ),
        title="GA800 Drive AC Drive for Industrial Applications Technical Reference (SIEPC71061737)",
        verified="2026-09-30",
    ),
    KnownDocument(
        source_id="yaskawa",
        url=(
            "https://www.yaskawa.com/delegate/getAttachment?documentId=SIEPC71060618"
            "&cmd=documents&documentName=SIEPC71060618.pdf"
        ),
        title="YASKAWA AC Drive-V1000 Compact Vector Control Drive Technical Manual",
        verified="2026-09-30",
    ),
    # Rockwell Automation. literature.rockwellautomation.com's robots.txt redirects
    # to an ordinary page on www.rockwellautomation.com, which states no rules;
    # each opened, confirmed a PDF served to our user agent, titled from its cover.
    KnownDocument(
        source_id="rockwell",
        url="https://literature.rockwellautomation.com/idc/groups/literature/documents/um/520-um001_-en-e.pdf",
        title="PowerFlex 520-series Adjustable Frequency AC Drive User Manual (520-UM001)",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="rockwell",
        url="https://literature.rockwellautomation.com/idc/groups/literature/documents/pm/750-pm001_-en-p.pdf",
        title="PowerFlex 750-Series AC Drives Programming Manual (750-PM001)",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="rockwell",
        url="https://literature.rockwellautomation.com/idc/groups/literature/documents/um/1769-um021_-en-p.pdf",
        title="CompactLogix 5370 Controllers User Manual (1769-UM021)",
        verified="2026-10-01",
    ),
    # Mitsubishi Electric. dl.mitsubishielectric.com's robots.txt redirects to
    # www.mitsubishielectric.com's, which disallows only two unrelated pages.
    KnownDocument(
        source_id="mitsubishi",
        url="https://dl.mitsubishielectric.com/dl/fa/document/manual/inv/ib0600865eng/ib0600865engt.pdf",
        title="FR-E800 Instruction Manual (Connection), IB-0600865ENG-T",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="mitsubishi",
        url="https://dl.mitsubishielectric.com/dl/fa/document/manual/inv/ib0600868eng/ib0600868engu.pdf",
        title="FR-E800 Instruction Manual (Function), IB-0600868ENG-U",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="mitsubishi",
        url="https://dl.mitsubishielectric.com/dl/fa/document/manual/inv/ib0600874eng/ib0600874engn.pdf",
        title="FR-E800 Instruction Manual (Maintenance), IB-0600874ENG-N",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="mitsubishi",
        url="https://dl.mitsubishielectric.com/dl/fa/document/manual/inv/ib0600503eng/ib0600503engp.pdf",
        title="FR-A800 Instruction Manual (Detailed), IB-0600503ENG-P",
        verified="2026-10-01",
    ),
    # WEG. static.weg.net has no robots.txt (404), which RFC 9309 reads as no
    # restrictions.
    KnownDocument(
        source_id="weg",
        url="https://static.weg.net/medias/downloadcenter/hb7/h52/WEG-CFW500-programming-manual-10006739425-en.pdf",
        title="Frequency Inverter CFW500 V4.1X Programming Manual",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="weg",
        url="https://static.weg.net/medias/downloadcenter/h73/ha4/WEG-CFW500-modbus-rtu-manual-10002253377-en.pdf",
        title="CFW500 Modbus RTU User's Guide",
        verified="2026-10-01",
    ),
    # Omron. assets.omron.eu and files.omron.eu both allow everything.
    KnownDocument(
        source_id="omron",
        url="https://assets.omron.eu/downloads/latest/manual/en/i570_mx2_users_manual_en.pdf",
        title="MX2 Inverter User's Manual (I570)",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="omron",
        url="https://files.omron.eu/downloads/latest/manual/en/w578_nx-series_nx1p2_cpu_unit_hardware_users_manual_en.pdf",
        title="NX-series NX1P2 CPU Unit Hardware User's Manual (W578)",
        verified="2026-10-01",
    ),
    # LS Electric. www.ls-electric.com disallows only its Korean upload and
    # download paths; sol.ls-electric.com allows all.
    KnownDocument(
        source_id="lselectric",
        url=(
            "https://www.ls-electric.com/upload/customer/download/"
            "925da566-373d-4a8d-bf96-06eece71148b/S100_E.pdf"
        ),
        title="LSLV S100 High-Performance Standard Drive Manual",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="lselectric",
        url=(
            "https://www.ls-electric.com/upload/customer/download/"
            "45cf9cc9-e4b9-4dc1-b0ca-d9ea3c51d82f/iG5A_simple%20manual.pdf"
        ),
        title="SV-iG5A Inverter Manual",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="lselectric",
        url=(
            "https://sol.ls-electric.com/uploads/document/16407659210700/"
            "iG5A%20Troubleshooting%20manual_ENG_Rev1.0_150417.pdf"
        ),
        title="iG5A 0.4-22 kW AC Variable Speed Drive Troubleshooting Manual, Rev 1.0",
        verified="2026-10-01",
    ),
    # Inovance. www.inovance.eu's robots.txt does not cover /fileadmin/downloads/.
    KnownDocument(
        source_id="inovance",
        url=(
            "https://www.inovance.eu/fileadmin/downloads/Manuals/EN/MD500-PLUS/"
            "MD500-PLUS_Software_Guide_EN_A04_19011580.pdf"
        ),
        title="MD500-PLUS Series AC Drive Software Guide, A04",
        verified="2026-10-01",
    ),
    # Hitachi. www.hitachi-iesa.com disallows only CMS internals.
    KnownDocument(
        source_id="hitachi",
        url=(
            "https://www.hitachi-iesa.com/sites/default/files/supportdocs/"
            "WJ200_Instruction_NT325X.pdf"
        ),
        title="WJ200 Series Inverter Instruction Manual (NT325X)",
        verified="2026-10-01",
    ),
    # Fuji Electric. americas.fujielectric.com disallows its admin and dated
    # archive paths, not /wp-content/uploads/.
    KnownDocument(
        source_id="fuji",
        url=(
            "https://americas.fujielectric.com/wp-content/uploads/2017/02/"
            "FRENIC-Ace-Instruction-Manual-INR-SI47-1733f-E.pdf"
        ),
        title="FRENIC-Ace High Performance Inverter Instruction Manual (INR-SI47-1733f-E)",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="fuji",
        url=(
            "https://americas.fujielectric.com/wp-content/uploads/2017/01/" "INR-SI47-1729b-E.pdf"
        ),
        title="FRENIC-Mini (C2) Compact Inverter Instruction Manual (INR-SI47-1729b-E)",
        verified="2026-10-01",
    ),
    # Nidec / Control Techniques. moen.nidec.com's robots.txt has no rules.
    KnownDocument(
        source_id="nidec",
        url=(
            "https://moen.nidec.com/drives/-/media/Project/Nidec/ControlTechniques/"
            "Documents/Technical/Control-Getting-Started-Guides/Unidrive-M/"
            "M700-701-HS70-71/"
            "Unidrive-M700-M701-and-HS70-71-Control-Getting-Started-Guide-EN.pdf"
        ),
        title="Unidrive M700/M701 and HS70/HS71 Control Getting Started Guide, Issue 4",
        verified="2026-10-01",
    ),
    KnownDocument(
        source_id="nidec",
        url=(
            "https://moen.nidec.com/drives/-/media/Project/Nidec/ControlTechniques/"
            "Documents/Technical/Short-Form-Guides/Unidrive-M/"
            "Unidrive-M700-M701-HS70-HS71-Short-Form-Guide-EN.pdf"
        ),
        title="Unidrive M700/M701/HS70/HS71 Short Form Guide, Frame 3 to 11",
        verified="2026-10-01",
    ),
    # SEW-EURODRIVE. download.sew-eurodrive.com has no robots.txt (404).
    KnownDocument(
        source_id="sew",
        url="https://download.sew-eurodrive.com/download/pdf/20045344.pdf",
        title="MOVITRAC LTE-B Operating Instructions, Edition 11/2012",
        verified="2026-10-01",
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
