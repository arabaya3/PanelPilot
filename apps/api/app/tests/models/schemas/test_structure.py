"""Tests for `app/models/schemas/structure.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The structure map is what makes a citation resolvable, so its own guarantees
are asserted here; how chunking consumes it is tested in
`tests/ai/retrieval/test_chunking.py`.
"""

from __future__ import annotations

import pytest

from app.models.schemas.structure import (
    FRONT_MATTER,
    BlockKind,
    StructuralBlock,
    StructureMap,
)


def test_only_tables_and_procedures_are_atomic() -> None:
    """Widening this set silently permits splitting something indivisible."""
    assert BlockKind.TABLE.is_atomic
    assert BlockKind.PROCEDURE.is_atomic
    assert not BlockKind.PARAGRAPH.is_atomic
    assert not BlockKind.HEADING.is_atomic
    assert not BlockKind.FIGURE_CAPTION.is_atomic


def test_a_block_must_carry_a_real_page() -> None:
    """Page 0 is not a page; a chunk citing it sends the reader nowhere."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="body", page=0)


def test_a_whitespace_only_block_is_rejected() -> None:
    """An empty block would produce a chunk citing a page containing nothing."""
    with pytest.raises(ValueError, match="has no text"):
        StructuralBlock(kind=BlockKind.TABLE, text="\n  \t ", page=4)


def test_blocks_must_be_in_reading_order() -> None:
    """Order is what lets a chunk claim a page range.

    A parser emitting blocks out of order would silently produce citations
    pointing at the wrong page — the failure looks like correct output.
    """
    with pytest.raises(ValueError, match="reading order"):
        StructureMap(
            blocks=[
                StructuralBlock(kind=BlockKind.PARAGRAPH, text="later", page=12, section="s"),
                StructuralBlock(kind=BlockKind.PARAGRAPH, text="earlier", page=3, section="s"),
            ]
        )


def test_repeated_pages_are_allowed() -> None:
    """Several blocks legitimately share one page; only going backwards is wrong."""
    StructureMap(
        blocks=[
            StructuralBlock(kind=BlockKind.HEADING, text="1 Intro", page=5, section="1 Intro"),
            StructuralBlock(kind=BlockKind.PARAGRAPH, text="Body.", page=5, section="1 Intro"),
        ]
    )


def test_an_empty_map_is_valid() -> None:
    """A document whose parser found no structure yields no chunks, not an error."""
    assert StructureMap(blocks=[]).blocks == []


def test_front_matter_is_labelled_rather_than_left_blank() -> None:
    """Front matter precedes any heading — but it still has to be citable.

    This test previously asserted the opposite: that an empty section was fine
    before the first heading. That pinned the behaviour which made every
    front-matter chunk uncitable and failed AI-001's acceptance criterion, and
    it passed only because no fixture had front matter in it.
    """
    block = StructuralBlock(kind=BlockKind.PARAGRAPH, text="Cover page.", page=1)
    assert block.section == FRONT_MATTER
    assert block.section.strip(), "front matter must still resolve to something"
