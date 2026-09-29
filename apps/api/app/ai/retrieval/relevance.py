"""Absolute relevance: does a passage match the question, not just outrank others.

The fused hybrid score cannot answer that. Each leg is min-max normalised
*within one result set* before blending (see ``hybrid_search``), so the best
hit of any query scores near 1.0 -- including a query the corpus has nothing
on, whose best hit is merely the least bad. The cite-or-refuse threshold is
judged against that score, so on its own it measures rank, not relevance: an
off-topic question that matches anything at all clears it.

Two signals here are absolute, and a passage counts as evidence if either
holds:

* **Similarity** -- the cosine between the query's embedding and the
  passage's. Computed from the vectors the search already returns, so it
  costs no extra query, and it means the same thing whatever else was
  retrieved.
* **An anchor** -- the passage contains a fault code or parameter number the
  query names. Similarity is weakest exactly there: every fault code in a
  manual embeds close to every other, so a lookup for "F0001" cannot be
  judged by it, and a floor that ignored this would refuse the most exact
  questions the product gets.

What the similarity floor should be depends on the embedding model and the
corpus, so it is measured, not guessed: ``calibrate`` turns an eval set's
in-scope and out-of-scope questions into a recommended value.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from app.ai.retrieval.query_classifier import code_references
from app.models.schemas.evaluation import EvalCategory, EvalEntry
from app.models.schemas.search import RetrievedPassage

# A number in a code reference. Three digits at least: "E-24" names 24, which
# appears in any manual as a voltage, a pin count or a page number, and would
# anchor nearly every passage to nearly every question.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_MIN_ANCHOR_DIGITS = 3


def cosine(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Return the cosine similarity of two vectors.

    Args:
        a: One vector.
        b: The other.

    Returns:
        A value in [-1, 1], or ``None`` when the vectors cannot be compared --
        different widths, or either has zero length. ``None`` rather than 0.0,
        because "could not measure" must not read as "measured, unrelated",
        and neither must it read as related.
    """
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0.0 or not math.isfinite(norm):
        return None
    return max(-1.0, min(1.0, dot / norm))


def anchors_of(query: str) -> frozenset[str]:
    """Return the numbers a query's code references name.

    Args:
        query: The engineer's question.

    Returns:
        Each number of at least three digits, as written ("0001", "21.03").
        The prefix is dropped: manuals write the same code as F0001, F 0001
        and F-0001, and the number is the part that identifies it.
    """
    numbers = (
        number for reference in code_references(query) for number in _NUMBER.findall(reference)
    )
    return frozenset(n for n in numbers if sum(c.isdigit() for c in n) >= _MIN_ANCHOR_DIGITS)


def is_anchored(text: str, anchors: frozenset[str]) -> bool:
    """Report whether a passage contains one of the query's code numbers.

    Args:
        text: The passage.
        anchors: From ``anchors_of``.

    Returns:
        ``True`` if any anchor appears as a whole number: not inside a longer
        one, so a question about 0001 is not anchored by 10001 or 0001.5.
    """
    return any(
        re.search(rf"(?<![\d.]){re.escape(anchor)}(?![\d]|\.\d)", text) for anchor in anchors
    )


class Calibration(BaseModel):
    """A recommended similarity floor, and what it would do.

    Attributes:
        skipped: Questions not measured: a code anchored one of their
            passages (the floor does not apply), nothing was retrieved (the
            floor cannot change that), or no similarity could be computed.
        floor: The recommendation, or ``None`` when there is too little data
            to recommend anything.
        in_scope: Questions measured that the corpus should answer. Those
            anchored by a code are left out: the floor does not apply to them.
        out_of_scope: Questions measured that it should refuse.
        kept: Fraction of in-scope questions whose best passage clears the
            floor -- the answers it keeps.
        refused: Fraction of out-of-scope questions whose best passage falls
            below it -- the refusals it adds.
        reason: Why there is no recommendation, when there is none.
    """

    floor: float | None
    skipped: int = Field(default=0, ge=0)
    in_scope: int = Field(ge=0)
    out_of_scope: int = Field(ge=0)
    kept: float | None = None
    refused: float | None = None
    reason: str | None = None


#: Fewer measured questions than this and a percentile is an anecdote.
MIN_IN_SCOPE = 20
MIN_OUT_OF_SCOPE = 5


def calibrate(
    in_scope: Sequence[float], out_of_scope: Sequence[float], *, keep: float = 0.95
) -> Calibration:
    """Recommend a similarity floor from measured questions.

    Args:
        in_scope: For each question the corpus should answer, the highest
            similarity among its retrieved passages.
        out_of_scope: The same, for each question it should refuse.
        keep: The fraction of in-scope questions the floor must still let
            through. Refusing a question the corpus answers is safe but
            costly; this bounds that cost, and the out-of-scope refusals are
            whatever that bound leaves room for.

    Returns:
        The highest floor that keeps ``keep`` of in-scope questions, with the
        effect it would have on both sets. No recommendation when either set
        is too small to mean anything, or when the floor would refuse no
        out-of-scope question at all: then similarity does not separate the
        two here, and enforcing it would cost answers for nothing.
    """
    measured = Calibration(floor=None, in_scope=len(in_scope), out_of_scope=len(out_of_scope))
    if len(in_scope) < MIN_IN_SCOPE or len(out_of_scope) < MIN_OUT_OF_SCOPE:
        return measured.model_copy(
            update={
                "reason": (
                    f"needs at least {MIN_IN_SCOPE} in-scope and {MIN_OUT_OF_SCOPE} "
                    f"out-of-scope questions without a code anchor; have "
                    f"{len(in_scope)} and {len(out_of_scope)}"
                )
            }
        )

    ranked = sorted(in_scope)
    # The floor may refuse at most this many in-scope questions; it sits at
    # the lowest similarity among the ones it must keep.
    allowed_losses = math.floor(len(ranked) * (1.0 - keep) + 1e-9)
    floor = ranked[allowed_losses]
    kept = sum(s >= floor for s in in_scope) / len(in_scope)
    refused = sum(s < floor for s in out_of_scope) / len(out_of_scope)
    if refused == 0.0:
        return measured.model_copy(
            update={
                "kept": kept,
                "refused": 0.0,
                "reason": (
                    f"a floor keeping {keep:.0%} of in-scope questions ({floor:.3f}) "
                    "refuses none of the out-of-scope ones; similarity does not "
                    "separate them in this corpus"
                ),
            }
        )
    # Rounded down, never to nearest: rounding up could lift the floor over
    # the very question it was placed to keep.
    rounded = math.floor(floor * 1000) / 1000
    return measured.model_copy(update={"floor": rounded, "kept": kept, "refused": refused})


def calibrate_from_eval_set(
    entries: Sequence[EvalEntry],
    retrieve: Callable[[EvalEntry], list[RetrievedPassage]],
    *,
    keep: float = 0.95,
) -> Calibration:
    """Run an eval set's questions through retrieval and calibrate a floor.

    Args:
        entries: The eval set. ``OUT_OF_SCOPE`` entries are the questions to
            refuse; every other category is a question to answer.
        retrieve: Runs one entry's query with no similarity floor, so every
            passage's similarity is seen.
        keep: See ``calibrate``.

    Returns:
        The calibration, counting what was not measured and why.
    """
    in_scope: list[float] = []
    out_of_scope: list[float] = []
    skipped = 0
    for entry in entries:
        passages = retrieve(entry)
        measured = [p.similarity for p in passages if p.similarity is not None]
        if not measured or any(p.anchored for p in passages):
            skipped += 1
            continue
        target = out_of_scope if entry.category is EvalCategory.OUT_OF_SCOPE else in_scope
        target.append(max(measured))
    return calibrate(in_scope, out_of_scope, keep=keep).model_copy(update={"skipped": skipped})
