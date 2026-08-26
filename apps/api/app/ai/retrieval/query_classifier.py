"""Deciding which retrieval blend a query needs.

A fault-code lookup and a symptom description are different retrieval
problems. "F0001" wants the lexical leg — every fault code in a manual is
semantically adjacent to every other, so vector similarity is close to noise
for it. "trips when the conveyor starts under load" wants the semantic leg,
because the engineer's words are not the manual's words.

**Rules, not a model.** Classification runs on every query, must be
explainable when retrieval goes wrong, and has to be stable enough that
tuning against it means something — a learned classifier that drifts would
silently re-point queries at weights tuned for a different distribution.

**Ambiguity resolves toward the semantic leg.** A query that looks like both
("F0001 keeps coming back when the line starts") is treated as a symptom
description, because the lexical leg still contributes under semantic weights
whereas the reverse is much weaker: under fault-code weights the vector leg is
0.15 and a paraphrase is effectively unfindable.
"""

from __future__ import annotations

import re

from app.models.schemas.retrieval_config import QueryType

# Fault codes as manufacturers actually write them: F0001, E-024, AL 5091,
# "fault 2340". A bare number is deliberately not enough — "24" alone is far
# more likely a measurement than a code.
_FAULT_CODE = re.compile(
    r"""
    (?:
      # F0001, E-024 — the letters must be attached or hyphenated. A space
      # would match "is 24" in "is 24 volts correct", routing a measurement
      # question to weights that all but disable the semantic leg.
        \b[a-z]{1,3}-?\d{2,5}\b
      # "AL 5091" — a space is only allowed after a known code prefix, so the
      # separator is not a general licence to join any word to any number.
      | \b(?:al|f|e|err|flt)\s\d{2,5}\b
      | \b(?:fault|alarm|error|trip|code)\s+(?:code\s+)?[a-z]?[\s-]?\d{1,5}\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Parameter references: "par 21.03", "parameter 99.04", "P-1204", "20.01".
_PARAMETER = re.compile(
    r"""
    (?:
        \b(?:par|param|parameter|pr)\.?\s*[\s-]?\d{1,3}(?:\.\d{1,3})?\b
      | \bp-?\d{3,4}\b
      | \b\d{1,3}\.\d{2}\b              # the bare group.index form
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Words that describe a fault happening rather than name it. Their presence
# means the engineer is describing, so the semantic leg matters even when a
# code is also present.
_SYMPTOM_MARKERS = (
    "when",
    "after",
    "during",
    "keeps",
    "sometimes",
    "intermittent",
    "randomly",
    "under load",
    "won't",
    "will not",
    "does not",
    "doesn't",
    "why",
    "how do",
    "what causes",
)


def classify_query(query: str) -> QueryType:
    """Classify a query for blend-weight selection.

    Args:
        query: The engineer's question.

    Returns:
        The query type. Ambiguous queries resolve to
        ``SYMPTOM_DESCRIPTION`` — see the module docstring for why that is the
        safer direction.
    """
    text = query.strip()
    if not text:
        # An empty query retrieves nothing regardless; classify it as the type
        # whose weights are most forgiving rather than raising, because the
        # caller's own validation owns rejecting it.
        return QueryType.SYMPTOM_DESCRIPTION

    lowered = text.casefold()
    describing = any(marker in lowered for marker in _SYMPTOM_MARKERS)

    # A description that also cites a code is still a description: the
    # engineer is asking what the code means *in their situation*, and only
    # the semantic leg reaches the explanation.
    if describing:
        return QueryType.SYMPTOM_DESCRIPTION

    if _PARAMETER.search(text):
        return QueryType.PARAMETER_LOOKUP
    if _FAULT_CODE.search(text):
        return QueryType.FAULT_CODE

    return QueryType.SYMPTOM_DESCRIPTION
