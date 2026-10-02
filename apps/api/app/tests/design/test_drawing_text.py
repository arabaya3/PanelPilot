"""Tests for `app/design/drawing_text.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.design import drawing_text, notes, pages

_WEB = Path(__file__).resolve().parents[4] / "web" / "src" / "messages"
_LOCALE = Path(drawing_text.__file__).parent / "locale"


def test_words_in_english_are_the_source_text() -> None:
    say = drawing_text.Words("en")
    assert say("Parts list") == "Parts list"
    assert say("{number} of {total}", number=3, total=9) == "3 of 9"


def test_words_in_arabic() -> None:
    say = drawing_text.Words("ar")
    assert say("Parts list") == "قائمة المواد"
    assert say("{number} of {total}", number=3, total=9) == "3 من 9"
    # A phrase the catalogue lacks falls back to English.
    assert say("Not in the catalogue") == "Not in the catalogue"


def test_an_unknown_language_is_drawn_in_english() -> None:
    say = drawing_text.Words("fr")
    assert say.language == "en"
    assert say("Parts list") == "Parts list"


def test_note_in_arabic_with_its_plural_and_select() -> None:
    say = drawing_text.Words("ar")
    assert say.note(notes.note("fed_from", board="MDB")) == "مغذّاة من MDB."
    assert say.note(notes.note("spare_ways", count=3, percent=20)).startswith("اترك 3 مخارج")
    assert say.note(notes.note("spare_ways", count=1, percent=20)).startswith("اترك مخرجاً")
    split = notes.note(
        "split_points", load="Hall", points=4, watts=60, power="0.24", source="given"
    )
    assert say.note(split).endswith("من الوصف")
    # English is the note's own text.
    english = notes.note("fed_from", board="MDB")
    assert drawing_text.Words("en").note(english) == english.text


@pytest.mark.parametrize(
    ("message", "params", "expected"),
    [
        ("{a} and {b}", {"a": "1", "b": "2"}, "1 and 2"),
        ("{n, plural, =0 {none} one {# item} other {# items}}", {"n": "0"}, "none"),
        ("{n, plural, one {# item} other {# items}}", {"n": "5"}, "5 items"),
        ("{k, select, a {A {x}} other {B}}", {"k": "a", "x": "!"}, "A !"),
        ("{missing}", {}, "{missing}"),
    ],
)
def test_format_message(message: str, params: dict[str, str], expected: str) -> None:
    assert drawing_text.format_message(message, params, "en") == expected


@pytest.mark.parametrize(
    ("count", "category"),
    [(0, "zero"), (1, "one"), (2, "two"), (5, "few"), (11, "many"), (100, "other"), (103, "few")],
)
def test_arabic_plural(count: int, category: str) -> None:
    assert drawing_text._arabic_plural(count) == category


@pytest.mark.parametrize("language", ["ar", "he"])
def test_the_drawing_says_every_note_as_the_page_does(language: str) -> None:
    """One sentence per note per language, on the page and on the drawing."""
    drawing = json.loads((_LOCALE / f"{language}.json").read_text(encoding="utf-8"))
    page = json.loads((_WEB / f"{language}.json").read_text(encoding="utf-8"))
    assert drawing["note"] == page["design"]["note"]


def test_hebrew_has_every_phrase_arabic_has() -> None:
    arabic = json.loads((_LOCALE / "ar.json").read_text(encoding="utf-8"))["drawing"]
    hebrew = json.loads((_LOCALE / "he.json").read_text(encoding="utf-8"))["drawing"]
    assert set(hebrew) == set(arabic)


def test_words_in_hebrew_with_its_plural() -> None:
    say = drawing_text.Words("he")
    assert say("Board") == "לוח"
    assert (
        drawing_text.format_message(
            "{count, plural, one {אחד} two {שניים} other {# רבים}}", {"count": "2"}, "he"
        )
        == "שניים"
    )
    assert (
        drawing_text.format_message(
            "{count, plural, one {אחד} other {# רבים}}", {"count": "5"}, "he"
        )
        == "5 רבים"
    )


def test_every_drawing_phrase_has_an_arabic_one() -> None:
    drawing = json.loads((_LOCALE / "ar.json").read_text(encoding="utf-8"))["drawing"]
    english = {*pages._TITLE_LABELS.values(), *pages._KIND_NAMES.values()}
    assert english <= set(drawing)
