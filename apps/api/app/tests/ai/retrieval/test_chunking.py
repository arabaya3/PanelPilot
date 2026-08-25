"""Tests for `app/ai/retrieval/chunking.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

AI-001's testing requirement is three sample manuals with manually-verified
boundaries around at least one table and one numbered procedure each. The three
fixtures below are modelled on real documents this product will ingest (an ABB
drive manual, a Schneider installation guide, a Rittal enclosure datasheet),
with the expected boundaries written out by hand rather than derived from the
implementation — a test that computes its expectation the same way the code
does proves only that the code is self-consistent.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval.chunking import (
    TARGET_MAX_TOKENS,
    chunk_document,
    estimate_tokens,
    missing_citation_fields,
)
from app.models.schemas.documents import DocumentChunk, SourceDocument
from app.models.schemas.structure import BlockKind, StructuralBlock, StructureMap

# --- fixtures modelled on real manuals --------------------------------------

_PROSE = (
    "The drive monitors output current continuously and compares it against the "
    "configured trip threshold. When the measured value exceeds that threshold "
    "for longer than the filter time, the drive trips and records the fault. "
)


def _doc(doc_id: str, url: str) -> SourceDocument:
    return SourceDocument(
        id=doc_id,
        source_id="src-1",
        title="Sample manual",
        url=url,
        content_hash=f"hash-{doc_id}",
        text="",
    )


def _abb_manual() -> tuple[SourceDocument, StructureMap, dict[str, str]]:
    """ABB drive manual: a fault-code table and a commissioning procedure."""
    table = (
        "Code | Name | Cause | Action\n"
        "F0001 | OVERCURRENT | Output current exceeded trip limit | Check motor cable\n"
        "F0002 | DC OVERVOLTAGE | DC link above limit | Extend deceleration ramp\n"
        "F2330 | EARTH LEAKAGE | Earth fault in motor or cable | Megger the motor\n"
        "F2340 | SHORT CIRCUIT | Output phase-to-phase short | Inspect terminals\n"
        "F3130 | INPUT PHASE LOSS | Supply phase missing | Check incoming supply"
    )
    procedure = (
        "1. Isolate the drive and verify zero voltage at the DC link terminals.\n"
        "2. Set parameter 99.04 to the motor control mode required by the load.\n"
        "3. Enter motor nameplate data into parameters 99.06 through 99.10.\n"
        "4. Run the identification routine with the motor uncoupled.\n"
        "5. Restore the load and confirm the current stays within the rating."
    )
    blocks = [
        # Front matter, which every real manual has and which no fixture had.
        # Its absence is what let the empty-section defect through review.
        StructuralBlock(
            kind=BlockKind.PARAGRAPH,
            text="ACS880 firmware manual. Revision D. Read the safety "
            "instructions before installing or operating the drive.",
            page=2,
        ),
        StructuralBlock(
            kind=BlockKind.HEADING,
            text="3 Fault tracing",
            page=88,
            section="3 Fault tracing",
            level=1,
        ),
        StructuralBlock(
            kind=BlockKind.PARAGRAPH, text=_PROSE * 2, page=88, section="3 Fault tracing"
        ),
        StructuralBlock(kind=BlockKind.TABLE, text=table, page=89, section="3 Fault tracing"),
        StructuralBlock(
            kind=BlockKind.HEADING,
            text="4 Commissioning",
            page=91,
            section="4 Commissioning",
            level=1,
        ),
        StructuralBlock(
            kind=BlockKind.PROCEDURE, text=procedure, page=92, section="4 Commissioning"
        ),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=93, section="4 Commissioning"),
    ]
    return (
        _doc("abb-acs880", "https://example.invalid/acs880"),
        StructureMap(blocks=blocks),
        {
            "table": table,
            "procedure": procedure,
        },
    )


def _schneider_guide() -> tuple[SourceDocument, StructureMap, dict[str, str]]:
    """Schneider installation guide: a derating table and a sizing procedure."""
    table = (
        "Ambient °C | PVC 70 | XLPE 90\n"
        "25 | 1.06 | 1.04\n"
        "30 | 1.00 | 1.00\n"
        "35 | 0.94 | 0.96\n"
        "40 | 0.87 | 0.91\n"
        "45 | 0.79 | 0.87\n"
        "50 | 0.71 | 0.82"
    )
    procedure = (
        "1. Determine the design current of the circuit.\n"
        "2. Select the reference installation method from Table B.52.1.\n"
        "3. Apply the ambient correction factor for the insulation type.\n"
        "4. Apply the grouping factor for circuits sharing the containment.\n"
        "5. Choose the smallest cross-section whose derated ampacity exceeds the design current."
    )
    blocks = [
        StructuralBlock(
            kind=BlockKind.PARAGRAPH,
            text="Electrical Installation Guide 2024. This guide is intended "
            "for qualified electrical engineers.",
            page=1,
        ),
        StructuralBlock(
            kind=BlockKind.HEADING,
            text="G Cable sizing",
            page=410,
            section="G Cable sizing",
            level=1,
        ),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=410, section="G Cable sizing"),
        StructuralBlock(
            kind=BlockKind.TABLE, text=table, page=412, section="G Cable sizing > G.6 Derating"
        ),
        StructuralBlock(
            kind=BlockKind.PROCEDURE,
            text=procedure,
            page=413,
            section="G Cable sizing > G.7 Method",
        ),
    ]
    return (
        _doc("schneider-eig", "https://example.invalid/eig"),
        StructureMap(blocks=blocks),
        {
            "table": table,
            "procedure": procedure,
        },
    )


def _rittal_datasheet() -> tuple[SourceDocument, StructureMap, dict[str, str]]:
    """Rittal datasheet: a dissipation table and a cooling-selection procedure."""
    table = (
        "Enclosure | W x H x D mm | Effective area m2\n"
        "TS8 6-part | 600 x 1800 x 400 | 5.4\n"
        "TS8 8-part | 800 x 2000 x 500 | 7.9\n"
        "TS8 12-part | 1200 x 2000 x 600 | 10.6"
    )
    procedure = (
        "1. Sum the dissipation of every component mounted in the enclosure.\n"
        "2. Read the effective surface area for the enclosure and mounting type.\n"
        "3. Calculate passive loss from the temperature difference and surface area.\n"
        "4. Subtract passive loss from total dissipation to get required cooling."
    )
    blocks = [
        StructuralBlock(
            kind=BlockKind.PARAGRAPH,
            text="TS8 enclosure system datasheet. Dimensions are nominal and "
            "subject to manufacturing tolerance.",
            page=1,
        ),
        StructuralBlock(
            kind=BlockKind.HEADING,
            text="5 Climate control",
            page=12,
            section="5 Climate control",
            level=1,
        ),
        StructuralBlock(kind=BlockKind.TABLE, text=table, page=12, section="5 Climate control"),
        StructuralBlock(
            kind=BlockKind.PROCEDURE, text=procedure, page=13, section="5 Climate control"
        ),
        StructuralBlock(
            kind=BlockKind.PARAGRAPH, text=_PROSE, page=14, section="5 Climate control"
        ),
    ]
    return (
        _doc("rittal-ts8", "https://example.invalid/ts8"),
        StructureMap(blocks=blocks),
        {
            "table": table,
            "procedure": procedure,
        },
    )


MANUALS = {
    "abb": _abb_manual,
    "schneider": _schneider_guide,
    "rittal": _rittal_datasheet,
}


def _chunk(doc: SourceDocument, structure: StructureMap) -> list[DocumentChunk]:
    return chunk_document(doc, structure, brand="ABB", model="ACS880", doc_type="manual")


# --- the spec's testing requirement -----------------------------------------


@pytest.mark.parametrize("manual", sorted(MANUALS))
def test_a_table_is_never_split_across_chunks(manual: str) -> None:
    """A parameter table split in two gives an engineer half a table.

    Worse, it gives no signal that the other half exists — they would size a
    cable from the rows they can see. The whole table must land in exactly one
    chunk, intact.
    """
    doc, structure, expected = MANUALS[manual]()
    chunks = _chunk(doc, structure)

    holding = [c for c in chunks if expected["table"] in c.text]
    assert (
        len(holding) == 1
    ), f"{manual}: table appears in {len(holding)} chunks; it must be intact in exactly one"
    # And no other chunk may hold a fragment of it.
    first_row = expected["table"].splitlines()[1]
    fragments = [c for c in chunks if first_row in c.text and c is not holding[0]]
    assert not fragments, f"{manual}: a table fragment leaked into another chunk"


@pytest.mark.parametrize("manual", sorted(MANUALS))
def test_a_numbered_procedure_is_never_split_mid_step(manual: str) -> None:
    """Half a procedure reads as a complete one, which is the danger.

    An engineer following steps 1-3 of a five-step isolation procedure has no
    indication that steps 4 and 5 exist.
    """
    doc, structure, expected = MANUALS[manual]()
    chunks = _chunk(doc, structure)

    holding = [c for c in chunks if expected["procedure"] in c.text]
    assert (
        len(holding) == 1
    ), f"{manual}: procedure appears in {len(holding)} chunks; it must be intact in one"
    steps = [line for line in expected["procedure"].splitlines() if line.strip()]
    assert all(step in holding[0].text for step in steps), f"{manual}: a step was dropped"


@pytest.mark.parametrize("manual", sorted(MANUALS))
def test_every_chunk_resolves_to_one_page_and_section(manual: str) -> None:
    """AI-001's acceptance criterion, asserted per chunk."""
    doc, structure, _ = MANUALS[manual]()
    for chunk in _chunk(doc, structure):
        assert chunk.page >= 1
        assert (
            missing_citation_fields(chunk) == []
        ), f"{manual}: chunk {chunk.id} is missing {missing_citation_fields(chunk)}"


@pytest.mark.parametrize("manual", sorted(MANUALS))
def test_no_metadata_field_is_left_empty(manual: str) -> None:
    """The other half of the criterion: nothing blank, not just nothing null."""
    doc, structure, _ = MANUALS[manual]()
    chunks = _chunk(doc, structure)
    assert chunks
    for chunk in chunks:
        for field in (
            "id",
            "document_id",
            "text",
            "section",
            "brand",
            "model",
            "doc_type",
            "source_url",
        ):
            assert str(getattr(chunk, field)).strip(), f"{manual}: {field} is empty"


# --- band and boundary behaviour --------------------------------------------


def test_oversize_is_allowed_only_for_atomic_structures() -> None:
    """A chunk may exceed the band, but only with a stated reason.

    An unexplained 900-token chunk is indistinguishable from a broken chunker;
    one carrying oversized_reason is a documented tradeoff.
    """
    doc, structure, _ = MANUALS["abb"]()
    for chunk in _chunk(doc, structure):
        if estimate_tokens(chunk.text) > TARGET_MAX_TOKENS:
            assert chunk.oversized_reason, f"chunk {chunk.id} exceeds the band with no reason given"


def test_prose_is_split_when_it_exceeds_the_band() -> None:
    """Long prose has no atomic structure, so the band applies to it."""
    doc = _doc("long-prose", "https://example.invalid/long")
    blocks = [
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=1, section="1 Intro")
        for _ in range(12)
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    assert len(chunks) > 1, "prose well past the band was not split at all"
    for chunk in chunks:
        assert chunk.oversized_reason is None


def test_a_heading_starts_a_new_chunk() -> None:
    """The heading is where the section path comes from; it stays with its content."""
    doc = _doc("headings", "https://example.invalid/h")
    blocks = [
        StructuralBlock(kind=BlockKind.HEADING, text="1 First", page=1, section="1 First", level=1),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="Short body.", page=1, section="1 First"),
        StructuralBlock(
            kind=BlockKind.HEADING, text="2 Second", page=2, section="2 Second", level=1
        ),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="Other body.", page=2, section="2 Second"),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    sections = [c.section for c in chunks]
    assert "1 First" in sections
    assert "2 Second" in sections
    # The two sections must not be merged into one chunk despite being short.
    merged = [c for c in chunks if "First" in c.text and "Second" in c.text]
    assert not merged, "two sections were merged; the citation would name only one"


def test_a_chunk_cites_the_page_where_it_starts() -> None:
    """A chunk spanning a page break cites its first page.

    That is where a reader opens the PDF and starts looking.
    """
    doc = _doc("spanning", "https://example.invalid/s")
    blocks = [
        StructuralBlock(
            kind=BlockKind.PARAGRAPH, text="Start of the passage.", page=7, section="2 Body"
        ),
        StructuralBlock(
            kind=BlockKind.PARAGRAPH, text="Continues overleaf.", page=8, section="2 Body"
        ),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    assert chunks[0].page == 7


def test_chunk_ids_are_stable_across_reruns() -> None:
    """Re-chunking an unchanged document must not look like new content.

    Promotion compares content to decide whether live text changed; ids that
    moved every run would make every re-index look like a rewrite.
    """
    doc, structure, _ = MANUALS["abb"]()
    assert [c.id for c in _chunk(doc, structure)] == [c.id for c in _chunk(doc, structure)]


def test_changed_text_changes_the_chunk_id() -> None:
    """The other direction: edited content must not reuse an id."""
    doc, structure, _ = MANUALS["abb"]()
    first = _chunk(doc, structure)
    structure.blocks[1].text = structure.blocks[1].text + " Additional clarification."
    assert [c.id for c in _chunk(doc, structure)] != [c.id for c in first]


def test_an_empty_structure_map_yields_no_chunks() -> None:
    doc = _doc("empty", "https://example.invalid/e")
    assert _chunk(doc, StructureMap(blocks=[])) == []


# --- the structure map's own guarantees -------------------------------------


def test_a_block_with_no_text_is_rejected() -> None:
    """An empty block would cite a page containing nothing."""
    with pytest.raises(ValueError, match="has no text"):
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="   ", page=1)


def test_blocks_out_of_reading_order_are_rejected() -> None:
    """Order is what lets a chunk claim a page; a scrambled map cites wrongly."""
    with pytest.raises(ValueError, match="reading order"):
        StructureMap(
            blocks=[
                StructuralBlock(kind=BlockKind.PARAGRAPH, text="later", page=9, section="s"),
                StructuralBlock(kind=BlockKind.PARAGRAPH, text="earlier", page=2, section="s"),
            ]
        )


def test_tables_and_procedures_are_the_atomic_kinds() -> None:
    """Pinned: widening this set silently permits splitting something indivisible."""
    atomic = {k for k in BlockKind if k.is_atomic}
    assert atomic == {BlockKind.TABLE, BlockKind.PROCEDURE}


# --- regressions for what review found --------------------------------------


def test_front_matter_is_citable_rather_than_section_less() -> None:
    """Regression: a blank section made the chunk uncitable.

    Content before the first heading — cover page, revision history, safety
    notice — previously produced ``section=""``, which fails the acceptance
    criterion outright. All three fixtures happened to start at a heading, so
    the suite certified a criterion the code did not meet.
    """
    doc = _doc("frontmatter", "https://example.invalid/f")
    blocks = [
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="Cover page. Revision D.", page=1),
        StructuralBlock(kind=BlockKind.HEADING, text="1 Scope", page=3, section="1 Scope"),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=3, section="1 Scope"),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    assert chunks
    for chunk in chunks:
        assert chunk.section.strip(), f"chunk {chunk.id} has no section"
        assert missing_citation_fields(chunk) == []


def test_a_blank_metadata_field_is_refused_at_construction() -> None:
    """Regression: brand/model/doc_type passed straight through unvalidated.

    ``missing_citation_fields`` existed but was enforced nowhere, so a
    whitespace brand reached the index silently.
    """
    doc, structure, _ = MANUALS["abb"]()
    with pytest.raises(ValueError, match="blank citation fields"):
        chunk_document(doc, structure, brand="   ", model="ACS880", doc_type="manual")


def test_a_section_change_without_a_heading_block_still_splits() -> None:
    """Regression: a chunk could be labelled with a section describing part of it.

    Parsers often assign a subsection path without emitting every heading as
    its own block. Grouping across that boundary produced a citation pointing
    a reader at a section the text is not in.
    """
    doc = _doc("subsections", "https://example.invalid/sub")
    # Deliberately tiny and no HEADING between them, so neither the band nor
    # the heading rule can separate these. The section change is then the only
    # thing that can, which is what makes this test able to detect its loss.
    # A generous target_max_tokens removes the band from the picture entirely.
    blocks = [
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="Alpha body.", page=4, section="2 A"),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="Beta body.", page=4, section="2 B"),
    ]
    chunks = chunk_document(
        doc,
        StructureMap(blocks=blocks),
        brand="ABB",
        model="ACS880",
        doc_type="manual",
        target_max_tokens=10_000,
    )
    assert len(chunks) == 2, f"expected one chunk per section, got {len(chunks)}"
    for chunk in chunks:
        other = "Beta" if chunk.section == "2 A" else "Alpha"
        assert other not in chunk.text, f"{chunk.section} chunk contains {other} text"


def test_a_dense_table_is_not_under_counted_as_prose() -> None:
    """Regression: chars//4 under-counted tables ~3-4x.

    That silently disabled ``oversized_reason`` on exactly the content the
    reporting exists to explain — a table the code believed was 289 tokens was
    really over a thousand.
    """
    table = "\n".join(f"{n} | {n * 1.5:.2f} | {n * 2.25:.3f} | {n % 7}" for n in range(60))
    prose = "The quick brown fox jumps over the lazy dog. " * 30
    # Per character: a dense table must cost more tokens than prose.
    # Comparing totals would only compare lengths.
    table_rate = estimate_tokens(table) / len(table)
    prose_rate = estimate_tokens(prose) / len(prose)
    assert table_rate > prose_rate * 2, (
        f"table {table_rate:.3f} tokens/char vs prose {prose_rate:.3f}; a dense "
        "table must not be costed as cheaply as prose"
    )


def test_a_lone_heading_is_not_emitted_as_its_own_chunk() -> None:
    """Regression: one-token chunks polluted the index.

    A heading alone carries no answer but still matches on embedding noise.
    """
    doc = _doc("headings-only", "https://example.invalid/ho")
    # Three headings in a row, then prose. Nothing here is atomic, so the
    # heading-before-table rule cannot help: only the undersized merge stops
    # these becoming three one-token chunks.
    blocks = [
        StructuralBlock(kind=BlockKind.HEADING, text="4 X", page=1, section="4 X"),
        StructuralBlock(kind=BlockKind.HEADING, text="4.1 Y", page=1, section="4 X"),
        StructuralBlock(kind=BlockKind.HEADING, text="4.1.1 Z", page=1, section="4 X"),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=1, section="4 X"),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    assert chunks
    for chunk in chunks:
        assert (
            estimate_tokens(chunk.text) > 5
        ), f"chunk {chunk.id!r} holds only {chunk.text!r} — too small to retrieve"


def test_a_heading_before_a_table_survives_as_the_section_not_the_text() -> None:
    """The common manual layout: a heading immediately followed by a table.

    An earlier version of this test asserted the heading text ended up INSIDE
    the table's chunk. That was wrong twice over: it contaminated the atomic
    chunk with content that is not part of the table, and it meant the chunk
    was no longer just the table. The heading's actual job is to supply the
    section path, and it still does — the citation names it without the text
    being spliced in.
    """
    doc, structure, expected = MANUALS["rittal"]()
    chunks = _chunk(doc, structure)
    holding = [c for c in chunks if expected["table"] in c.text]
    assert len(holding) == 1
    assert holding[0].text.strip() == expected["table"].strip(), "the chunk is not just the table"
    assert holding[0].section == "5 Climate control", "the heading was lost from the citation"


def test_band_split_sections_are_linked_not_duplicated() -> None:
    """The spec's "overlap only at true structural continuations".

    Done with links rather than copied text: duplicating a passage into two
    chunks makes it match twice and inflates how well supported an answer looks.
    """
    doc = _doc("long-section", "https://example.invalid/ls")
    blocks = [
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=1, section="1 Long")
        for _ in range(12)
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    assert len(chunks) > 1
    assert chunks[0].continues_into == chunks[1].id
    assert chunks[1].continues_from == chunks[0].id
    # Linked, not overlapped: no text is present in both.
    assert chunks[0].text not in chunks[1].text


@pytest.mark.parametrize("manual", sorted(MANUALS))
def test_an_atomic_structure_is_alone_in_its_chunk(manual: str) -> None:
    """Intact is not enough — a table must be the WHOLE chunk.

    Regression, and the same class of gap as the front-matter one: the existing
    tests assert a table appears intact in exactly one chunk, which stays true
    when other content is *added around* it. Nothing asserted it was alone.

    The undersized merge guarded on ``oversized_reason``, which a small table
    never has, so tables and procedures under the band were fused with their
    neighbours. In the rittal fixture a table and a procedure became one chunk;
    in abb a table absorbed a heading and a prose paragraph. Two consecutive
    tables from pages 89 and 90 fused into a single chunk citing page 88 — an
    engineer reading it sees rows from two tables under one wrong citation.
    """
    doc, structure, expected = MANUALS[manual]()
    chunks = _chunk(doc, structure)

    for label in ("table", "procedure"):
        holding = [c for c in chunks if expected[label] in c.text]
        assert len(holding) == 1, f"{manual}: {label} spans {len(holding)} chunks"
        chunk = holding[0]
        assert chunk.is_atomic, f"{manual}: {label} chunk is not marked atomic"
        assert chunk.text.strip() == expected[label].strip(), (
            f"{manual}: the {label} chunk carries extra content beyond the "
            f"{label} itself:\n{chunk.text!r}"
        )


def test_two_adjacent_small_tables_are_never_fused() -> None:
    """The exact reproduction from review, pinned.

    Two small tables on consecutive pages must stay separate chunks citing
    their own pages — not one chunk citing whichever page came first.
    """
    doc = _doc("two-tables", "https://example.invalid/tt")
    first = "Code | Cause\nF0001 | Overcurrent\nF0002 | Overvoltage"
    second = "Code | Cause\nF2330 | Earth fault\nF2340 | Short circuit"
    blocks = [
        StructuralBlock(kind=BlockKind.TABLE, text=first, page=89, section="3 Faults"),
        StructuralBlock(kind=BlockKind.TABLE, text=second, page=90, section="3 Faults"),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))

    assert len(chunks) == 2, f"two tables collapsed into {len(chunks)} chunk(s)"
    assert chunks[0].text.strip() == first
    assert chunks[0].page == 89
    assert chunks[1].text.strip() == second
    assert chunks[1].page == 90, "the second table must cite its own page"


def test_a_small_table_is_not_absorbed_by_following_prose() -> None:
    """Size must not decide indivisibility."""
    doc = _doc("small-table", "https://example.invalid/st")
    table = "Param | Value\n99.04 | Vector"
    blocks = [
        StructuralBlock(kind=BlockKind.TABLE, text=table, page=5, section="2 Params"),
        StructuralBlock(kind=BlockKind.PARAGRAPH, text=_PROSE, page=5, section="2 Params"),
    ]
    chunks = _chunk(doc, StructureMap(blocks=blocks))
    holding = [c for c in chunks if table in c.text]
    assert len(holding) == 1
    assert holding[0].text.strip() == table, "prose leaked into the table's chunk"


def test_atomicity_is_marked_independently_of_size() -> None:
    """``oversized_reason`` is reporting; ``is_atomic`` is correctness.

    Using the former as a proxy for the latter is what caused the fusion.
    """
    doc = _doc("flag", "https://example.invalid/fl")
    small = "A | B\n1 | 2"
    blocks = [StructuralBlock(kind=BlockKind.TABLE, text=small, page=1, section="1 S")]
    chunk = _chunk(doc, StructureMap(blocks=blocks))[0]
    assert chunk.is_atomic, "a small table is still atomic"
    assert chunk.oversized_reason is None, "and it is not oversized"
