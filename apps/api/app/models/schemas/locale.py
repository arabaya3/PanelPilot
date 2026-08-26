"""The locales a diagnosis can be generated in.

**Three languages, generated directly.** The alternative — generate in English
and machine-translate — stacks a translation layer's errors on top of
retrieval's and generation's, and it is the shortcut that produces "translated
UI chrome around English-only AI output". Arabic is a first-class output
language here, not a post-process.

**Technical tokens never translate.** A fault code, a parameter number, a unit
and a part number mean one thing in every language, and an F0001 rendered as
ف٠٠٠١ is not a fault code any more — it matches nothing in the manual, nothing
in the drive's display, and nothing the engineer can type. The same goes for
transliteration: "ACS880" is the model number whatever the surrounding prose.

**Arabic and Hebrew are both RTL and that is where the similarity ends.**
Sharing a text direction says nothing about technical vocabulary; Hebrew
engineering terminology needs its own review rather than being assumed covered
by "we handled RTL".
"""

from __future__ import annotations

from enum import StrEnum


class Locale(StrEnum):
    """A language a response may be generated in.

    Deliberately a closed set. An open string would let a caller pass anything
    and get whatever the model felt like, which for a language the corpus does
    not support is worse than refusing.
    """

    ENGLISH = "en"
    ARABIC = "ar"
    HEBREW = "he"

    @property
    def is_rtl(self) -> bool:
        """Whether this locale is written right-to-left.

        Used for display isolation, not for generation. It is a typographic
        fact, and deciding technical correctness from it is the mistake this
        module's docstring warns about.
        """
        return self in (Locale.ARABIC, Locale.HEBREW)

    @property
    def english_name(self) -> str:
        """The language's name, for use inside an English system prompt."""
        return {
            Locale.ENGLISH: "English",
            Locale.ARABIC: "Arabic",
            Locale.HEBREW: "Hebrew",
        }[self]
