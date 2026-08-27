"""Tests for PDF structure extraction.

Every fixture here is a **real PDF**, built with reportlab and read back
through the real parser. A hand-built ``StructureMap`` would test nothing that
matters: the entire question is whether layout can be read, and constructing
the answer by hand assumes it away.

What is being defended is the citation chain. A heading missed means a chunk
with no resolvable section; a table split means half a parameter table
presented as a whole one; a column-interleaved page means text indexed as
something the manual never said. All three are silent — they produce
plausible-looking chunks — which is why they are asserted rather than assumed.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.ingestion.structure import UnreadableDocumentError, extract_structure
from app.models.schemas.structure import FRONT_MATTER, BlockKind, StructuralBlock

WIDTH, HEIGHT = A4


class Page:
    """A tiny page builder, so fixtures read as layout rather than as calls."""

    def __init__(self, pdf: canvas.Canvas) -> None:
        """Start at the top of a fresh page."""
        self.pdf = pdf
        self.y = HEIGHT - 70

    def heading(self, text: str, size: float = 16) -> Page:
        self.pdf.setFont("Helvetica-Bold", size)
        self.pdf.drawString(60, self.y, text)
        self.y -= size * 2
        return self

    def body(self, text: str, size: float = 10) -> Page:
        self.pdf.setFont("Helvetica", size)
        self.pdf.drawString(60, self.y, text)
        self.y -= size * 1.6
        return self

    def ruled_table(self, rows: list[list[str]], *, col_w: float = 120) -> Page:
        top, left, row_h = self.y, 60.0, 20.0
        cols = len(rows[0])
        for r in range(len(rows) + 1):
            self.pdf.line(left, top - r * row_h, left + col_w * cols, top - r * row_h)
        for c in range(cols + 1):
            self.pdf.line(left + c * col_w, top, left + c * col_w, top - len(rows) * row_h)
        self.pdf.setFont("Helvetica", 9)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.pdf.drawString(left + c * col_w + 4, top - r * row_h - 14, text)
        self.y = top - (len(rows) + 1) * row_h
        return self

    def columns(self, left_lines: list[str], right_lines: list[str]) -> Page:
        self.pdf.setFont("Helvetica", 9)
        for i, text in enumerate(left_lines):
            self.pdf.drawString(60, self.y - i * 14, text)
        for i, text in enumerate(right_lines):
            self.pdf.drawString(330, self.y - i * 14, text)
        self.y -= max(len(left_lines), len(right_lines)) * 14
        return self


def build(*builders: Callable[[Page], object]) -> bytes:
    """Render pages into PDF bytes."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for builder in builders:
        builder(Page(pdf))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def kinds(blocks: list[StructuralBlock]) -> list[BlockKind]:
    return [block.kind for block in blocks]


# --- headings, from typography rather than wording ---------------------------


def test_a_larger_line_becomes_a_heading() -> None:
    data = build(lambda p: p.heading("3 Fault tracing", 18).body("The drive trips on overcurrent."))
    blocks = extract_structure(data).blocks

    assert blocks[0].kind is BlockKind.HEADING
    assert blocks[0].text == "3 Fault tracing"
    assert blocks[1].kind is BlockKind.PARAGRAPH


def test_heading_depth_follows_relative_size_not_absolute() -> None:
    # A manual typeset at 8pt body has 12pt headings; one at 12pt body does
    # not. An absolute threshold would find headings in one and none in the
    # other.
    data = build(
        lambda p: p.heading("3 Fault tracing", 20)
        .heading("3.2 Overcurrent", 13)
        .body("Check the motor cable.")
    )
    headings = [b for b in extract_structure(data).blocks if b.kind is BlockKind.HEADING]

    assert [h.text for h in headings] == ["3 Fault tracing", "3.2 Overcurrent"]
    assert headings[0].level is not None
    assert headings[1].level is not None
    assert headings[0].level < headings[1].level


def test_the_section_path_nests_and_then_unwinds() -> None:
    # The path is what makes a citation resolvable, so a subsection under the
    # wrong parent is a citation pointing at the wrong part of the manual.
    data = build(
        lambda p: p.heading("3 Fault tracing", 20)
        .heading("3.2 Overcurrent", 13)
        .body("Check the motor cable."),
        lambda p: p.heading("4 Maintenance", 20).body("Replace the fans every five years."),
    )
    blocks = extract_structure(data).blocks

    cable = next(b for b in blocks if "motor cable" in b.text)
    assert cable.section == "3 Fault tracing > 3.2 Overcurrent"

    fans = next(b for b in blocks if "fans" in b.text)
    assert fans.section == "4 Maintenance"


def test_content_before_any_heading_is_filed_as_front_matter() -> None:
    # Never blank: an empty section makes the chunk uncitable, which the
    # schema forbids and every real manual would trigger with its cover page.
    data = build(lambda p: p.body("Revision history: A, June 2024.").heading("1 Safety", 18))
    blocks = extract_structure(data).blocks

    assert blocks[0].section == FRONT_MATTER


# --- tables, from ruling lines rather than text shape ------------------------


def test_a_ruled_table_becomes_one_atomic_block() -> None:
    # The rule AI-001's atomic-block design exists for. Half a parameter table
    # is not a smaller citation, it is a wrong one.
    data = build(
        lambda p: p.heading("3 Fault tracing", 16).ruled_table(
            [
                ["Code", "Name", "Action"],
                ["F0001", "Overcurrent", "Check motor cable"],
                ["F0002", "Undervoltage", "Check supply"],
            ]
        )
    )
    blocks = extract_structure(data).blocks
    tables = [b for b in blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1
    assert tables[0].kind.is_atomic
    # Every row present, in one block.
    assert "F0001" in tables[0].text
    assert "F0002" in tables[0].text
    assert "Check supply" in tables[0].text


def test_a_tables_rows_are_not_also_emitted_as_paragraphs() -> None:
    # Otherwise one fact yields two citations, and a reader following the
    # second finds a fragment with no table around it.
    data = build(
        lambda p: p.heading("3 Fault tracing", 16).ruled_table(
            [["Code", "Action"], ["F0001", "Check motor cable"]]
        )
    )
    blocks = extract_structure(data).blocks
    paragraphs = [b for b in blocks if b.kind is BlockKind.PARAGRAPH]

    assert not any("F0001" in b.text for b in paragraphs)


def test_a_table_carries_the_section_it_sits_under() -> None:
    data = build(
        lambda p: p.heading("3 Fault tracing", 18)
        .heading("3.2 Overcurrent", 13)
        .ruled_table([["Code", "Action"], ["F0001", "Check cable"]])
    )
    table = next(b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE)

    assert table.section == "3 Fault tracing > 3.2 Overcurrent"


def test_a_borderless_table_is_not_reported_as_a_table() -> None:
    # Investigated before choosing this approach: pdfplumber's text-alignment
    # fallback does return something for these, but it swept a heading into
    # the table and emitted empty rows. A half-right table is worse than none,
    # because it becomes an atomic block silently missing rows.
    data = build(
        lambda p: p.heading("4 Parameters", 16)
        .body("30.17    Current limit    200 %")
        .body("21.03    Stop mode        Coast")
    )
    blocks = extract_structure(data).blocks

    assert BlockKind.TABLE not in kinds(blocks)


# --- what it refuses to read -------------------------------------------------


def test_a_two_column_page_is_refused_rather_than_interleaved() -> None:
    # Read line-by-line, two columns merge into sentences the manual never
    # contained. Indexing that would put invented text behind a real page
    # number, which is precisely what the citation rules exist to prevent.
    data = build(
        lambda p: p.heading("5 Commissioning", 16).columns(
            ["Set the motor data first.", "Then run the ID run."],
            ["Verify the encoder.", "Tune the speed loop."],
        )
    )

    with pytest.raises(UnreadableDocumentError, match="columns"):
        extract_structure(data)


def test_a_scanned_page_is_skipped_rather_than_yielding_garbage() -> None:
    # A manual with one scanned appendix should still yield its readable
    # pages; the scan contributes nothing rather than noise.
    def scanned(page: Page) -> None:
        page.pdf.rect(60, HEIGHT - 300, 400, 200, stroke=1, fill=0)

    data = build(lambda p: p.heading("1 Safety", 16).body("Isolate before working."), scanned)
    blocks = extract_structure(data).blocks

    assert any("Isolate" in b.text for b in blocks)
    assert all(b.page == 1 for b in blocks)


def test_a_document_with_no_text_layer_at_all_is_refused() -> None:
    def scanned(page: Page) -> None:
        page.pdf.rect(60, HEIGHT - 300, 400, 200, stroke=1, fill=0)

    with pytest.raises(UnreadableDocumentError, match="no text layer"):
        extract_structure(build(scanned))


def test_a_file_that_is_not_a_pdf_is_refused() -> None:
    with pytest.raises(UnreadableDocumentError):
        extract_structure(b"this is not a PDF at all")


# --- what chunking needs downstream ------------------------------------------


def test_every_block_carries_a_page_number() -> None:
    # "page 88, Fault tracing" is checkable against the PDF; a block that
    # cannot name its page is not citable at all.
    data = build(
        lambda p: p.heading("3 Fault tracing", 16).body("First page."),
        lambda p: p.heading("4 Maintenance", 16).body("Second page."),
    )
    blocks = extract_structure(data).blocks

    assert {b.page for b in blocks} == {1, 2}
    assert all(b.page >= 1 for b in blocks)


def test_blocks_come_back_in_reading_order() -> None:
    data = build(
        lambda p: p.heading("3 Fault tracing", 16).body("Alpha.").body("Beta."),
        lambda p: p.body("Gamma."),
    )
    texts = [b.text for b in extract_structure(data).blocks]

    assert texts.index("Alpha.") < texts.index("Beta.") < texts.index("Gamma.")


def test_the_result_satisfies_the_schemas_own_ordering_rule() -> None:
    # StructureMap rejects blocks whose pages run backwards, so this both
    # tests the extractor and proves the two agree.
    data = build(
        lambda p: p.heading("A", 16).body("one"),
        lambda p: p.heading("B", 16).body("two"),
        lambda p: p.heading("C", 16).body("three"),
    )
    result = extract_structure(data)

    pages = [b.page for b in result.blocks]
    assert pages == sorted(pages)


def test_it_feeds_chunk_document_end_to_end() -> None:
    # The point of the whole module: what comes out has to be what chunking
    # takes in. Asserted by actually running it rather than by matching types.
    from app.ai.retrieval.chunking import chunk_document
    from app.models.schemas.documents import SourceDocument

    data = build(
        lambda p: p.heading("3 Fault tracing", 18)
        .body("The drive trips on overcurrent when the limit is exceeded.")
        .ruled_table([["Code", "Action"], ["F0001", "Check motor cable"]])
    )
    structure = extract_structure(data)

    document = SourceDocument(
        id="doc-1",
        source_id="abb",
        title="ACS880 Firmware Manual",
        url="https://library.abb.com/acs880.pdf",
        content_hash="0" * 64,
        text="ignored; chunking reads the structure map",
    )
    chunks = chunk_document(document, structure, brand="ABB", model="ACS880", doc_type="manual")

    assert chunks
    assert all(chunk.page >= 1 for chunk in chunks)
    assert all(chunk.section for chunk in chunks)
    # The table survived as its own atomic chunk.
    assert any(chunk.is_atomic and "F0001" in chunk.text for chunk in chunks)


def test_a_table_is_filed_under_the_heading_above_it_not_the_one_below() -> None:
    # The case the first fixtures missed. Every table there sat after its
    # heading, so a version that emitted tables only at the end of the page
    # still passed — the trailing flush happened to catch them.
    #
    # Here a second heading follows the table. If tables are not interleaved by
    # position, this one is filed under "3.3 Earth faults" — a citation
    # pointing at the section *after* the one the table is actually in.
    data = build(
        lambda p: p.heading("3.2 Overcurrent", 16)
        .ruled_table([["Code", "Action"], ["F0001", "Check motor cable"]])
        .heading("3.3 Earth faults", 16)
        .body("Measure insulation resistance.")
    )
    table = next(b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE)

    assert table.section == "3.2 Overcurrent"
