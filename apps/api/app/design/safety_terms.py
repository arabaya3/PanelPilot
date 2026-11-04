"""Keyword detection of fire-pump and life-safety equipment in a description (M6b/M6c).

Detection only, and a heuristic: a word list can never recognise every way a
description can name life-safety equipment (other phrasings, languages,
misspellings, letters spaced apart, look-alikes not folded here, references
too far away). It narrows what a model can slip through; it is not the gate.
The gate is the user's accept-and-place step and the open items that stay on
the record for review.

Two levels:

* ``life_safety``: a term that names fire-pump or life-safety equipment
  ("fire pump", "sprinkler", "smoke extract", "emergency lighting", ...).
* ``possibly``: a word that may be about life safety but is not clearly so
  ("emergency", "standby", "alarm", "fire" alone, "critical", "safety",
  "pressurisation", ...). Never read as harmless: an item so worded is not a
  supported load, and the mention stays open until a person clarifies it.

Text is read through :func:`skeleton` (UTS #39 style, for detection only):
NFKC; every dash and hyphen variant as ``-``; format characters (zero-width
spaces and joiners, bidi marks) dropped; combining marks (accents, Arabic
harakat, Hebrew niqqud) and the Arabic tatweel dropped; Cyrillic and Greek
letters that look like Latin ones folded to them; Arabic alef, yeh and teh
marbuta variants folded. The stored text and its offsets never change: each
mention carries offsets into the stored text.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Level = Literal["life_safety", "possibly"]

#: Every dash or hyphen a description may hold, read as "-". U+00AD (soft
#: hyphen) is a format character: it is read as "-" before those are dropped.
DASHES = frozenset(
    "-\u00ad\u058a\u05be\u1400\u1806\u2010\u2011\u2012\u2013\u2014\u2015"
    "\u2043\u2212\u2e17\u2e1a\u2e3a\u2e3b\u2e40\u301c\u30a0\ufe31\ufe32\ufe58\ufe63\uff0d"
)

#: Cyrillic, Greek and other letters that look like a Latin letter (after
#: NFKC; lower and upper case), as the Latin letter. From the UTS #39
#: confusables data, restricted to letters a description could plausibly use.
CONFUSABLES: dict[str, str] = {
    # Cyrillic lower case
    "\u0430": "a", "\u0431": "b", "\u0432": "b", "\u0435": "e", "\u0450": "e", "\u0451": "e",
    "\u0433": "r", "\u0491": "r", "\u0456": "i", "\u0457": "i", "\u0458": "j", "\u043a": "k",
    "\u043c": "m", "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t",
    "\u0443": "y", "\u0445": "x", "\u0455": "s", "\u04bb": "h", "\u0501": "d", "\u051b": "q",
    "\u051d": "w", "\u0438": "u", "\u043f": "n", "\u044c": "b", "\u04cf": "l", "\u0475": "v",
    # Cyrillic upper case
    "\u0410": "a", "\u0412": "b", "\u0415": "e", "\u0400": "e", "\u0401": "e", "\u041a": "k",
    "\u041c": "m", "\u041d": "h", "\u041e": "o", "\u0420": "p", "\u0421": "c", "\u0422": "t",
    "\u0423": "y", "\u0425": "x", "\u0405": "s", "\u0406": "i", "\u0407": "i", "\u0408": "j",
    "\u04c0": "i", "\u04ae": "y", "\u0417": "3", "\u0474": "v", "\u050c": "g", "\u051a": "q",
    "\u051c": "w",
    # Greek lower case
    "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v",
    "\u03bf": "o", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x", "\u03b3": "y",
    "\u03c2": "c", "\u03f2": "c", "\u03f3": "j", "\u03b7": "n",
    # Greek upper case
    "\u0391": "a", "\u0392": "b", "\u0395": "e", "\u0396": "z", "\u0397": "h", "\u0399": "i",
    "\u039a": "k", "\u039c": "m", "\u039d": "n", "\u039f": "o", "\u03a1": "p", "\u03a4": "t",
    "\u03a5": "y", "\u03a7": "x", "\u03f9": "c", "\u037f": "j",
    # Latin look-alikes NFKC keeps
    "\u0131": "i", "\u0269": "i", "\u026a": "i", "\u0261": "g", "\u0251": "a", "\u1d00": "a",
    "\u0280": "r", "\u1d07": "e", "\u0299": "b", "\u029c": "h", "\u1d0b": "k", "\u029f": "l",
    "\u1d0d": "m", "\u0274": "n", "\u1d0f": "o", "\u1d18": "p", "\ua731": "s", "\u1d1b": "t",
    "\u1d1c": "u", "\u1d20": "v", "\u1d21": "w", "\u028f": "y", "\u1d22": "z", "\u0138": "k",
    "\u1d04": "c", "\u1d05": "d", "\u1d0a": "j", "\u1d0c": "l", "\u1d03": "b", "\u1d29": "p",
    "\u0262": "g", "\ua730": "f", "\ua7af": "q", "\u1d7b": "i", "\u1d7e": "u",
    # Arabic letter variants (hamza seats, alef maqsura, teh marbuta)
    "\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0671": "\u0627",
    "\u0649": "\u064a", "\u0629": "\u0647",
}  # fmt: skip

_TATWEEL = "\u0640"


def skeleton(text: str) -> tuple[str, list[int]]:
    """``text`` as detection reads it, lower case, and where each character came from.

    Detection only: never stored, never shown. The map takes a position in
    the result back to ``text``.
    """
    out: list[str] = []
    where: list[int] = []
    for index, char in enumerate(text):
        if char in DASHES:
            pieces = "-"
        elif char == _TATWEEL or unicodedata.category(char) == "Cf":
            continue
        else:
            decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", char))
            kept = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
            pieces = "".join(
                "-" if c in DASHES else CONFUSABLES.get(c, c)
                for c in unicodedata.normalize("NFC", kept)
            )
        for piece in pieces.lower():
            out.append(CONFUSABLES.get(piece, piece))
            where.append(index)
    return "".join(out), where


#: Between the words of a compound: nothing, spaces, hyphens, underscores,
#: dots or slashes ("fire pump", "fire-pump", "firepump", "fire_pump").
_S = r"[\s_\-./]*"

_ENGLISH_DEFINITE = [
    rf"fire{_S}(?:pump|water|alarm|main|hose|fight\w*|m[ae]n'?s?{_S}lift|service|damper|"
    rf"curtain|shutter|suppress\w*|detect\w*|extinguish\w*|protection|reel|riser|brigade|"
    rf"door{_S}(?:hold|release|retain)\w*|stop{_S}valve|telephone|panel)",
    rf"fire{_S}fighters?'?s?{_S}lift",
    r"jockey",
    r"sprinkl",
    r"hydrant",
    r"deluge",
    r"drencher",
    rf"foam{_S}(?:pump|system|inductor)",
    rf"(?:wet|dry){_S}riser",
    rf"smoke{_S}(?:extract|exhaust|control|vent|damper|detect|fan|curtain|management|"
    r"evacuat|clearance|shaft|alarm)",
    rf"emergency{_S}(?:light|luminaire|exit|escape|voice|evacuat|lift|stop{_S}(?:fire|pump))",
    rf"escape{_S}(?:light|route{_S}light|sign)",
    rf"exit{_S}sign",
    rf"life{_S}safety",
    rf"stair(?:case|well|way)?s?{_S}pressur",
    rf"pressuri[sz]ation{_S}(?:fan|system){_S}(?:for|of){_S}stair",
    r"evacuat",
    rf"voice{_S}alarm",
    rf"refuge{_S}alarm",
    rf"gas{_S}suppression",
    rf"fm{_S}200",
    rf"heat{_S}detect",
    rf"manual{_S}call{_S}point",
    rf"break{_S}glass",
]

# Arabic, folded as the skeleton folds it (alef forms as bare alef, teh
# marbuta as heh, alef maqsura as yeh).
_ARABIC_DEFINITE = [
    "\u0645\u0636\u062e\u0647\\s*(?:\u0627\u0644)?\u062d\u0631\u064a\u0642",  # fire pump
    "\u0627\u0646\u0630\u0627\u0631\\s*(?:\u0627\u0644)?\u062d\u0631\u064a\u0642",  # fire alarm
    "\u0645\u0643\u0627\u0641\u062d\u0647\\s*(?:\u0627\u0644)?\u062d\u0631\u064a\u0642",  # firefighting
    "\u0627\u0637\u0641\u0627\u0621",  # extinguishing
    "\u0631\u0634\u0627\u0634",  # sprinkler(s)
    "\u0645\u0631\u0634\u0627\u062a",  # sprinklers
    "\u0647\u064a\u062f\u0631\u0627\u0646\u062a",  # hydrant
    "\u062f\u062e\u0627\u0646",  # smoke
    "\u062c\u0648\u0643\u064a",  # jockey
    "\u0627\u062e\u0644\u0627\u0621",  # evacuation
    "\u0633\u0644\u0627\u0645\u0647\\s*\u0627\u0644\u0627\u0631\u0648\u0627\u062d",  # life safety
    "\u062a\u0636\u063a\u064a\u0637",  # pressurisation (of stairs)
    # emergency lighting / emergency exit
    "(?:\u0627\u0646\u0627\u0631\u0647|\u0627\u0636\u0627\u0621\u0647)\\s*(?:\u0627\u0644)?\u0637\u0648\u0627\u0631\u064a",
    "\u0645\u062e\u0631\u062c\\s*(?:\u0627\u0644)?\u0637\u0648\u0627\u0631\u064a",
]

_HEBREW_DEFINITE = [
    "\u05de\u05e9\u05d0\u05d1\u05ea\\s*(?:\u05d4)?(?:\u05d0\u05e9|\u05db\u05d9\u05d1\u05d5\u05d9)",  # fire pump
    "\u05de\u05ea\u05d6",  # sprinkler
    "\u05e1\u05e4\u05e8\u05d9\u05e0\u05e7\u05dc\u05e8",  # sprinkler
    "\u05d2\u05dc\u05d0\u05d9\\s*(?:\u05d4)?(?:\u05e2\u05e9\u05df|\u05d0\u05e9|\u05d7\u05d5\u05dd)",  # smoke, fire or heat detector
    "\u05d2\u05d9\u05dc\u05d5\u05d9\\s*(?:\u05d4)?\u05d0\u05e9",  # fire detection
    "\u05db\u05d9\u05d1\u05d5\u05d9",  # extinguishing
    "\u05d4\u05d9\u05d3\u05e8\u05e0\u05d8",  # hydrant
    "\u05e2\u05e9\u05df",  # smoke
    "\u05d2'?\u05d5\u05e7\u05d9",  # jockey
    "\u05ea\u05d0\u05d5\u05e8\u05ea\\s*(?:\u05d4)?\u05d7\u05d9\u05e8\u05d5\u05dd",  # emergency lighting
    "\u05d9\u05e6\u05d9\u05d0\u05ea\\s*(?:\u05d4)?\u05d7\u05d9\u05e8\u05d5\u05dd",  # emergency exit
    "\u05d1\u05d8\u05d9\u05d7\u05d5\u05ea\\s*(?:\u05d4)?\u05d7\u05d9\u05d9\u05dd",  # life safety
    "\u05e9\u05d7\u05e8\u05d5\u05e8\\s*\u05e2\u05e9\u05df",  # smoke release
]

#: Words that may be about life safety and are not clearly so.
_ENGLISH_POSSIBLY = [
    r"\bfire\b",
    r"\bfires\b",
    r"\bemergenc(?:y|ies)\b",
    r"\bstand[\s_\-]?by\b",
    r"\balarms?\b",
    r"\bcritical\b",
    r"\bsafety\b",
    r"\bpressuri[sz]",
    r"\bsmoke\b",
    r"\bescape\b",
    r"\bessential\b",
    r"\blife[\s_\-]?support\b",
    r"\bfirefighter",
    r"\bfireman",
]

_ARABIC_POSSIBLY = [
    "\u062d\u0631\u064a\u0642",  # fire
    "\u0637\u0648\u0627\u0631\u064a",  # emergency
    "\u0627\u0646\u0630\u0627\u0631",  # alarm
    "\u0627\u062d\u062a\u064a\u0627\u0637\u064a",  # standby
    "\u0633\u0644\u0627\u0645\u0647",  # safety
]

_HEBREW_POSSIBLY = [
    "(?<!\\w)\u05d4?\u05d0\u05e9(?!\\w)",  # fire (the word alone: it is inside many words)
    "\u05d7\u05d9\u05e8\u05d5\u05dd",  # emergency
    "\u05d0\u05d6\u05e2\u05e7\u05d4",  # alarm
    "\u05d2\u05d9\u05d1\u05d5\u05d9",  # standby / backup
    "\u05d1\u05d8\u05d9\u05d7\u05d5\u05ea",  # safety
    "\u05e4\u05d9\u05e0\u05d5\u05d9",  # evacuation (also "clearing")
]

_DEFINITE = re.compile("|".join(_ENGLISH_DEFINITE + _ARABIC_DEFINITE + _HEBREW_DEFINITE))
_POSSIBLY = re.compile("|".join(_ENGLISH_POSSIBLY + _ARABIC_POSSIBLY + _HEBREW_POSSIBLY))


@dataclass(frozen=True)
class Mention:
    """One matched span of the stored text.

    Attributes:
        mention_id: ``m`` and 16 hex digits: a digest of the level, the
            normalised term and its start offset, stable for the same text.
        level: ``life_safety`` or ``possibly``.
        term: The matched words as detection read them (skeleton, spaces
            and separators collapsed to one space), at most 60 characters.
        start: Offset of its first character in the stored text.
        end: Offset after its last character.
    """

    mention_id: str
    level: Level
    term: str
    start: int
    end: int


def _term(matched: str) -> str:
    return re.sub(r"[\s_\-./]+", " ", matched).strip()[:60]


def mention_id(level: str, term: str, start: int) -> str:
    """The stable id of a mention: level, normalised term and offset."""
    digest = hashlib.sha256(f"{level}\x00{term}\x00{start}".encode()).hexdigest()
    return "m" + digest[:16]


def mentions(text: str) -> list[Mention]:
    """Every life-safety and possibly-safety term in ``text``, in order.

    A possibly-safety word inside a life-safety term ("fire" in "fire pump")
    is that term, not a second mention.
    """
    read, where = skeleton(text)
    found: list[Mention] = []
    taken: list[tuple[int, int]] = []
    levels: tuple[tuple[Level, re.Pattern[str]], ...] = (
        ("life_safety", _DEFINITE),
        ("possibly", _POSSIBLY),
    )
    for level, pattern in levels:
        for match in pattern.finditer(read):
            a, b = match.start(), match.end()
            if a == b or any(a < y and x < b for x, y in taken):
                continue
            taken.append((a, b))
            term = _term(match.group())
            start, end = where[a], where[b - 1] + 1
            found.append(Mention(mention_id(level, term, start), level, term, start, end))
    return sorted(found, key=lambda m: (m.start, m.end))


def level_of(text: str) -> Level | None:
    """``life_safety`` if ``text`` names such equipment, else ``possibly`` if it may, else ``None``."""
    read, _ = skeleton(text)
    if _DEFINITE.search(read):
        return "life_safety"
    if _POSSIBLY.search(read):
        return "possibly"
    return None
