"""Which manufacturer a question is about, read from the model names in it.

Retrieval can restrict to one brand, and a question that names its drive is
better answered from that drive's manuals. Without the restriction, "ACS880
shows 3220 DC link undervoltage" ranked three SINAMICS passages about F30003
above ABB's own row for 3220: the words match a Siemens fault better than the
ABB table matches the words, and the engineer was told the library had no
answer when it did. The brand filter only ever came from the equipment the
engineer set by hand, which a first question rarely has.

The vocabulary is read from the corpus, not written here: the model-like
tokens (letters and digits together -- ``ACS880``, ``G120``, ``CU240B``) in
the titles of the documents actually published under each brand, plus the
brand names themselves. A token two brands share says nothing and is dropped.
A list kept by hand would name drives the library does not hold and miss the
ones it does.

The restriction is applied only when the question points at exactly one
brand. Two brands, or none, and retrieval runs unfiltered as before: a wrong
filter hides the answer, which is worse than a noisy ranking.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping

import structlog

from app.ai.retrieval.hybrid_search import published_titles_by_brand

logger = structlog.get_logger(__name__)

#: A model designation: letters and digits in one token (ACS880, G120, CU240B-2).
#: Pure words ("drive") and pure numbers (fault codes, "2310") never qualify --
#: a fault code is shared across brands and says nothing about whose it is.
_MODEL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{3,}"
)
#: A product family written without digits, as titles print one: in capitals
#: (SINAMICS, SIMATIC). Case is what separates it from the title's ordinary
#: words -- "Voltage", "Firmware", "System" -- which, with only a few brands in
#: the corpus, would each belong to one brand and send a question about "low
#: voltage" to whichever manual happened to say it in its title.
_FAMILY = re.compile(r"(?<![A-Za-z])[A-Z][A-Z&]{3,}(?![A-Za-z])")
#: A word in the question, compared with families and brand names case-blind.
_WORD = re.compile(r"[A-Za-z][A-Za-z&]{2,}")

#: How long a vocabulary read from the index is trusted. New manuals reach
#: production in batches, minutes apart at the fastest.
VOCABULARY_TTL_S = 600.0

Vocabulary = Mapping[str, str]
"""Upper-cased token -> the one brand it belongs to."""


def tokens(text: str) -> set[str]:
    """The model-like tokens in ``text``, upper-cased."""
    return {match.group(0).strip("-").upper() for match in _MODEL_TOKEN.finditer(text)}


def build_vocabulary(titles_by_brand: Mapping[str, list[str]]) -> dict[str, str]:
    """Map each token to its brand, keeping only tokens one brand owns.

    Args:
        titles_by_brand: Every published document title, grouped by brand.

    Returns:
        Upper-cased token -> brand. The brand's own name is a token of it, and
        so is a family its titles print in capitals (SINAMICS).
    """
    owners: dict[str, set[str]] = {}
    for brand, titles in titles_by_brand.items():
        found = {brand.upper()}
        for title in titles:
            found |= tokens(title)
            found |= set(_FAMILY.findall(title))
        for token in found:
            owners.setdefault(token, set()).add(brand)
    return {token: next(iter(brands)) for token, brands in owners.items() if len(brands) == 1}


def infer_manufacturer(question: str, *, vocabulary: Vocabulary | None = None) -> str | None:
    """The one manufacturer ``question`` names a product of, if exactly one.

    Args:
        question: The engineer's question, in any language -- model names are
            written in Latin script either way.
        vocabulary: Injected in tests; read from the production index otherwise.

    Returns:
        The brand, or ``None`` when the question names none or several.
    """
    known = vocabulary if vocabulary is not None else _cached_vocabulary()
    if not known:
        return None
    mentioned = tokens(question) | {word.upper() for word in _WORD.findall(question)}
    brands = {known[token] for token in mentioned if token in known}
    return next(iter(brands)) if len(brands) == 1 else None


# --- the vocabulary, read from the published corpus ------------------------------

_lock = threading.Lock()
_cache: tuple[float, dict[str, str]] | None = None


def _cached_vocabulary(
    *,
    load: Callable[[], dict[str, list[str]]] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """The vocabulary, rebuilt at most every ``VOCABULARY_TTL_S`` seconds."""
    global _cache
    with _lock:
        if _cache is not None and now() - _cache[0] < VOCABULARY_TTL_S:
            return _cache[1]
    try:
        vocabulary = build_vocabulary((load or published_titles_by_brand)())
    except Exception:
        # Never a reason to fail the question: without a hint, retrieval runs
        # unfiltered exactly as it did before this module existed.
        logger.warning("equipment_hint.vocabulary_unavailable", exc_info=True)
        return {}
    with _lock:
        _cache = (now(), vocabulary)
    return vocabulary
