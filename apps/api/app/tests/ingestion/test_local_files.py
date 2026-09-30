"""Tests for `app/ingestion/local_files.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.crawler import content_hash
from app.ingestion.local_files import SOURCES_FILE, read_folder, read_sources

_HEADER = "filename,title,document_reference,revision,direct_download_url,product_page_url\n"
_PAGE = "https://www.se.com/us/en/download/document/NVE41295/"


def _folder(tmp_path: Path, csv: str, **files: bytes) -> Path:
    (tmp_path / SOURCES_FILE).write_text(_HEADER + csv, encoding="utf-8")
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    return tmp_path


def test_a_listed_pdf_becomes_a_document_with_its_title_and_page(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        f"atv320.pdf,ATV320 Programming Manual,NVE41295,06,,{_PAGE}\n",
        **{"atv320.pdf": b"%PDF-1.7 manual"},
    )

    batch = read_folder(folder, source_id="schneider", known_hashes=set())

    (document,) = batch.result.documents
    assert document.title == "ATV320 Programming Manual"
    assert document.url == _PAGE
    assert document.source_id == "schneider"
    assert batch.payloads[document.id] == b"%PDF-1.7 manual"
    assert batch.refused == {}


def test_the_direct_download_is_preferred_to_the_product_page(tmp_path: Path) -> None:
    direct = "https://download.schneider-electric.com/files?p_Doc_Ref=NVE41295"
    folder = _folder(tmp_path, f"a.pdf,T,NVE41295,06,{direct},{_PAGE}\n", **{"a.pdf": b"%PDF-1"})

    (document,) = read_folder(folder, source_id="schneider", known_hashes=set()).result.documents
    assert document.url == direct


def test_a_file_that_cannot_be_cited_or_read_is_refused_by_name(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        f"no-url.pdf,No URL,X,1,,\nhtml.pdf,An error page,X,1,,{_PAGE}\n",
        **{"unlisted.pdf": b"%PDF-1", "no-url.pdf": b"%PDF-1", "html.pdf": b"<html>403</html>"},
    )

    batch = read_folder(folder, source_id="schneider", known_hashes=set())

    assert batch.result.documents == []
    assert set(batch.refused) == {"unlisted.pdf", "no-url.pdf", "html.pdf"}
    assert batch.refused["html.pdf"] == "not a PDF"


def test_a_file_already_staged_is_skipped_as_unchanged(tmp_path: Path) -> None:
    body = b"%PDF-1.7 same"
    folder = _folder(tmp_path, f"a.pdf,T,X,1,,{_PAGE}\n", **{"a.pdf": body})

    batch = read_folder(folder, source_id="schneider", known_hashes={content_hash(body)})

    assert batch.result.documents == []
    assert [o.skipped_reason for o in batch.result.outcomes] == ["unchanged"]


def test_no_sources_file_means_no_rows(tmp_path: Path) -> None:
    assert read_sources(tmp_path) == {}


def test_the_committed_schneider_list_names_every_manual_it_expects() -> None:
    # `data/schneider/sources.csv` is what a person downloads against. A row
    # this reader drops would refuse its manual after the download.
    folder = Path(__file__).resolve().parents[3] / "data" / "schneider"
    rows = read_sources(folder)

    assert len(rows) == 6
    assert all(row.url.startswith("https://www.se.com/") for row in rows.values())
