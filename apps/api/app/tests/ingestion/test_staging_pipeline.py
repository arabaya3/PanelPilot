"""Tests for the staging pipeline.

Two properties are defended here, and they are different in kind.

The first is structural: **this module cannot reach an index at all.** Not
"does not write production" — cannot resolve, open or write *any* index, by
any spelling. The repository's own architecture test enforces that, and it
caught an earlier version of this module importing ``index_chunk`` and
``IndexTarget.STAGING``, which reads as safe because it never names
production. It is not: a module holding an ``IndexTarget`` reaches production
by ordinal or by ``getattr``, which is how a name-based check was defeated in
review. So staging prepares chunk bodies and hands them back, and the caller
in ``app.domain`` decides where they go.

The second is that a document which cannot be prepared completely is not
prepared at all. A chunk missing its page or source_url would surface later as
an answer nobody can trace back, and half-preparing a document puts exactly
that into the review queue looking finished.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.ingestion.staging_pipeline import StructureExtractor, prepare_documents
from app.ingestion.structure import extract_structure
from app.models.schemas.documents import CrawlResult, SourceDocument
from app.models.schemas.structure import BlockKind, StructuralBlock, StructureMap

WIDTH, HEIGHT = A4


def manual_pdf() -> bytes:
    """A small readable manual page."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(60, HEIGHT - 70, "3 Fault tracing")
    pdf.setFont("Helvetica", 10)
    for i, line in enumerate(
        [
            "The drive trips on overcurrent when the output current",
            "exceeds the limit set in parameter 30.17. Check the motor",
            "cable for damage before increasing the limit.",
        ]
    ):
        pdf.drawString(60, HEIGHT - 110 - i * 16, line)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def document(*, doc_id: str = "doc-1", data: bytes | None = None) -> SourceDocument:
    return SourceDocument(
        id=doc_id,
        source_id="abb",
        title="ACS880 Firmware Manual",
        url=f"https://library.abb.com/{doc_id}.pdf",
        content_hash=f"{doc_id:0>64}"[:64],
        # The crawler hands over decoded bytes; the extractor reads the real
        # file, so this field is not what structure comes from.
        text="",
    )


def crawl(*documents: SourceDocument) -> CrawlResult:
    return CrawlResult(source_id="abb", documents=list(documents), outcomes=[])


def extractor_for(mapping: dict[str, bytes]) -> StructureExtractor:
    """An extractor reading each document's real PDF bytes."""

    def extract(doc: SourceDocument) -> StructureMap:
        return extract_structure(mapping[doc.id], document_id=doc.id)

    return extract


# --- the structural guarantee ------------------------------------------------


def test_the_module_cannot_reach_any_index() -> None:
    # Asserted on the source, not on behaviour: a runtime test only shows the
    # paths it exercised did not write, whereas this shows none could.
    #
    # Parsed rather than grepped. The module docstring names both PRODUCTION
    # and IndexTarget while explaining why it must not hold them, and a
    # substring search cannot tell an explanation from an instruction — this
    # test failed that way on its first run against a module that was correct.
    tree = ast.parse(Path("app/ingestion/staging_pipeline.py").read_text(encoding="utf-8"))
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    index_capable = {
        "index_chunk",
        "ensure_index",
        "resolve_index",
        "get_client",
        "IndexTarget",
        "PRODUCTION",
        "STAGING",
    }
    assert not (referenced & index_capable)


# --- staging a document ------------------------------------------------------


def test_a_readable_document_is_staged() -> None:
    data = manual_pdf()
    doc = document(data=data)

    result, _bodies = prepare_documents(crawl(doc), extract_structure=extractor_for({doc.id: data}))

    assert result.staged_document_ids == [doc.id]
    assert result.failures == {}


def test_an_unreadable_document_fails_that_document_not_the_run() -> None:
    # A malformed PDF in a library of hundreds should cost that document, not
    # the whole night's crawl.
    good_data = manual_pdf()
    good = document(doc_id="good", data=good_data)
    bad = document(doc_id="bad")

    result, _ = prepare_documents(
        crawl(bad, good),
        extract_structure=extractor_for({good.id: good_data, bad.id: b"not a pdf"}),
    )

    assert result.staged_document_ids == [good.id]
    assert "bad" in result.failures


def test_a_document_producing_no_chunks_is_recorded_as_a_failure() -> None:
    doc = document()

    def empty(_doc: SourceDocument) -> StructureMap:
        return StructureMap(blocks=[])

    result, _ = prepare_documents(crawl(doc), extract_structure=empty)

    assert result.staged_document_ids == []
    assert doc.id in result.failures


def test_a_chunk_missing_a_citation_field_stops_the_whole_document() -> None:
    # Refused before writing anything, so a document does not land
    # half-staged. A partially-indexed manual in the review queue looks
    # finished, and the missing half is invisible precisely because it is
    # missing.
    doc = document()

    def blank_section(_doc: SourceDocument) -> StructureMap:
        return StructureMap(
            blocks=[
                StructuralBlock(
                    kind=BlockKind.PARAGRAPH,
                    text="A paragraph with no section above it.",
                    page=1,
                    # Blank rather than FRONT_MATTER: what an extractor bug
                    # would produce, and what must not reach the index.
                    section=" ",
                )
            ]
        )

    result, _ = prepare_documents(crawl(doc), extract_structure=blank_section)

    assert result.staged_document_ids == []
    assert doc.id in result.failures


def test_every_staged_chunk_is_marked_pending_verification() -> None:
    # None becomes retrievable by answer generation as a result of staging.
    data = manual_pdf()
    doc = document(data=data)
    _, prepared = prepare_documents(crawl(doc), extract_structure=extractor_for({doc.id: data}))
    bodies = prepared[doc.id]

    assert bodies
    assert all(body["verification_status"] == "pending" for body in bodies)


def test_the_model_is_recorded_as_unknown_rather_than_guessed() -> None:
    # A wrong model on a chunk is a citation pointing at the wrong equipment.
    # The verification queue is where a reviewer supplies it.
    data = manual_pdf()
    doc = document(data=data)
    _, prepared = prepare_documents(crawl(doc), extract_structure=extractor_for({doc.id: data}))
    bodies = prepared[doc.id]

    assert all(body["model"] == "unknown" for body in bodies)


def test_the_brand_defaults_to_the_source_when_not_supplied() -> None:
    data = manual_pdf()
    doc = document(data=data)
    _, prepared = prepare_documents(crawl(doc), extract_structure=extractor_for({doc.id: data}))
    bodies = prepared[doc.id]

    assert all(body["brand"] == "abb" for body in bodies)
