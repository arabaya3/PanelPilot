"""Structural description of a source document.

A manual's own headings, tables and numbered procedures are what make a
citation resolvable: "page 88, Fault tracing" is checkable against the PDF,
whereas "characters 4200-4900" is not. This module models that structure so
chunking can split on it rather than on a character count.

The structure map comes from the PDF parser (layout and heading extraction).
Chunking consumes it and never re-derives structure from the text itself —
guessing where a table starts by counting pipes is exactly the failure this
design avoids.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

# What content before the first heading is filed under. A real section path
# would be a lie, but an empty one is worse: it makes the chunk uncitable,
# which the acceptance criterion forbids.
FRONT_MATTER = "Front matter"


class BlockKind(StrEnum):
    """What a structural block is, which decides whether it may be split.

    ``TABLE`` and ``PROCEDURE`` are **atomic**: a parameter table split across
    two chunks gives an engineer half a table with no indication the rest
    exists, and half a numbered procedure reads as a complete one. Both are
    worse than an oversized chunk.
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    PROCEDURE = "procedure"
    FIGURE_CAPTION = "figure_caption"

    @property
    def is_atomic(self) -> bool:
        """Report whether this block must never be split mid-way."""
        return self in {BlockKind.TABLE, BlockKind.PROCEDURE}


class StructuralBlock(BaseModel):
    """One structural unit extracted from the source document.

    Attributes:
        kind: What sort of block this is.
        text: The block's text, exactly as it should appear in a chunk.
        page: 1-indexed page in the source PDF. Required — a chunk that cannot
            name its page is not citable.
        section: The heading path this block sits under, e.g.
            ``"3 Fault tracing > 3.2 Overcurrent"``. Content preceding the
            first heading is labelled ``FRONT_MATTER`` rather than left blank:
            a chunk citing an empty section is not resolvable, and every real
            manual opens with a cover page, revision history or safety notice.
        level: Heading depth, for headings only.
    """

    kind: BlockKind
    text: str
    page: int = Field(ge=1)
    section: str = FRONT_MATTER
    level: int | None = None

    @model_validator(mode="after")
    def _reject_empty_text(self) -> StructuralBlock:
        """Refuse a block with no text.

        Returns:
            The validated block.

        Raises:
            ValueError: If the text is blank. An empty block would produce a
                chunk citing a page that contains nothing.
        """
        if not self.text.strip():
            raise ValueError(f"{self.kind.value} block on page {self.page} has no text")
        return self


class StructureMap(BaseModel):
    """The ordered structural blocks of one document.

    Attributes:
        blocks: Blocks in reading order.
    """

    blocks: list[StructuralBlock]

    @model_validator(mode="after")
    def _reject_out_of_order_pages(self) -> StructureMap:
        """Refuse blocks whose page numbers run backwards.

        Returns:
            The validated map.

        Raises:
            ValueError: If pages decrease. Reading order is what lets a chunk
                claim a page range, so a parser emitting blocks out of order
                would silently produce citations pointing at the wrong page.
        """
        pages = [b.page for b in self.blocks]
        if pages != sorted(pages):
            raise ValueError("structural blocks are not in reading order by page")
        return self
