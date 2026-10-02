"""Arabic and Hebrew text, laid out for a PDF that draws glyphs left to right.

A PDF page shows a string's glyphs in the order given, one after the other,
and knows nothing of joining or direction. So a line holding Arabic is
handed over the way a reader sees it:

* Arabic letters are replaced by their joined presentation forms
  (``arabic_reshaper``), which the drawing font holds.
* The line is put in visual order. This is a reduced form of the Unicode
  bidirectional algorithm (UAX #9), enough for one line of a drawing label:
  runs of right-to-left letters are reversed, and runs of left-to-right
  ones (Latin, digits, "-Q3", "10 kA") are kept as written. Neutral marks
  take the direction of the text around them, or the line's where the two
  sides differ; a sign that belongs to a number ("3.5", "30 %") stays
  with it, as does a designation's prefix ("-Q3"). Mirrored brackets are
  swapped in a reversed run.

The line's own direction is that of its first letter, digits aside. Text with no
right-to-left letter is returned as it was.
"""

from __future__ import annotations

import unicodedata

import arabic_reshaper

_MIRRORED = str.maketrans("()[]{}<>«»", ")(][}{><»«")

#: Marks kept with a number they touch: decimal and thousands separators,
#: signs, and units written against it.
_NUMBER_MARKS = frozenset(".,:/+-%°‰")

#: IEC 81346 prefix signs, kept with the Latin name they open ("-Q3", "=MDB").
_PREFIX_MARKS = frozenset("-=+")


def _strong(char: str) -> str | None:
    """``"R"`` for a right-to-left letter, ``"L"`` for any other letter or digit."""
    kind = unicodedata.bidirectional(char)
    if kind in ("R", "AL"):
        return "R"
    if kind in ("L", "EN", "AN") or char.isdigit():
        return "L"
    return None


def has_rtl(text: str) -> bool:
    """Whether a text holds any right-to-left letter.

    Args:
        text: Any text.

    Returns:
        True if it holds Arabic or Hebrew.
    """
    return any(_strong(char) == "R" for char in text)


def _directions(text: str) -> tuple[list[str], str]:
    """Each character's resolved direction, and the line's own."""
    marks: list[str | None] = [_strong(char) for char in text]
    # A mark between, or next to, digits belongs to the number.
    for index, char in enumerate(text):
        if marks[index] is None and char in _NUMBER_MARKS:
            before = text[index - 1] if index else ""
            after = text[index + 1] if index + 1 < len(text) else ""
            if before.isdigit() or after.isdigit():
                marks[index] = "L"
        following = text[index + 1] if index + 1 < len(text) else ""
        if (
            marks[index] is None
            and char in _PREFIX_MARKS
            and following
            and _strong(following) == "L"
        ):
            marks[index] = "L"
    letters = (unicodedata.bidirectional(char) for char in text)
    base = next(
        ("R" if kind in ("R", "AL") else "L" for kind in letters if kind in ("L", "R", "AL")), "L"
    )
    resolved: list[str] = []
    for index, mark in enumerate(marks):
        if mark is not None:
            resolved.append(mark)
            continue
        before = next((m for m in reversed(marks[:index]) if m is not None), base)
        after = next((m for m in marks[index + 1 :] if m is not None), base)
        resolved.append(before if before == after else base)
    return resolved, base


def visual(text: str) -> str:
    """Lay one line out in the order its glyphs are drawn, left to right.

    Args:
        text: The line, in logical (typed) order.

    Returns:
        The line shaped and reordered; unchanged if it holds no Arabic or
        Hebrew.
    """
    if not has_rtl(text):
        return text
    shaped = arabic_reshaper.reshape(text)
    directions, base = _directions(shaped)
    runs: list[tuple[str, str]] = []
    for char, direction in zip(shaped, directions, strict=True):
        if runs and runs[-1][0] == direction:
            runs[-1] = (direction, runs[-1][1] + char)
        else:
            runs.append((direction, char))
    laid = [run[::-1].translate(_MIRRORED) if direction == "R" else run for direction, run in runs]
    if base == "R":
        laid.reverse()
    return "".join(laid)
