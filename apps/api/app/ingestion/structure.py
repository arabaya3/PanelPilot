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
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pdfplumber
import structlog
from pdfminer.pdfpage import PDFPage

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

#: A section number: ``3``, ``3.2``, ``3.2.1``, optionally followed by the
#: heading text. Manuals number their sections and never their sentences, so
#: this is the single most reliable signal available here.
_NUMBERED_HEADING = re.compile(r"^\d+(\.\d+)*\.?(\s+\S|$)")

#: A fault or alarm code opening a line: Siemens' "F30002", "A08757". A list
#: manual is a heading per code; titled at more than a few words, as most
#: are, they failed the unnumbered-heading length check and every such fault
#: was filed under the one before it -- a citation to the wrong fault.
_CODE_HEADING = re.compile(r"^[A-Z]{1,2}\d{4,5}\s+\S")

#: A word of three letters or more, in any script.
_WORD = re.compile(r"[^\W\d_]{3,}")

#: A continuation banner: "Table 3 (continued)", "cont.", and so on.
_CONTINUED = re.compile(r"\bcont(inued)?\b", re.IGNORECASE)

#: Characters further apart than this share of the font size are separate
#: words. Many PDFs position words rather than emit a space glyph between
#: them; joined without one, Siemens' "4.2 List of faults and alarms" read
#: "4.2List of faults and alarms" in every section path.
WORD_GAP_RATIO = 0.2

#: Page furniture -- running headers and footers -- sits in this share of the
#: page at the top and at the bottom.
FURNITURE_MARGIN_RATIO = 0.12

#: A margin line repeated verbatim at the same height on this many pages is
#: furniture, not content. Siemens repeats the chapter and section titles at
#: the top of every page; read as headings, they re-opened the section on
#: each page and nested it under itself: "4.2List of faults and alarms >
#: 4.2List of faults and alarms".
MIN_FURNITURE_PAGES = 3

#: Below this, a page is treated as having no usable text layer.
MIN_CHARS_PER_PAGE = 2

#: A heading is short. Manuals do not set running prose as headings, and this
#: is what stops a page of small-print boilerplate — which can legitimately
#: outweigh the body text and capture the size estimate — from promoting whole
#: sentences into section paths.
#:
#: A backstop, not the discriminator. At 32 it silently demoted real
#: subsection headings; the numbering and sentence-termination checks in
#: ``_is_heading`` do the actual separating, so this only has to exclude
#: something no manual would set as a heading.
MAX_HEADING_CHARS = 120

#: An unnumbered heading carries no section number to identify it, so it has
#: to be recognised by shape alone: a few words, no sentence terminator. A
#: numbered heading may be far longer.
MAX_UNNUMBERED_HEADING_CHARS = 32

#: A column boundary is a vertical whitespace corridor that persists down the
#: page, not one wide gap on one line. A footer with a document code on the
#: left and a page number on the right has exactly one such gap, and refusing
#: the document over it discards an entire manual.
MIN_COLUMN_LINES = 4

#: A page whose wide-gap lines are at least this share of its lines is
#: columnar however few there are. Without it, a three-line two-column block
#: fell below the count floor and was silently interleaved.
COLUMN_LINE_SHARE = 0.5

#: Each side of a real column gutter carries at least this much text. A
#: contents page's gap has a page number on the right — one or two characters
#: — which is what separates it from a column boundary regardless of width.
MIN_COLUMN_SIDE_CHARS = 6

#: The most pages one document may have. The longest real manuals in the
#: three libraries run to about a thousand pages; past twice that, a file is
#: far more likely a trap than a manual -- a few kilobytes of PDF can declare
#: thousands of pages, each costing a full layout pass.
MAX_PAGES = 2000

#: Wall-clock budget for one document's extraction: this floor, or
#: ``EXTRACTION_S_PER_PAGE`` for each of its pages if that is more. Checked
#: between pages, so a single pathological page can overrun it, but a
#: document cannot hold the crawl job open page after page.
#:
#: A flat 300 s contradicted ``MAX_PAGES``: Yaskawa's 836- and 1132-page
#: technical references, dense with tables, were refused at pages 815 and
#: 401. Scaling with the page count keeps the bound -- ``MAX_PAGES`` caps it
#: at 2000 s -- and lets a real manual of that length through.
EXTRACTION_DEADLINE_S = 300.0
EXTRACTION_S_PER_PAGE = 1.0


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
        gap_start: Where that widest gap begins, so a corridor can be
            recognised by several lines sharing one horizontal position.
        split_at_gap: The text either side of that gap, for telling a column
            gutter from the run-up to a right-aligned page number.
    """

    text: str
    page: int
    top: float
    size: float
    bold: bool
    max_gap: float
    gap_start: float
    split_at_gap: tuple[str, str]


def _join(chars: Sequence[dict[str, Any]]) -> str:
    """Join a line's characters, restoring the spaces their positions imply.

    Args:
        chars: One line's characters, left to right.

    Returns:
        The text, with a space wherever a gap wider than ``WORD_GAP_RATIO`` of
        the font size separates two non-space characters.
    """
    parts: list[str] = []
    previous: dict[str, Any] | None = None
    for char in chars:
        text = str(char["text"])
        if previous is not None and not text.isspace() and not str(previous["text"]).isspace():
            gap = float(char["x0"]) - float(previous["x1"])
            if gap > WORD_GAP_RATIO * float(char.get("size", 0.0) or 0.0):
                parts.append(" ")
        parts.append(text)
        previous = char
    return "".join(parts)


def _drop_furniture(lines_by_page: dict[int, list[_Line]], heights: dict[int, float]) -> None:
    """Remove running headers and footers from every page, in place.

    Args:
        lines_by_page: Each page's lines.
        heights: Each page's height.

    A line is furniture when the same text sits at the same height within
    the top or bottom margin of at least ``MIN_FURNITURE_PAGES`` pages that
    carry other content too. A
    real heading appears once; a page number changes from page to page; only
    furniture repeats verbatim in the margin.
    """

    def in_margin(line: _Line) -> bool:
        height = heights.get(line.page)
        if not height:
            return False
        margin = height * FURNITURE_MARGIN_RATIO
        return not margin < line.top < height - margin

    def key(line: _Line) -> tuple[str, int] | None:
        if not in_margin(line):
            return None
        return (line.text, round(line.top / 2))

    def page_number(line: _Line) -> bool:
        # A bare number in the margin. It changes every page, so it never
        # repeats, but it matches the section-number pattern: "726" became a
        # heading and filed the next page's content under it.
        return in_margin(line) and line.text.isdigit()

    pages: dict[tuple[str, int], set[int]] = {}
    for page, lines in lines_by_page.items():
        # Furniture frames content. A page whose only line is the repeated
        # one has nothing else to frame, and dropping it would drop the page.
        if len(lines) < 2:
            continue
        for line in lines:
            k = key(line)
            if k is not None:
                pages.setdefault(k, set()).add(page)
    furniture = {k for k, seen in pages.items() if len(seen) >= MIN_FURNITURE_PAGES}
    for page, lines in lines_by_page.items():
        if len(lines) < 2:
            continue
        lines_by_page[page] = [
            line for line in lines if key(line) not in furniture and not page_number(line)
        ]


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
        # Rotated text is dropped rather than read. Bucketing by `top` alone,
        # a 90-degree axis label becomes one block per character in reverse
        # order — forty uncitable chunks from one figure label. pdfplumber
        # reports orientation, so this is a check rather than a guess.
        if not char.get("upright", True):
            continue
        key = int(float(char["top"]) / LINE_TOLERANCE_PT)
        buckets.setdefault(key, []).append(char)

    lines: list[_Line] = []
    for key in sorted(buckets):
        row = sorted(buckets[key], key=lambda c: float(c["x0"]))
        text = _join(row).strip()
        if not text:
            continue

        gap = 0.0
        gap_start = 0.0
        gap_index = 0
        for index, (previous, current) in enumerate(itertools.pairwise(row)):
            width = float(current["x0"]) - float(previous["x1"])
            if width > gap:
                gap = width
                gap_start = float(previous["x1"])
                gap_index = index + 1

        fonts = [str(c.get("fontname", "")) for c in row]
        lines.append(
            _Line(
                text=text,
                page=page_number,
                top=float(row[0]["top"]),
                size=max(float(c.get("size", 0.0)) for c in row),
                bold=sum("bold" in f.lower() for f in fonts) > len(fonts) / 2,
                max_gap=gap,
                gap_start=gap_start,
                split_at_gap=(_join(row[:gap_index]), _join(row[gap_index:])),
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

    # Ties break toward the larger size. The alternative to body text is
    # mostly *smaller* — captions, footnotes, table cells — so on a 10pt/8pt
    # tie, picking 8 would make body text 1.25x "body" and promote all of it.
    return max(weights, key=lambda size: (weights[size], size))


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


def _text_on_both_sides(line: _Line) -> bool:
    """Report whether a line's widest gap has real text on either side.

    Args:
        line: The line under test.

    Returns:
        ``True`` when both sides carry more than a page number's worth.

    What distinguishes a column gutter from the gap before a right-aligned
    page number: the number is one or two characters, a column carries a
    clause.

    Belt and braces, and honestly labelled as such. Mutation testing showed
    the corridor check alone already spares a contents page — varying title
    lengths mean those gaps do not share one vertical band — so removing this
    changes no observed behaviour and no test can pin it. It stays because it
    is the *reason* the two cases differ, and the corridor check happens to
    agree; a contents page with uniform title lengths would produce a corridor
    and need this. It should not be mistaken for a tested guarantee.
    """
    before, after = line.split_at_gap
    return (
        len(before.strip()) >= MIN_COLUMN_SIDE_CHARS and len(after.strip()) >= MIN_COLUMN_SIDE_CHARS
    )


def _looks_columnar(lines: Sequence[_Line], *, page_width: float) -> bool:
    """Report whether a page is laid out in columns.

    Args:
        lines: The page's lines.
        page_width: Page width, so the threshold holds for A4 and Letter.

    Returns:
        ``True`` when several lines share a wide gap at a consistent
        horizontal position — a vertical whitespace corridor.

    Requiring a *corridor* rather than a single wide gap is what separates a
    two-column page from ordinary furniture. A footer with a document code on
    the left and a page number on the right has one wide gap; a justified
    paragraph and a contents page with dot leaders each have one too. Refusing
    on any of those discards a readable manual entirely, which is a worse
    outcome than the interleaving the check exists to prevent.
    """
    # A gutter between two columns has substantial text on BOTH sides. A
    # contents page's wide gap has a page number on the right — one or two
    # characters. That is the property that separates them, and it holds
    # whatever the gap measures.
    #
    # An upper bound on gap width was the earlier attempt, and it let a real
    # two-column page through: narrow columns with a wide margin exceeded the
    # ceiling and were interleaved into sentences the manual never contained.
    threshold = page_width * COLUMN_GAP_RATIO
    wide = [line for line in lines if line.max_gap > threshold and _text_on_both_sides(line)]
    if not wide:
        return False

    # Two floors, either of which is enough. The line count catches a full
    # two-column page; the proportion catches a short one — three lines per
    # column sat below the count and was interleaved into
    # "outgoing cables.phases.", text the manual never contained, emitted as
    # an ordinary paragraph with a real page number.
    #
    # A page whose wide-gap lines are most of its content is columnar however
    # few they are; a footer or a contents line is a small fraction of a page.
    enough_lines = len(wide) >= MIN_COLUMN_LINES
    dominates = len(wide) >= 2 and len(wide) >= len(lines) * COLUMN_LINE_SHARE
    if not (enough_lines or dominates):
        return False

    # A corridor is where the gaps OVERLAP, not where they start. Column text
    # is ragged-right, so the gap on each line begins wherever that line
    # happened to end — anchoring on the start point put five genuinely
    # columnar lines 41pt apart and found no corridor at all. What they share
    # is the vertical band every one of them spans.
    spans = [(line.gap_start, line.gap_start + line.max_gap) for line in wide]
    best = 1
    for start, end in spans:
        overlapping = sum(1 for s2, e2 in spans if s2 < end and start < e2)
        best = max(best, overlapping)
    # The corridor must be shared by as many lines as admitted the page. A
    # page carried in on the proportion floor has fewer than MIN_COLUMN_LINES
    # wide lines by definition, so demanding that many overlaps here would
    # discard it again — which is what left a three-line two-column block
    # interleaved after the floor was added.
    required = MIN_COLUMN_LINES if enough_lines else 2
    return best >= required


def _is_heading(line: _Line, *, body: float) -> bool:
    """Decide whether a line is a heading.

    Args:
        line: The line under test.
        body: The document's estimated body size.

    Returns:
        ``True`` when the line reads as a heading rather than as prose.

    Three rounds of review pushed this through four one-dimensional rules —
    size ratio, then bold required, then an absolute length cap — and each one
    failed on the other side. A 32-character cap silently demoted
    ``3.2.1 Overcurrent protection settings`` to a paragraph and filed its
    body under the parent section: not a missing path, a confidently wrong
    one. Requiring bold produced zero headings on a size-only manual; not
    considering bold produced zero on a same-size-bold manual. Both are common
    layouts.

    The signals below actually separate headings from prose, and were already
    in the data:

    * A **numbering prefix** (``3``, ``3.2``, ``3.2.1``) is near-decisive.
      Manuals number their sections and do not number their sentences.
    * **Sentence termination.** The boilerplate that motivated the length cap
      ends in a full stop; headings do not. That one check separates
      ``replacing the relay module in the cubicle.`` from
      ``3.2.1 Overcurrent protection settings`` with no cap at all.
    * **Bold or larger**, as corroboration rather than as the decision.

    A bold same-size safety callout — ``WARNING: Do not touch the
    terminals.`` — is still rejected: it ends in a full stop and carries no
    number.
    """
    text = line.text.strip()
    if not text or len(text) > MAX_HEADING_CHARS:
        return False
    # A number alone names nothing: a page number in a contents list, or a
    # chapter numeral set on its own line above its title. As headings they
    # gave section paths like "Table of contents > 234".
    if text.replace(".", "").isdigit():
        return False

    prominent = line.size >= body * HEADING_SIZE_RATIO or (line.bold and line.size >= body)
    if not prominent:
        return False

    if _NUMBERED_HEADING.match(text) or _CODE_HEADING.match(text):
        return True

    # Unnumbered headings exist ("Contents", "Safety", "Fault tracing"), and
    # they are both short and unterminated. Termination alone is not enough:
    # a wrapped prose line — "Check the trip circuit supervision output
    # before" — carries no full stop either, because the sentence continues on
    # the next line. Real unnumbered headings are a few words.
    # And a heading names something in words. Found on ABB's installation
    # handbook, whose large-set equations -- "I = I k k = I k", "≤" -- were
    # opening sections of their own.
    if not _WORD.search(text):
        return False
    return len(text) <= MAX_UNNUMBERED_HEADING_CHARS and not text.endswith(
        (".", ":", ";", "!", "?", ",")
    )


def _section_path(stack: Sequence[str]) -> str:
    """Render the current heading stack as a section path.

    Args:
        stack: Headings from outermost to innermost.

    Returns:
        The path, or ``FRONT_MATTER`` when nothing has been seen yet. Never
        empty — an empty section makes a chunk uncitable.
    """
    return " > ".join(stack) if stack else FRONT_MATTER


def _rows_of(table: Any) -> list[list[str]]:
    """Return a table's non-empty rows as trimmed cell lists.

    Args:
        table: A pdfplumber table.

    Returns:
        One list of cells per row that carries any content.
    """
    rows: list[list[str]] = []
    # The same gap rule as body text. pdfplumber's default is a fixed 3 pt, so
    # a manual that positions words 2.5 pt apart instead of emitting spaces --
    # Schneider's framed safety messages -- read "LOSSOFCONTROL".
    for row in table.extract(x_tolerance_ratio=WORD_GAP_RATIO):
        cells = [(cell or "").replace("\n", " ").strip() for cell in row]
        if any(cells):
            rows.append(cells)
    return rows


def _continues(previous: list[list[str]], following: list[list[str]]) -> bool:
    """Report whether one page's table continues onto the next.

    Args:
        previous: Rows of the table ending the earlier page.
        following: Rows of the table opening the later page.

    Returns:
        ``True`` when the two are one table split by a page break.

    Requires a positive signal. An earlier version demanded the header repeat
    *exactly at row zero*, which missed a continuation behind a
    ``Table 3 (continued)`` banner — the header is repeated, one row lower —
    leaving a fragment presenting itself as a complete table, which is the
    failure this whole mechanism exists to prevent.

    **A continuation carrying no header at all is not stitched**, and that is
    a deliberate limit rather than an oversight. Geometry looked like the
    remaining evidence — a table beginning at the top of a page is where a
    break lands — but measurement killed it: an unrelated table opening the
    next page starts at exactly the same position, so the signal does not
    separate the two cases. Requiring the continuation to carry no header of
    its own does not rescue it either, because recognising a header in an
    all-text table is the same undecidable problem.

    So that layout stays two blocks. It is the wrong answer for one real
    case, and it is the safe direction: two fragments of one table are
    visibly two blocks, whereas fusing two different tables presents rows
    under a heading they never appeared under.

    The column count must match in every case: a different shape is a
    different table.
    """
    if not previous or not following:
        return False
    if len(previous[0]) != len(following[0]):
        return False

    header = previous[0]
    # Repeated at the top, or one row down behind a banner.
    if following[0] == header or (len(following) > 1 and following[1] == header):
        return True
    return any(_CONTINUED.search(cell) for cell in following[0])


def _looks_like_header(row: list[str], body: list[list[str]]) -> bool:
    """Guess whether a row is a header rather than data.

    Args:
        row: The candidate row.
        body: The rows beneath it.

    Returns:
        ``True`` when the row is non-numeric and the rows below it are not —
        the shape a header has. Used only to decide whether a continuation
        repeated its header, never to drop content.
    """
    if not body:
        return False

    def numeric(cells: list[str]) -> int:
        return sum(1 for cell in cells if cell and cell.replace(".", "", 1).strip("%A V").isdigit())

    return numeric(row) == 0 and any(numeric(r) for r in body)


def _split_rows(text: str) -> list[list[str]]:
    """Read a rendered table back into rows, for continuation checks.

    Args:
        text: A table block's text.

    Returns:
        Its rows as cell lists.
    """
    return [line.split("\t") for line in text.split("\n") if line]


def _join_rows(rows: list[list[str]]) -> str:
    """Render rows as a table block's text.

    Args:
        rows: Cell lists.

    Returns:
        One line per row, cells tab-separated.
    """
    return "\n".join("\t".join(cells) for cells in rows)


def _table_rows(table: Any) -> str:
    """Render a detected table as text.

    Args:
        table: A pdfplumber table.

    Returns:
        One line per row, cells tab-separated. Kept whole deliberately: this
        becomes an atomic block, and the point of that is that a reader gets
        the entire table or none of it.
    """
    return "\n".join("\t".join(cells) for cells in _rows_of(table))


def _flush_tables_above(
    pending: list[tuple[float, list[list[str]]]],
    *,
    limit: float,
    page: int,
    stack: Sequence[str],
    into: list[StructuralBlock],
    last_page: dict[int, int],
) -> None:
    """Emit any pending table that starts above a point on the page.

    Takes its state as arguments rather than closing over the caller's loop
    variables. As a nested function it captured ``pending``, ``page`` and
    ``stack`` by reference, which worked only because each page redefined it —
    a refactor hoisting the definition would have made every table read the
    final page's section, silently.

    Args:
        pending: Remaining ``(top, rows)`` pairs for this page, ascending,
            with each table's rows already extracted. Consumed in place.
        limit: Emit tables starting at or above this vertical position.
        page: 1-indexed page number.
        stack: The heading stack as it stands at this point in the page.
        into: Block list to append to.
        last_page: Maps a block's index to the last page its content came
            from. A merged table keeps its *first* page as its citation — that
            is where a reader turns — so adjacency cannot be tested against
            it. Comparing against `previous.page` meant a three-page table
            merged pages 1 and 2 and then compared `1 == 2` for page 3,
            leaving an 11-row fragment presenting itself as a whole table.
    """
    while pending and pending[0][0] <= limit:
        _, rows = pending.pop(0)
        if not rows:
            continue

        # A table opening a page may be the previous page's table continuing.
        # Merged rather than emitted separately: each fragment would otherwise
        # be its own atomic block presenting itself as a whole table, which is
        # the failure the atomic-block rule exists to prevent — and the
        # fragments are more credible than a bad guess, because each carries a
        # real page number and section.
        previous = into[-1] if into else None
        if (
            previous is not None
            and previous.kind is BlockKind.TABLE
            and last_page.get(len(into) - 1, previous.page) == page - 1
            and _continues(_split_rows(previous.text), rows)
        ):
            carried = rows[1:] if rows[0] == _split_rows(previous.text)[0] else rows
            into[-1] = previous.model_copy(
                update={"text": previous.text + "\n" + _join_rows(carried)}
            )
            last_page[len(into) - 1] = page
            continue

        into.append(
            StructuralBlock(
                kind=BlockKind.TABLE,
                text=_join_rows(rows),
                page=page,
                section=_section_path(stack),
            )
        )
        last_page[len(into) - 1] = page


def extraction_budget_s(page_count: int) -> float:
    """Return how long a document of this many pages may take to read.

    Args:
        page_count: The document's pages, already capped at ``MAX_PAGES``.

    Returns:
        Seconds: ``EXTRACTION_DEADLINE_S``, or ``EXTRACTION_S_PER_PAGE`` per
        page if that is more.
    """
    return max(EXTRACTION_DEADLINE_S, page_count * EXTRACTION_S_PER_PAGE)


def _count_pages(document: Any) -> int:
    """Count a PDF's pages, refusing one with more than ``MAX_PAGES``.

    Args:
        document: An open pdfplumber PDF.

    Returns:
        The page count.

    Raises:
        UnreadableDocumentError: If there are more than ``MAX_PAGES``.

    Counted by walking pdfminer's page tree lazily and stopping one past the
    cap, rather than through ``document.pages`` (which builds every page
    object first) or the catalogue's ``/Count`` (which the file itself
    declares, and so can understate). A tiny file can declare thousands of
    pages, and the refusal should cost as little as the file did.
    """
    counted = sum(1 for _ in itertools.islice(PDFPage.create_pages(document.doc), MAX_PAGES + 1))
    if counted > MAX_PAGES:
        raise UnreadableDocumentError(f"more than {MAX_PAGES} pages; refusing to extract")
    return counted


def _read_page(
    page: Any,
    lines_by_page: dict[int, list[_Line]],
    tables_by_page: dict[int, list[tuple[float, float, list[list[str]]]]],
    widths: dict[int, float],
    document_id: str,
    heights: dict[int, float] | None = None,
) -> None:
    """Reduce one page to the small records the block pass needs.

    Args:
        page: A pdfplumber page.
        lines_by_page: Filled with the page's lines, keyed by page number.
        tables_by_page: Filled with each detected table's
            ``(top, bottom, rows)``, keyed by page number.
        widths: Filled with the page's width, keyed by page number.
        document_id: For log lines.
        heights: Filled with the page's height, keyed by page number, when
            given; the furniture pass needs it.

    Table rows are extracted here, while the page is open, rather than later
    from retained pdfplumber table objects: those hold a reference to their
    page, and so to its entire parsed layout.
    """
    page_number = int(page.page_number)
    # Some manuals fake bold by printing each glyph twice at the same spot;
    # read as-is, "CHS" comes out "CCHHSS" in the heading a citation shows.
    page = page.dedupe_chars()
    chars = page.chars
    if len(chars) < MIN_CHARS_PER_PAGE:
        # A scan. Skipped rather than failed: a manual with one scanned
        # appendix should still yield its readable pages.
        logger.info("structure.page_without_text", document_id=document_id, page=page_number)
        return
    lines_by_page[page_number] = _group_lines(chars, page_number)
    tables_by_page[page_number] = [
        (float(table.bbox[1]), float(table.bbox[3]), _rows_of(table))
        for table in page.find_tables()
    ]
    widths[page_number] = float(page.width)
    if heights is not None:
        heights[page_number] = float(page.height)


def extract_structure(data: bytes, *, document_id: str = "") -> StructureMap:
    """Read a PDF's structural blocks from its layout.

    Args:
        data: The PDF bytes as crawled.
        document_id: Identifier for log lines; not used for parsing.

    Returns:
        The document's blocks in reading order.

    Raises:
        UnreadableDocumentError: If the file is not a readable PDF, has no text
            layer, is laid out in columns on every page, has more
            than ``MAX_PAGES`` pages, or takes longer than
            ``extraction_budget_s`` allows it to read.

    Pages are read one at a time, and each is reduced to its lines and its
    tables' rows before the next is opened, with pdfplumber's per-page caches
    released in between. Holding every page's characters and table objects
    until the end -- as this once did -- kept each page's whole layout alive:
    a 79 KB, 500-page PDF took 4 GB and 83 seconds.
    """
    # Everything pdfplumber does is inside the try, table extraction included:
    # any failure of the parser on this input is "not a readable PDF", which
    # the caller records per document rather than as a crashed run.
    started = time.monotonic()
    lines_by_page: dict[int, list[_Line]] = {}
    tables_by_page: dict[int, list[tuple[float, float, list[list[str]]]]] = {}
    widths: dict[int, float] = {}
    heights: dict[int, float] = {}
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            page_count = _count_pages(document)
            budget = extraction_budget_s(page_count)
            for page in document.pages:
                if time.monotonic() - started > budget:
                    raise UnreadableDocumentError(
                        f"extraction passed {budget:g}s at page "
                        f"{page.page_number} of {page_count}"
                    )
                try:
                    _read_page(page, lines_by_page, tables_by_page, widths, document_id, heights)
                finally:
                    # Released whether or not the page was usable: the layout
                    # is the expensive part, and nothing below needs it.
                    page.close()
    except UnreadableDocumentError:
        raise
    except Exception as exc:
        raise UnreadableDocumentError(f"could not open PDF: {exc}") from exc

    _drop_furniture(lines_by_page, heights)
    all_lines = list(itertools.chain.from_iterable(lines_by_page.values()))
    if not all_lines:
        raise UnreadableDocumentError("no text layer in any page")

    body = _body_size(all_lines)
    blocks: list[StructuralBlock] = []
    stack: list[str] = []
    # Font size of each open heading, so a same-size heading is recognised as
    # a sibling rather than nested beneath its equal.
    sizes: list[float] = []
    # Index of a block in `blocks` -> the last page its content came from.
    last_page: dict[int, int] = {}
    skipped_pages: list[int] = []

    # Grouped by page once, up front. Filtering the whole document's lines for
    # each page made this quadratic in document length.
    for page_number in sorted(page for page, lines in lines_by_page.items() if lines):
        page_lines = lines_by_page[page_number]
        page_width = widths[page_number]

        # Tables are interleaved with the lines by vertical position rather
        # than emitted up front. Emitting them first filed every table under
        # whatever section was open at the *end* of the previous page — on a
        # page opening with "3 Fault tracing", its own table came out under
        # "Front matter", which is a citation pointing at the wrong part of
        # the manual.
        page_tables = tables_by_page.get(page_number, [])
        table_bands = [(top, bottom) for top, bottom, _ in page_tables]
        pending = sorted(((top, rows) for top, _, rows in page_tables), key=lambda pair: pair[0])

        # Lines inside a detected table are excluded: a wide two-column table
        # has a gap at the same x on every row, which is a corridor by any
        # measure — but it is a table, already read as one, and refusing the
        # page over it would reject exactly the documents this exists to
        # capture.
        outside_tables = [
            line
            for line in page_lines
            if not any(top <= line.top <= bottom for top, bottom in table_bands)
        ]
        if _looks_columnar(outside_tables, page_width=page_width):
            # The page's prose is dropped, not the document. Read line by
            # line, columns interleave into sentences the manual never
            # contained, so this page's text cannot be indexed -- but refusing
            # the whole manual over it cost five of six Siemens manuals, each
            # rejected for one columned page among hundreds of readable ones.
            # Its tables were read as tables and are kept.
            logger.warning(
                "structure.columnar_page_skipped",
                document_id=document_id,
                page=page_number,
            )
            skipped_pages.append(page_number)
            page_lines = [
                line
                for line in page_lines
                if any(top <= line.top <= bottom for top, bottom in table_bands)
            ]

        for line in page_lines:
            # Any table starting above this line belongs to the section that
            # was open when the line above it was read.
            _flush_tables_above(
                pending,
                limit=line.top,
                page=page_number,
                stack=stack,
                into=blocks,
                last_page=last_page,
            )

            # Lines inside a detected table were already emitted as part of it;
            # repeating them as paragraphs would duplicate the content and give
            # a reader two citations for one fact.
            if any(top <= line.top <= bottom for top, bottom in table_bands):
                continue

            is_heading = _is_heading(line, body=body)
            if is_heading and line.text in stack:
                # A heading already open is being repeated, not reopened: a
                # running header on a section too short for the furniture
                # pass to catch. Nesting it under itself gave paths like
                # "1.3 Security information > 1.3 Security information".
                continue
            if is_heading:
                level = _heading_level(line.size, body)
                # Clamped so a heading can never descend more than one level
                # below the stack that is actually open. `_heading_level` maps
                # size ratios onto absolute depths, so a document going H1 to
                # H3 — common, since the buckets are coarse — left the stack
                # too shallow to truncate, and a *sibling* H3 appended instead
                # of replacing: "3 Protection > 3.1 Overcurrent > 3.2 Earth
                # fault", asserting a containment the manual does not have.
                level = min(level, len(stack) + 1)
                # Two headings of the same size are siblings, whatever the
                # coarse level buckets say. Clamping to the open depth alone
                # still let one descend below the other, producing
                # "3 Protection > 3.1 Overcurrent > 3.2 Earth fault" — a
                # containment the manual does not have, inherited by every
                # chunk beneath it.
                if sizes and abs(sizes[-1] - line.size) < 0.1:
                    level = len(sizes)
                del stack[level - 1 :]
                del sizes[level - 1 :]
                sizes.append(line.size)
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
        _flush_tables_above(
            pending,
            limit=float("inf"),
            page=page_number,
            stack=stack,
            into=blocks,
            last_page=last_page,
        )

    if not blocks and skipped_pages:
        raise UnreadableDocumentError(
            f"every readable page is laid out in columns (pages {skipped_pages}); "
            "reading order cannot be determined"
        )

    logger.info(
        "structure.extracted",
        document_id=document_id,
        pages=page_count,
        blocks=len(blocks),
        tables=sum(len(t) for t in tables_by_page.values()),
        columnar_pages_skipped=skipped_pages,
    )
    return StructureMap(blocks=blocks)
