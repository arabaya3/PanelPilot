"""The words a drawing set is written in, by the company's drawing language.

The drawing's own words (page titles, title-block labels, table headings)
and the design's notes are looked up in ``locale/<language>.json``. Notes
there are the same sentences the page shows in that language (a test keeps
the two the same), in ICU message format; this module renders the subset
they use: ``{name}``, ``{name, plural, ...}`` with ``#``, and
``{name, select, ...}``.

English is the drawing's source text, so it needs no catalogue; a language
with none, or a word missing from one, falls back to English rather than
leaving a gap.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from app.design.notes import TEMPLATES
from app.models.schemas.design import DesignNote

_LOCALES = Path(__file__).parent / "locale"

#: Drawing languages with a catalogue; any other is drawn in English.
LANGUAGES = frozenset({"en", "ar", "he"})


@cache
def _catalogue(language: str) -> Mapping[str, Mapping[str, str]]:
    path = _LOCALES / f"{language}.json"
    if language == "en" or language not in LANGUAGES or not path.is_file():
        return {"drawing": {}, "note": {}}
    loaded: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _arabic_plural(count: int) -> str:
    """The CLDR plural category of a whole number in Arabic."""
    if count == 0:
        return "zero"
    if count == 1:
        return "one"
    if count == 2:
        return "two"
    if 3 <= count % 100 <= 10:
        return "few"
    if 11 <= count % 100 <= 99:
        return "many"
    return "other"


def _plural(language: str, value: str) -> str:
    try:
        count = int(value)
    except ValueError:
        return "other"
    if language == "ar":
        return _arabic_plural(count)
    if language == "he":
        # CLDR: one, two, and other for every other count.
        return {1: "one", 2: "two"}.get(count, "other")
    return "one" if count == 1 else "other"


def _closing(text: str, start: int) -> int:
    """The index of the brace closing the one opened just before ``start``."""
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unbalanced braces in {text!r}")


def _branches(body: str) -> dict[str, str]:
    """The ``key {text}`` pairs of a plural or select."""
    branches: dict[str, str] = {}
    index = 0
    while index < len(body):
        opening = body.find("{", index)
        if opening < 0:
            break
        end = _closing(body, opening + 1)
        branches[body[index:opening].strip()] = body[opening + 1 : end]
        index = end + 1
    return branches


def format_message(message: str, params: Mapping[str, str], language: str) -> str:
    """Render an ICU message with its params.

    Args:
        message: The message, as the catalogue holds it.
        params: The values it names, as text.
        language: For its plural rules.

    Returns:
        The text. A param the message names but ``params`` lacks is left as
        its name in braces, so the gap shows rather than hides.
    """
    out: list[str] = []
    index = 0
    while index < len(message):
        char = message[index]
        if char != "{":
            out.append(char)
            index += 1
            continue
        end = _closing(message, index + 1)
        inner = message[index + 1 : end]
        index = end + 1
        name, _, rest = (part.strip() for part in inner.partition(","))
        if not rest:
            out.append(params.get(name, "{" + name + "}"))
            continue
        kind, _, body = (part.strip() for part in rest.partition(","))
        branches = _branches(body)
        value = params.get(name, "")
        if kind == "plural":
            key = f"={value}" if f"={value}" in branches else _plural(language, value)
            chosen = branches.get(key, branches.get("other", ""))
            out.append(format_message(chosen.replace("#", value), params, language))
        else:
            chosen = branches.get(value, branches.get("other", ""))
            out.append(format_message(chosen, params, language))
    return "".join(out)


class Words:
    """A drawing's words in one language.

    Attributes:
        language: The language they are in; English where no catalogue is held.
    """

    def __init__(self, language: str) -> None:
        """Pick the catalogue for a language.

        Args:
            language: The company profile's drawing language.
        """
        self.language = language if language in LANGUAGES else "en"

    def __call__(self, text: str, **params: object) -> str:
        """Say one of the drawing's own phrases.

        Args:
            text: The English phrase, as a ``str.format`` template.
            **params: The values it names.

        Returns:
            The phrase in this language, or in English if it has none.
        """
        template = _catalogue(self.language)["drawing"].get(text, text)
        return template.format(**params) if params else template

    def note(self, made: DesignNote) -> str:
        """Say a design note.

        Args:
            made: The note.

        Returns:
            Its sentence in this language, or its English text where the
            catalogue has no sentence for its code.
        """
        message = _catalogue(self.language)["note"].get(made.code)
        if message is None or made.code not in TEMPLATES:
            return made.text
        return format_message(message, made.params, self.language)
