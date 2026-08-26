"""Tests for `app/models/schemas/locale.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.models.schemas.locale import Locale


def test_the_supported_languages_are_pinned() -> None:
    """Three, and adding a fourth is a decision.

    An open string would let a caller ask for a language the corpus does not
    support and get whatever the model felt like, which is worse than a
    refusal.
    """
    assert {locale.value for locale in Locale} == {"en", "ar", "he"}


@pytest.mark.parametrize(
    ("locale", "rtl"), [(Locale.ENGLISH, False), (Locale.ARABIC, True), (Locale.HEBREW, True)]
)
def test_text_direction_is_reported(locale: Locale, rtl: bool) -> None:
    assert locale.is_rtl is rtl


def test_sharing_a_direction_does_not_make_two_locales_the_same() -> None:
    """Arabic and Hebrew are both RTL and that is where it ends.

    Text direction is typography. Hebrew technical vocabulary needs its own
    review rather than being assumed covered by "we handled RTL", which is
    the assumption this project was warned against from the start.
    """
    rtl = [locale for locale in Locale if locale.is_rtl]
    assert len(rtl) == 2, "both RTL locales should be present"
    # Same direction, different languages — so nothing may treat direction as
    # a proxy for which language it is.
    assert len({locale.english_name for locale in rtl}) == 2


@pytest.mark.parametrize("locale", list(Locale))
def test_every_locale_has_an_english_name(locale: Locale) -> None:
    """Used inside an English system prompt, so every member needs one.

    A missing entry would raise at generation time, on the one request that
    happened to use that language.
    """
    assert locale.english_name


def test_the_english_names_are_distinct() -> None:
    """Each locale names a distinct language.

    Two sharing a name would give the model an ambiguous instruction and
    produce whichever language it guessed.
    """
    assert len({locale.english_name for locale in Locale}) == len(Locale)
