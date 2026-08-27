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

#: A heading is short. Manuals do not set running prose as headings, and this
#: is what stops a page of small-print boilerplate — which can legitimately
#: outweigh the body text and capture the size estimate — from promoting whole
#: sentences into section paths.
#:
#: Measured rather than guessed: real section headings in the fixtures run 14
#: to 22 characters ("1 Introduction", "3.2 Overcurrent faults"), while the
#: prose that was wrongly promoted ran 42 to 48. 32 sits in that gap with room
#: on both sides. A longer heading is possible but rare, and losing one costs
#: a section path; promoting a sentence costs every citation beneath it.
MAX_HEADING_CHARS = 32

#: A column boundary is a vertical whitespace corridor that persists down the
#: page, not one wide gap on one line. A footer with a document code on the
#: left and a page number on the right has exactly one such gap, and refusing
#: the document over it discards an entire manual.
MIN_COLUMN_LINES = 4

#: A page whose wide-gap lines are at least this share of its lines is
#: columnar however few there are. Without it, a three-line two-column block
#: fell below the count floor and was silently interleaved.
COLUMN_LINE_SHARE = 0.5

#: A gap wider than this is a gutter to a right-aligned element — a contents
#: page number, a footer — rather than a boundary between two columns of text.
MAX_COLUMN_GAP_RATIO = 0.45


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
    """

    text: str
    page: int
    top: float
    size: float
    bold: bool
    max_gap: float
    gap_start: float


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
        text = "".join(str(c["text"]) for c in row).strip()
        if not text:
            continue

        gap = 0.0
        gap_start = 0.0
        for previous, current in itertools.pairwise(row):
            width = float(current["x0"]) - float(previous["x1"])
            if width > gap:
                gap = width
                gap_start = float(previous["x1"])

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
    # Bounded above as well as below. A contents page right-aligns its page
    # numbers, leaving a gap spanning ~64% of the page — a gutter to a lone
    # number, not a column of text. Real column gaps measured 17-33%. Without
    # the ceiling, adding a proportion floor for short pages made a three-line
    # contents page refuse.
    threshold = page_width * COLUMN_GAP_RATIO
    ceiling = page_width * MAX_COLUMN_GAP_RATIO
    wide = [line for line in lines if threshold < line.max_gap <= ceiling]
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

    Size alone is not enough, and three attempts to fix this by improving the
    body-size estimate all failed on the same page: fourteen lines of 6pt
    disclaimer carry 1218 characters against 90 of body text, so *no*
    weight-based vote picks 10pt there. Every one of those attempts left two
    ordinary sentences promoted to level-1 headings, producing section paths
    like "replacing the relay module in the cubicle." — a citation nobody can
    resolve against a contents page.

    A heading is bigger than body text **and short**. Manuals do not set
    running prose as headings, and length is the property that separates the
    two regardless of what the size estimate got wrong. Bold is accepted as an
    alternative to being larger, since many manuals set same-size bold
    headings, but never on its own for a long line.
    """
    if len(line.text) > MAX_HEADING_CHARS:
        return False

    # Larger than body text is a heading on its own, bold or not. Requiring
    # bold unconditionally was worse than the bug it replaced: a manual with
    # 18pt regular headings over 10pt body — unambiguous by size — produced
    # *zero* headings, every block landing under "Front matter", silently and
    # for every page of the document. The original bug produced some wrong
    # section paths on one pathological page; this produced none at all across
    # a whole document class.
    # At body size, nothing qualifies. `WARNING: Do not touch the terminals.`
    # is bold, short and 10pt in a 10pt document, and treating same-size bold
    # as structure made it a level-4 heading that captured the section path
    # for everything after it. Safety callouts and bold lead-ins are
    # ubiquitous in manuals; same-size bold is emphasis, not structure.
    #
    # Being larger than body text is therefore the whole test, bold or not.
    # Requiring bold was worse than the bug it replaced: an 18pt-regular over
    # 10pt-body manual produced *zero* headings, every block under "Front
    # matter", silently and for every page.
    return line.size >= body * HEADING_SIZE_RATIO


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
    for row in table.extract():
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

    A **positive** signal is required, not the absence of a negative one. The
    first version accepted "carries no header-like row" as evidence of
    continuation, and `_looks_like_header` reports exactly that for an
    all-text table — parameter names and descriptions, which manuals are full
    of. So an unrelated two-column parameter table opening page 2 was fused
    onto page 1's fault-code table: twelve rows in one block, page 1, section
    "1 Fault codes", with three parameter rows presented as fault codes on a
    page they never appeared on.

    A continuation must therefore repeat the header, which is what manuals
    actually do, or be visually contiguous — a table starting at the very top
    of the page, where a break would land. Absence of evidence is not
    evidence.
    """
    if not previous or not following:
        return False
    if len(previous[0]) != len(following[0]):
        return False
    return following[0] == previous[0]


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
    pending: list[tuple[float, Any]],
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
        pending: Remaining ``(top, table)`` pairs for this page, ascending.
            Consumed in place.
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
        _, table = pending.pop(0)
        rows = _rows_of(table)
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
    # Index of a block in `blocks` -> the last page its content came from.
    last_page: dict[int, int] = {}

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
            # Reported per page rather than per line, and only when a gap band
            # persists. The earlier check refused on a single wide gap, which
            # a standard manual footer (document code left, page number right)
            # produces on every page — so one footer discarded the whole
            # manual, and `prepare_documents` recorded it as a parse failure
            # with no hint that the cause was a heuristic.
            raise UnreadableDocumentError(
                f"page {page_number} appears to be laid out in columns; "
                "reading order cannot be determined"
            )

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
        _flush_tables_above(
            pending,
            limit=float("inf"),
            page=page_number,
            stack=stack,
            into=blocks,
            last_page=last_page,
        )

    logger.info(
        "structure.extracted",
        document_id=document_id,
        pages=len(per_page),
        blocks=len(blocks),
        tables=sum(len(t) for t in tables_by_page.values()),
    )
    return StructureMap(blocks=blocks)
