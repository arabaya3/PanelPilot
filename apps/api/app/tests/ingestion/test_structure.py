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
            [
                "Set the motor data first.",
                "Then run the ID run.",
                "Check the direction of rotation.",
                "Confirm the encoder feedback.",
                "Save the parameter set.",
            ],
            [
                "Verify the encoder wiring.",
                "Tune the speed controller.",
                "Record the commissioning date.",
                "Hand over the documentation.",
                "Close the cubicle door.",
            ],
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


# --- the cases idealised fixtures missed --------------------------------------
#
# Every fixture above is a clean page: one column, no running header, no footer
# page number, no page-spanning table, no small print. A review built realistic
# pages instead and found three failures in that blind spot, two of them
# violating the property this module exists to protect.


def test_a_table_spanning_a_page_break_is_one_table() -> None:
    # The critical one. Each page's fragment was emitted as its own atomic
    # block, so an 80-row derating table became three blocks, each presenting
    # itself as complete — verbatim the "half a parameter table presented as a
    # whole one" the design forbids. Worse than counting pipes, because each
    # fragment carries a real page number and section and is therefore
    # maximally credible.
    def half(page: Page, first_row: int) -> None:
        rows = [["Ambient", "Rating"]] + [
            [f"Row {first_row + i}", f"{40 + first_row + i} A"] for i in range(8)
        ]
        page.ruled_table(rows)

    data = build(lambda p: half(p, 0), lambda p: half(p, 8))
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1, "a table split by a page break must not become two atomic blocks"
    for i in range(16):
        assert f"Row {i}" in tables[0].text, f"row {i} was lost in stitching"


def test_two_separate_tables_on_consecutive_pages_stay_separate() -> None:
    # The other side of stitching: fusing two genuinely different tables would
    # be its own fabrication. Different column counts, so they cannot continue.
    data = build(
        lambda p: p.ruled_table([["Code", "Action"], ["F0001", "Check cable"]]),
        lambda p: p.ruled_table([["A", "B", "C"], ["1", "2", "3"]]),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 2


def test_a_standard_manual_footer_does_not_discard_the_document() -> None:
    # A document code on the left and a page number on the right is the
    # standard ABB/Siemens footer. The first column check refused on any single
    # wide gap, so this discarded the entire manual — and `prepare_documents`
    # recorded it as a parse failure with no hint the cause was a heuristic.
    def page_with_footer(page: Page) -> None:
        page.heading("1 Introduction", 16)
        page.body("The relay measures phase currents continuously.")
        page.pdf.setFont("Helvetica", 8)
        page.pdf.drawString(60, 40, "1MRS756378 C")
        page.pdf.drawString(520, 40, "88")

    blocks = extract_structure(build(page_with_footer)).blocks

    assert any(b.kind is BlockKind.HEADING and b.text == "1 Introduction" for b in blocks)


def test_a_contents_page_with_page_numbers_is_not_refused() -> None:
    # Right-aligned page numbers produce one wide gap per line, at a different
    # place on each — furniture, not a column corridor.
    def contents(page: Page) -> None:
        page.heading("Contents", 16)
        page.pdf.setFont("Helvetica", 10)
        for i, (title, number) in enumerate(
            [("1 Introduction", "7"), ("2 Protection", "23"), ("3 Fault tracing", "88")]
        ):
            page.pdf.drawString(60, HEIGHT - 140 - i * 16, title)
            page.pdf.drawString(500, HEIGHT - 140 - i * 16, number)

    blocks = extract_structure(build(contents)).blocks
    assert blocks


def test_small_print_does_not_promote_body_text_to_headings() -> None:
    # Fourteen lines of 6pt disclaimer carry 1218 characters against 90 of
    # body text, so no weight-based body-size estimate picks 10pt. Three
    # attempts to fix this by improving the estimate all failed here; the
    # answer was that size alone cannot decide, and a heading is bold.
    #
    # Left unfixed, two ordinary sentences became level-1 headings and every
    # chunk below inherited a section path like "replacing the relay module in
    # the cubicle." — a citation nobody can resolve against a contents page.
    def page_with_boilerplate(page: Page) -> None:
        page.heading("5 Commissioning", 14)
        page.body("Check the trip circuit supervision output before")
        page.body("replacing the relay module in the cubicle.")
        page.pdf.setFont("Helvetica", 6)
        for i in range(14):
            page.pdf.drawString(
                60,
                HEIGHT - 200 - i * 9,
                "Disclaimer: this document is provided without warranty of any kind,",
            )

    blocks = extract_structure(build(page_with_boilerplate)).blocks
    headings = [b.text for b in blocks if b.kind is BlockKind.HEADING]

    assert headings == ["5 Commissioning"]
    assert all(b.section == "5 Commissioning" for b in blocks if b.kind is not BlockKind.HEADING)


def test_rotated_text_does_not_become_one_block_per_character() -> None:
    # Bucketing by vertical position alone, a 90-degree axis label becomes
    # forty uncitable single-character chunks in reverse order. pdfplumber
    # reports orientation, so this is a check rather than a guess.
    def rotated(page: Page) -> None:
        page.heading("6 Curves", 16)
        page.body("The derating curve is shown below.")
        page.pdf.saveState()
        page.pdf.rotate(90)
        page.pdf.setFont("Helvetica", 9)
        page.pdf.drawString(200, -300, "Output current in amperes")
        page.pdf.restoreState()

    blocks = extract_structure(build(rotated)).blocks

    assert all(len(b.text) > 1 for b in blocks), "rotated text shattered into character blocks"


# --- what the first round of fixes broke --------------------------------------
#
# A second review found that two of those three fixes traded one fabrication
# for another. These pin the corrected behaviour in both directions: the
# original bug must stay fixed, and the fix must not have created a new one.


def test_an_unrelated_all_text_table_is_not_fused_onto_the_previous_page() -> None:
    # Continuation used to accept the *absence* of a header row as evidence,
    # and an all-text table — parameter names and descriptions, which manuals
    # are full of — reports exactly that. So an unrelated parameters table
    # opening page 2 was fused onto page 1's fault codes: twelve rows in one
    # block, presenting parameter rows as fault codes on a page they never
    # appeared on. A continuation must now repeat the header.
    data = build(
        lambda p: p.heading("1 Fault codes", 14).ruled_table(
            [["Code", "Meaning"], ["F0001", "10 A"], ["F0002", "11 A"]]
        ),
        lambda p: p.ruled_table([["Stop mode", "Coast to stop"], ["Start mode", "Ramp up"]]),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 2, "two unrelated tables were fused into one"
    assert "Stop mode" not in tables[0].text


def test_a_table_spanning_three_pages_is_still_one_table() -> None:
    # Adjacency was tested against the merged block's page, which stays at the
    # first. Pages 1 and 2 merged, then page 3 compared 1 == 2 and started a
    # new block — the original bug relocated from every page boundary to every
    # boundary after the first.
    def part(page: Page, first: int) -> None:
        page.ruled_table(
            [["Ambient", "Rating"]]
            + [[f"Row {first + i}", f"{40 + first + i} A"] for i in range(5)]
        )

    data = build(
        lambda p: part(p, 0),
        lambda p: part(p, 5),
        lambda p: part(p, 10),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1
    for i in range(15):
        assert f"Row {i}" in tables[0].text, f"row {i} lost across a three-page span"


def test_a_manual_with_size_only_headings_still_gets_headings() -> None:
    # Requiring bold unconditionally was worse than the bug it replaced: an
    # 18pt-regular-over-10pt-body manual produced zero headings, every block
    # under "Front matter", silently, for every page. Larger than body text is
    # a heading whether or not it is bold.
    def size_only(page: Page) -> None:
        page.pdf.setFont("Helvetica", 18)
        page.pdf.drawString(60, HEIGHT - 70, "3 Fault tracing")
        page.pdf.setFont("Helvetica", 10)
        page.pdf.drawString(60, HEIGHT - 110, "The drive trips when the current limit is exceeded.")

    blocks = extract_structure(build(size_only)).blocks

    assert any(b.kind is BlockKind.HEADING and b.text == "3 Fault tracing" for b in blocks)
    assert all(b.section == "3 Fault tracing" for b in blocks if b.kind is BlockKind.PARAGRAPH)


def test_a_bold_safety_callout_does_not_become_a_section() -> None:
    # `WARNING: Do not touch the terminals.` is bold, short and body-sized.
    # It became a level-4 heading and captured the section path for everything
    # after it — the same unresolvable-path symptom, reached another way.
    # Safety callouts and bold lead-ins are ubiquitous; same-size bold is
    # emphasis, not structure.
    def callout(page: Page) -> None:
        page.heading("1 Operation", 14)
        page.body("Close the main disconnect before starting the drive.")
        page.pdf.setFont("Helvetica-Bold", 10)
        page.pdf.drawString(60, HEIGHT - 140, "WARNING: Do not touch the terminals.")
        page.pdf.setFont("Helvetica", 10)
        page.pdf.drawString(60, HEIGHT - 165, "Wait five minutes for the bus to discharge.")

    blocks = extract_structure(build(callout)).blocks
    headings = [b.text for b in blocks if b.kind is BlockKind.HEADING]

    assert headings == ["1 Operation"]
    assert all(b.section == "1 Operation" for b in blocks)


def test_a_short_two_column_block_is_refused_not_interleaved() -> None:
    # Three lines per column sat below the line-count floor and was
    # interleaved into "outgoing cables.phases." — text the manual never
    # contained, emitted as an ordinary paragraph with a real page number. A
    # page whose wide-gap lines are most of its content is columnar however
    # few they are.
    data = build(
        lambda p: p.heading("7 Isolation", 14).columns(
            [
                "The circuit breaker must be racked out",
                "before any work begins on the",
                "outgoing cables.",
            ],
            ["Verify absence of voltage using an", "approved tester on all three", "phases."],
        )
    )

    with pytest.raises(UnreadableDocumentError, match="columns"):
        extract_structure(data)


def test_a_table_does_not_stitch_across_an_intervening_page() -> None:
    # `<=` instead of `==` in the adjacency test passed every existing test,
    # because a genuine three-page span is consecutive either way. What
    # separates them is a gap: two tables with the same header on pages 1 and
    # 3, with unrelated prose on page 2. Those are two tables in a manual, and
    # merging them would attribute page 3's rows to page 1.
    def rated(page: Page, first: int) -> None:
        page.ruled_table(
            [["Ambient", "Rating"]]
            + [[f"Row {first + i}", f"{40 + first + i} A"] for i in range(4)]
        )

    # The intervening page carries a scanned figure and no text layer, so it
    # contributes no block at all. That is what isolates the adjacency test:
    # with prose in between, `previous.kind is TABLE` already rejects the
    # merge and adjacency is never consulted.
    def scanned(page: Page) -> None:
        page.pdf.rect(60, HEIGHT - 300, 400, 200, stroke=1, fill=0)

    data = build(lambda p: rated(p, 0), scanned, lambda p: rated(p, 10))
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 2, "tables on non-adjacent pages were stitched together"
    assert "Row 10" not in tables[0].text


# --- round three: the fixes were one-dimensional thresholds -------------------
#
# Rounds 1 and 2 each fixed a report by tightening one scalar, and every
# tightening opened a symmetric failure on the other side. These use the
# signals the data actually carries — a numbering prefix, sentence
# termination, text on both sides of a gap — rather than another threshold.


def test_a_long_numbered_subsection_heading_is_not_demoted() -> None:
    # "3.2.1 Overcurrent protection settings" is 37 characters, and a 32-char
    # cap silently made it a paragraph — so the subsection vanished *and* its
    # body was filed under the parent. Not a missing path: a confidently wrong
    # one, which retrieval surfaces as general protection text.
    def numbered(page: Page) -> None:
        page.heading("3 Protection", 18)
        page.heading("3.2.1 Overcurrent protection settings", 14)
        page.body("Set the start value to 1.5 times rated current.")

    blocks = extract_structure(build(numbered)).blocks
    headings = [b.text for b in blocks if b.kind is BlockKind.HEADING]

    assert "3.2.1 Overcurrent protection settings" in headings
    body = next(b for b in blocks if b.text.startswith("Set the start"))
    assert body.section.endswith("3.2.1 Overcurrent protection settings")


def test_a_same_size_bold_heading_is_recognised() -> None:
    # A 10pt-bold-heading over 10pt-body manual is common, and produced zero
    # headings when size ratio alone decided. The numbering prefix carries it.
    def same_size(page: Page) -> None:
        page.pdf.setFont("Helvetica-Bold", 10)
        page.pdf.drawString(60, HEIGHT - 70, "2 Mounting")
        page.pdf.setFont("Helvetica", 10)
        page.pdf.drawString(60, HEIGHT - 95, "Fit the unit to a DIN rail vertically.")

    blocks = extract_structure(build(same_size)).blocks

    assert any(b.kind is BlockKind.HEADING and b.text == "2 Mounting" for b in blocks)


def test_a_continuation_behind_a_continued_banner_is_stitched() -> None:
    # The header *is* repeated here, one row lower, behind "Table 3
    # (continued)" — so demanding an exact match at row zero left a fragment
    # presenting itself as a complete table.
    def part(page: Page, rows: list[list[str]]) -> None:
        page.ruled_table(rows)

    data = build(
        lambda p: part(p, [["Ambient", "Rating"], ["Row 0", "40 A"], ["Row 1", "41 A"]]),
        lambda p: part(
            p,
            [
                ["Table 3 (continued)", ""],
                ["Ambient", "Rating"],
                ["Row 2", "42 A"],
            ],
        ),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1
    assert "Row 2" in tables[0].text


def test_narrow_columns_with_a_wide_gutter_are_refused() -> None:
    # An upper bound on gap width was meant to spare contents pages and let a
    # real two-column page through instead: narrow columns with a wide margin
    # exceeded the ceiling and were interleaved into "Set motor data.Tune the
    # loop." — sentences the manual never contained. A gutter has substantial
    # text on both sides; a contents page's gap has a page number.
    def wide_gutter(page: Page) -> None:
        page.pdf.setFont("Helvetica", 10)
        for i, text in enumerate(["Set motor data.", "Run the ID run.", "Check rotation."]):
            page.pdf.drawString(45, HEIGHT - 100 - i * 14, text)
        for i, text in enumerate(["Tune the loop.", "Record the date.", "Hand over docs."]):
            page.pdf.drawString(400, HEIGHT - 100 - i * 14, text)

    with pytest.raises(UnreadableDocumentError, match="columns"):
        extract_structure(build(wide_gutter))


def test_sibling_subsections_do_not_nest_under_each_other() -> None:
    # `_heading_level` maps size ratios onto absolute depths, so a document
    # going H1 to H3 left the stack too shallow to truncate and a *sibling*
    # appended instead of replacing: "3 Protection > 3.1 Overcurrent > 3.2
    # Earth fault", a containment the manual does not have, inherited by every
    # chunk beneath it.
    def siblings(page: Page) -> None:
        page.heading("3 Protection", 20)
        page.heading("3.1 Overcurrent", 12)
        page.body("Stage one trips on instantaneous current.")
        page.heading("3.2 Earth fault", 12)
        page.body("Residual current is measured across all phases.")

    blocks = extract_structure(build(siblings)).blocks
    earth = next(b for b in blocks if b.text.startswith("Residual"))

    assert earth.section == "3 Protection > 3.2 Earth fault"
    assert "3.1 Overcurrent" not in earth.section


def test_a_banner_row_alone_marks_a_continuation() -> None:
    # Isolates the banner signal. The existing banner test also repeats the
    # header one row down, so it passes on either branch — remove the banner
    # check and it still goes green. Here the header is NOT repeated, so only
    # "(continued)" can carry it.
    data = build(
        lambda p: p.ruled_table([["Ambient", "Rating"], ["Row 0", "40 A"]]),
        lambda p: p.ruled_table([["Table 3 continued", ""], ["Row 1", "41 A"]]),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1
    assert "Row 1" in tables[0].text


def test_a_header_repeated_one_row_down_marks_a_continuation() -> None:
    # The mirror of the above: a banner that does not say "continued" — a
    # table caption — with the header beneath it.
    data = build(
        lambda p: p.ruled_table([["Ambient", "Rating"], ["Row 0", "40 A"]]),
        lambda p: p.ruled_table(
            [["Derating by temperature", ""], ["Ambient", "Rating"], ["Row 1", "41 A"]]
        ),
    )
    tables = [b for b in extract_structure(data).blocks if b.kind is BlockKind.TABLE]

    assert len(tables) == 1
    assert "Row 1" in tables[0].text


def test_a_columnar_block_among_full_width_prose_is_refused() -> None:
    # Both earlier column fixtures are *pure* column pages, where the wide
    # lines are essentially all the content — so both floors pass trivially and
    # neither is pinned. A real manual page is a columnar block surrounded by
    # full-width prose, which is what makes the line-count floor load-bearing.
    def mixed(page: Page) -> None:
        page.heading("5 Commissioning", 14)
        page.pdf.setFont("Helvetica", 10)
        for i in range(6):
            page.pdf.drawString(
                60, HEIGHT - 110 - i * 14, "This paragraph runs the full width of the page here."
            )
        left = ["Set the motor data.", "Run the ID run.", "Check the rotation.", "Save the set."]
        right = ["Tune the loop.", "Record the date.", "Hand over the docs.", "Close the door."]
        for i, text in enumerate(left):
            page.pdf.drawString(60, HEIGHT - 220 - i * 14, text)
        for i, text in enumerate(right):
            page.pdf.drawString(330, HEIGHT - 220 - i * 14, text)

    with pytest.raises(UnreadableDocumentError, match="columns"):
        extract_structure(build(mixed))


def test_a_contents_page_among_prose_is_still_not_refused() -> None:
    # The other side of that floor. Right-aligned page numbers produce wide
    # gaps too, and dropping the both-sides-text check would refuse this — a
    # readable page discarded, which is how one heuristic took out whole
    # manuals in an earlier round.
    def contents(page: Page) -> None:
        page.heading("Contents", 16)
        page.pdf.setFont("Helvetica", 10)
        # Titles of varying length, which is what a real contents page has —
        # and what makes the gaps overlap into a corridor. With uniform-length
        # titles the corridor check alone happens to save the page, so the
        # both-sides-text check is never the thing being tested.
        entries = [
            ("1 Introduction", "7"),
            ("2 Protection and control functions in detail", "23"),
            ("3 Fault tracing", "88"),
            ("4 Maintenance and periodic inspection tasks", "104"),
            ("5 Commissioning", "131"),
        ]
        for i, (title, number) in enumerate(entries):
            page.pdf.drawString(60, HEIGHT - 140 - i * 16, title)
            page.pdf.drawString(500, HEIGHT - 140 - i * 16, number)

    blocks = extract_structure(build(contents)).blocks
    assert any("Introduction" in b.text for b in blocks)
