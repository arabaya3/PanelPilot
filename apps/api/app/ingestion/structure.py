"""Extracting a document's structure from its PDF layout.

``app.models.schemas.structure`` says the structure map "comes from the PDF
parser (layout and heading extraction)" and that chunking "never re-derives
structure from the text itself — guessing where a table starts by counting
pipes is exactly the failure this design avoids". This module is that parser.

**Scope note.** This was not in BE-005's original description, which treats
text extraction as already solved and hands documents straight to chunking. It
turned out nothing produced a ``StructureMap``, and the alternative — inferring
structure from text shape — is the exact thing the design forbids. See the
scope note on BE-005 in docs/tasks/adan-lane.md.

**Why pdfplumber.** Investigated against PyMuPDF, pypdf and unstructured.
PyMuPDF is AGPL-3.0-or-commercial, which rules it out for a proprietary
product. pdfplumber is MIT and, more importantly, surfaces the two signals this
needs: per-character font size and weight, so a heading is identified by its
typography rather than by whether its wording looks heading-shaped; and table
detection from **ruling lines**, so an atomic block is recognised from the
geometry a human sees rather than from counting delimiters in flat text.

**What it refuses to do.** Probing real-shaped pages showed two cases where the
layout does not support a confident answer:

* A **borderless table** produces no ruling lines. pdfplumber's text-alignment
  fallback does return something, but it swept a heading into the table and
  emitted empty rows — a half-right table is worse than none, because it
  becomes an atomic block that is silently missing rows. Only ruled tables are
  detected.
* A **two-column page** interleaves when read line-by-line: two independent
  sentences merge into one. Pages laid out in columns are reported rather than
  guessed at.

A page with no text layer at all — a scan — yields nothing rather than garbage,
which is the failure direction that matters. Unciteable text wearing a real
page number is exactly what the citation rules exist to prevent.
"""

from __future__ import annotations

import io
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pdfplumber
import structlog

from app.models.schemas.structure import (
    FRONT_MATTER,
    BlockKind,
    StructuralBlock,
    StructureMap,
)

logger = structlog.get_logger(__name__)

#: Characters closer than this vertically belong to the same line. Chosen
#: below a single line's leading so ordinary spacing does not merge lines.
LINE_TOLERANCE_PT = 3.0

#: A line whose font is this much larger than the document's body size is a
#: heading. Relative rather than absolute: manuals are typeset at wildly
#: different base sizes, and 12pt is a heading in one and body text in another.
HEADING_SIZE_RATIO = 1.15

#: A gap wider than this between two runs on the same line suggests columns
#: rather than a sentence. Expressed as a fraction of page width so it holds
#: for A4 and Letter alike.
COLUMN_GAP_RATIO = 0.12

#: Below this, a page is treated as having no usable text layer.
MIN_CHARS_PER_PAGE = 2


class UnreadableDocumentError(Exception):
    """The PDF cannot be read into structure with any confidence.

    Raised rather than returning a partial map. A document that half-parsed
    would stage chunks citing pages whose content was never really extracted,
    and the whole citation chain rests on that not happening.
    """


@dataclass(frozen=True)
class _Line:
    """One visual line of text.

    Attributes:
        text: The line's characters in reading order.
        page: 1-indexed page it appeared on.
        top: Vertical position, for ordering.
        size: Largest font size on the line — a heading with a trailing
            footnote marker is still a heading.
        bold: Whether the dominant font is bold.
        max_gap: Widest horizontal gap between adjacent characters, used to
            spot column interleaving.
    """

    text: str
    page: int
    top: float
    size: float
    bold: bool
    max_gap: float


def _group_lines(chars: Sequence[dict[str, Any]], page_number: int) -> list[_Line]:
    """Group a page's characters into visual lines.

    Args:
        chars: pdfplumber character dicts for one page.
        page_number: 1-indexed page number.

    Returns:
        Lines in reading order, top to bottom.
    """
    buckets: dict[int, list[dict[str, Any]]] = {}
    for char in chars:
        key = int(float(char["top"]) / LINE_TOLERANCE_PT)
        buckets.setdefault(key, []).append(char)

    lines: list[_Line] = []
    for key in sorted(buckets):
        row = sorted(buckets[key], key=lambda c: float(c["x0"]))
        text = "".join(str(c["text"]) for c in row).strip()
        if not text:
            continue

        gap = 0.0
        for previous, current in itertools.pairwise(row):
            gap = max(gap, float(current["x0"]) - float(previous["x1"]))

        fonts = [str(c.get("fontname", "")) for c in row]
        lines.append(
            _Line(
                text=text,
                page=page_number,
                top=float(row[0]["top"]),
                size=max(float(c.get("size", 0.0)) for c in row),
                bold=sum("bold" in f.lower() for f in fonts) > len(fonts) / 2,
                max_gap=gap,
            )
        )
    return lines


def _body_size(lines: Sequence[_Line]) -> float:
    """Estimate the document's body-text size.

    Args:
        lines: Every line in the document.

    Returns:
        The size carrying the most *characters*, which in a manual is body text
        by a wide margin.

    Weighted by characters rather than by line count, and that distinction is
    the whole correctness of this function. A heading is one short line; body
    text is many long ones. Counting lines, a page with one heading and one
    sentence ties at 1–1, and ``max`` then picks whichever size hashes first —
    which on a real fixture picked the *heading's* 18pt as "body", measured the
    body text at 0.56 times it, and classified nothing as a heading at all.

    The mode rather than the mean, still: a few very large title lines would
    drag an average upward and suppress real headings.
    """
    weights: dict[float, int] = {}
    for line in lines:
        size = round(line.size, 1)
        weights[size] = weights.get(size, 0) + len(line.text)
    if not weights:
        return 0.0
    # Ties broken toward the smaller size: body text is never the largest
    # thing on a page, so when two sizes carry equal weight the smaller is the
    # safer guess.
    return min(weights, key=lambda size: (-weights[size], size))


def _heading_level(size: float, body: float) -> int:
    """Rank a heading by how much larger than body text it is.

    Args:
        size: The heading line's font size.
        body: The document's body size.

    Returns:
        1 for the largest headings, rising for smaller ones. Capped at 6, the
        depth beyond which a section path stops being useful anyway.
    """
    if body <= 0:
        return 1
    ratio = size / body
    if ratio >= 1.6:
        return 1
    if ratio >= 1.35:
        return 2
    if ratio >= HEADING_SIZE_RATIO:
        return 3
    return 4


def _section_path(stack: Sequence[str]) -> str:
    """Render the current heading stack as a section path.

    Args:
        stack: Headings from outermost to innermost.

    Returns:
        The path, or ``FRONT_MATTER`` when nothing has been seen yet. Never
        empty — an empty section makes a chunk uncitable.
    """
    return " > ".join(stack) if stack else FRONT_MATTER


def _table_rows(table: Any) -> str:
    """Render a detected table as text.

    Args:
        table: A pdfplumber table.

    Returns:
        One line per row, cells tab-separated. Kept whole deliberately: this
        becomes an atomic block, and the point of that is that a reader gets
        the entire table or none of it.
    """
    rendered: list[str] = []
    for row in table.extract():
        cells = [(cell or "").replace("\n", " ").strip() for cell in row]
        if any(cells):
            rendered.append("\t".join(cells))
    return "\n".join(rendered)


def _flush_tables_above(
    pending: list[tuple[float, Any]],
    *,
    limit: float,
    page: int,
    stack: Sequence[str],
    into: list[StructuralBlock],
) -> None:
    """Emit any pending table that starts above a point on the page.

    Takes its state as arguments rather than closing over the caller's loop
    variables. As a nested function it captured ``pending``, ``page`` and
    ``stack`` by reference, which worked only because each page redefined it —
    a refactor hoisting the definition would have made every table read the
    final page's section, silently.

    Args:
        pending: Remaining ``(top, table)`` pairs for this page, ascending.
            Consumed in place.
        limit: Emit tables starting at or above this vertical position.
        page: 1-indexed page number.
        stack: The heading stack as it stands at this point in the page.
        into: Block list to append to.
    """
    while pending and pending[0][0] <= limit:
        _, table = pending.pop(0)
        text = _table_rows(table)
        if not text:
            continue
        into.append(
            StructuralBlock(
                kind=BlockKind.TABLE,
                text=text,
                page=page,
                section=_section_path(stack),
            )
        )


def extract_structure(data: bytes, *, document_id: str = "") -> StructureMap:
    """Read a PDF's structural blocks from its layout.

    Args:
        data: The PDF bytes as crawled.
        document_id: Identifier for log lines; not used for parsing.

    Returns:
        The document's blocks in reading order.

    Raises:
        UnreadableDocumentError: If the file is not a readable PDF, has no text
            layer, or is laid out in columns this cannot read in order.
    """
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            pages = list(document.pages)
            per_page = [(page.page_number, page.chars, page.find_tables()) for page in pages]
    except UnreadableDocumentError:
        raise
    except Exception as exc:
        raise UnreadableDocumentError(f"could not open PDF: {exc}") from exc

    all_lines: list[_Line] = []
    tables_by_page: dict[int, list[Any]] = {}
    for page_number, chars, tables in per_page:
        if len(chars) < MIN_CHARS_PER_PAGE:
            # A scan. Skipped rather than failed: a manual with one scanned
            # appendix should still yield its readable pages.
            logger.info("structure.page_without_text", document_id=document_id, page=page_number)
            continue
        all_lines.extend(_group_lines(chars, page_number))
        tables_by_page[page_number] = tables

    if not all_lines:
        raise UnreadableDocumentError("no text layer in any page")

    body = _body_size(all_lines)
    blocks: list[StructuralBlock] = []
    stack: list[str] = []

    for page_number in sorted({line.page for line in all_lines}):
        page_lines = [line for line in all_lines if line.page == page_number]
        page_width = float(pages[page_number - 1].width)

        # Tables are interleaved with the lines by vertical position rather
        # than emitted up front. Emitting them first filed every table under
        # whatever section was open at the *end* of the previous page — on a
        # page opening with "3 Fault tracing", its own table came out under
        # "Front matter", which is a citation pointing at the wrong part of
        # the manual.
        page_tables = tables_by_page.get(page_number, [])
        table_bands = [(float(t.bbox[1]), float(t.bbox[3])) for t in page_tables]
        pending = sorted(((float(t.bbox[1]), t) for t in page_tables), key=lambda pair: pair[0])

        for line in page_lines:
            # Any table starting above this line belongs to the section that
            # was open when the line above it was read.
            _flush_tables_above(pending, limit=line.top, page=page_number, stack=stack, into=blocks)

            # Lines inside a detected table were already emitted as part of it;
            # repeating them as paragraphs would duplicate the content and give
            # a reader two citations for one fact.
            if any(top <= line.top <= bottom for top, bottom in table_bands):
                continue

            if line.max_gap > page_width * COLUMN_GAP_RATIO:
                # Two columns read as one line. Reported rather than guessed
                # at: the merged sentence would be indexed as something the
                # manual never said.
                raise UnreadableDocumentError(
                    f"page {page_number} appears to be laid out in columns; "
                    "reading order cannot be determined"
                )

            is_heading = line.size >= body * HEADING_SIZE_RATIO or (line.bold and line.size > body)
            if is_heading:
                level = _heading_level(line.size, body)
                del stack[level - 1 :]
                stack.append(line.text)
                blocks.append(
                    StructuralBlock(
                        kind=BlockKind.HEADING,
                        text=line.text,
                        page=page_number,
                        section=_section_path(stack),
                        level=level,
                    )
                )
                continue

            blocks.append(
                StructuralBlock(
                    kind=BlockKind.PARAGRAPH,
                    text=line.text,
                    page=page_number,
                    section=_section_path(stack),
                )
            )

        # A table below every line on the page, which the loop never reached.
        _flush_tables_above(pending, limit=float("inf"), page=page_number, stack=stack, into=blocks)

    logger.info(
        "structure.extracted",
        document_id=document_id,
        pages=len(per_page),
        blocks=len(blocks),
        tables=sum(len(t) for t in tables_by_page.values()),
    )
    return StructureMap(blocks=blocks)
