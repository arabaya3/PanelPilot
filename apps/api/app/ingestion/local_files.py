"""Manuals supplied as files, for sources that refuse the crawler.

Schneider Electric answers the crawler with 403 at its edge, and that is not
worked around. A person can still download the manuals in a browser; this
reads such a folder so the files take the same road as crawled ones --
structure, chunks, staging, review. Nothing here writes anywhere.

A folder holds the PDFs and a ``sources.csv`` naming each one::

    filename,title,document_reference,revision,direct_download_url,product_page_url

A file with no row is refused: a chunk cites its ``source_url``, and a citation
that cannot be followed back to the manufacturer's page is not one.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.crawler import content_hash
from app.models.schemas.documents import CrawlOutcome, CrawlResult, SourceDocument

SOURCES_FILE = "sources.csv"

#: Every PDF starts with this. Checked so a mislabelled HTML error page saved
#: as ``.pdf`` is refused by name rather than failing deep in the parser.
PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class SourceRow:
    """One line of ``sources.csv``.

    Attributes:
        filename: The PDF's name in the folder.
        title: The manual's title, as a citation shows it.
        url: Where it can be found: the direct download if known, else the
            product page.
    """

    filename: str
    title: str
    url: str


@dataclass
class LocalBatch:
    """What a folder yielded.

    Attributes:
        result: The documents, shaped as a crawl's.
        payloads: Each document's bytes, keyed by document id.
        refused: File name to the reason it was not read.
    """

    result: CrawlResult
    payloads: dict[str, bytes] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)


def read_sources(folder: Path) -> dict[str, SourceRow]:
    """Read a folder's ``sources.csv``.

    Args:
        folder: The folder.

    Returns:
        Rows keyed by file name; empty when there is no ``sources.csv``.

    Rows missing a file name, a title or both URLs are left out: each is a
    manual that could not be cited, and its file is refused for that.
    """
    path = folder / SOURCES_FILE
    if not path.is_file():
        return {}
    rows: dict[str, SourceRow] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            name = (raw.get("filename") or "").strip()
            title = (raw.get("title") or "").strip()
            url = (raw.get("direct_download_url") or "").strip() or (
                raw.get("product_page_url") or ""
            ).strip()
            if name and title and url.startswith("https://"):
                rows[name] = SourceRow(filename=name, title=title, url=url)
    return rows


def read_folder(folder: Path, *, source_id: str, known_hashes: set[str]) -> LocalBatch:
    """Read every PDF in a folder as documents of one source.

    Args:
        folder: The folder holding the PDFs and ``sources.csv``.
        source_id: The allow-listed source they belong to.
        known_hashes: Hashes already staged; a file with one is skipped as
            unchanged, as a re-crawl would skip it.

    Returns:
        The documents, their bytes, and the files refused with their reasons.
    """
    rows = read_sources(folder)
    batch = LocalBatch(result=CrawlResult(source_id=source_id, documents=[], outcomes=[]))
    for path in sorted(folder.glob("*.pdf")):
        row = rows.get(path.name)
        if row is None:
            batch.refused[path.name] = f"no usable row in {SOURCES_FILE} (title and https URL)"
            continue
        body = path.read_bytes()
        if not body.startswith(PDF_MAGIC):
            batch.refused[path.name] = "not a PDF"
            continue
        digest = content_hash(body)
        if digest in known_hashes:
            batch.result.outcomes.append(
                CrawlOutcome(url=row.url, fetched=True, skipped_reason="unchanged")
            )
            continue
        known_hashes.add(digest)
        document_id = digest[:32]
        batch.payloads[document_id] = body
        batch.result.documents.append(
            SourceDocument(
                id=document_id,
                source_id=source_id,
                title=row.title,
                url=row.url,
                content_hash=digest,
                text=body.decode("utf-8", errors="replace"),
            )
        )
        batch.result.outcomes.append(CrawlOutcome(url=row.url, fetched=True))
    return batch
