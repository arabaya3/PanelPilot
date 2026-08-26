"""Measuring retrieval quality, and tuning against it without cheating.

**Precision and recall are reported separately, per query type.** They fail in
opposite directions and an aggregate hides both. Under-retrieving makes
cite-or-refuse fire when it should not — frustrating but safe. Over-retrieving
puts irrelevant passages in front of generation, and an answer citing a
passage that does not support it is the failure the whole product exists to
prevent. A single number that averages fault-code lookups against symptom
descriptions can look healthy while one category is broken.

**The held-out split is enforced structurally, not by convention.** Tuning
against the whole eval set means the reported score measures how well the
parameters memorised the set, not how well retrieval works. So ``split_eval_set``
returns a :class:`TuningSplit` whose holdout is not reachable from the tuning
loop, and ``tune`` accepts only the tuning half. The final number comes from
``evaluate`` against the holdout, once.

Splitting is deterministic — by a hash of the entry id, not by shuffling —
so a re-run puts the same entries in the same half. A split that moves between
runs makes two tuning results incomparable, and makes "it improved" unfalsifiable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence

from pydantic import BaseModel, Field, model_validator

from app.ai.retrieval.query_classifier import classify_query
from app.models.schemas.evaluation import EvalEntry
from app.models.schemas.retrieval_config import BlendWeights, QueryType, RetrievalConfig

# What retrieval returns for one query, reduced to what scoring needs: the
# document ids of the retrieved passages, in rank order.
Retriever = Callable[[EvalEntry, RetrievalConfig], Sequence[str]]


class CategoryMetrics(BaseModel):
    """Precision and recall for one query type.

    Attributes:
        query_type: The category measured.
        queries: How many eval entries fell into it.
        hits: How many retrieved the expected document at all.
        precision: Of the passages retrieved across these queries, the
            fraction that were the expected document. Low precision means
            generation is being handed irrelevant passages to cite.
        recall: Of these queries, the fraction where the expected document was
            retrieved at all. Low recall means the answer was unreachable and
            cite-or-refuse will correctly but uselessly refuse.
    """

    query_type: QueryType
    queries: int = Field(ge=0)
    hits: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _hits_cannot_exceed_queries(self) -> CategoryMetrics:
        """Keep the counts self-consistent.

        Returns:
            The validated metrics.

        Raises:
            ValueError: If more queries hit than were run, which would mean
                the scorer double-counted and every number here is wrong.
        """
        if self.hits > self.queries:
            raise ValueError(
                f"{self.query_type.value}: {self.hits} hits from {self.queries} queries"
            )
        return self


class TuningReport(BaseModel):
    """Retrieval quality for one config, broken down by query type.

    Attributes:
        config: The configuration measured.
        by_category: Metrics per query type. Categories with no eval entries
            are present with zero counts rather than omitted — an absent
            category reads as "fine" when it means "never tested".
        holdout: Whether this was measured against the held-out split. Only a
            holdout report is a claim about retrieval quality; a tuning-split
            report is a claim about the tuning loop's progress.
    """

    config: RetrievalConfig
    by_category: list[CategoryMetrics]
    holdout: bool = False

    @property
    def weakest_category(self) -> CategoryMetrics | None:
        """The category with the lowest precision, ignoring untested ones.

        Returns:
            The weakest tested category, or ``None`` if nothing was tested.
            This is the number to read first: an aggregate that looks fine
            while one category is broken is the specific failure this report
            exists to prevent.
        """
        tested = [m for m in self.by_category if m.queries]
        return min(tested, key=lambda m: m.precision, default=None)

    @property
    def untested_categories(self) -> list[QueryType]:
        """Query types with no eval entries.

        Returns:
            The types nothing measured. A type here is one where retrieval
            could be arbitrarily bad and no number would show it.
        """
        return [m.query_type for m in self.by_category if not m.queries]


class TuningSplit(BaseModel):
    """An eval set divided into a tuning half and an untouchable holdout.

    The holdout exists to answer "does this generalise", and it can only do
    that if it is never used to choose parameters. That is why this is a type
    rather than a convention: ``tune`` takes ``split.tuning`` and the holdout
    is not in scope there.

    Attributes:
        tuning: Entries the tuning loop may use.
        holdout: Entries reserved for final validation.
    """

    tuning: list[EvalEntry] = Field(min_length=1)
    holdout: list[EvalEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _the_halves_are_disjoint(self) -> TuningSplit:
        """Refuse a split that leaks.

        Returns:
            The validated split.

        Raises:
            ValueError: If an entry appears in both halves. One leaked entry
                turns the holdout score into a partly-memorised score, and
                nothing downstream could detect it.
        """
        overlap = sorted({e.id for e in self.tuning} & {e.id for e in self.holdout})
        if overlap:
            raise ValueError(f"entries in both halves of the split: {overlap}")
        return self


def _holdout_bucket(entry_id: str) -> int:
    """Assign an entry to a stable bucket in [0, 100).

    Args:
        entry_id: The entry's stable id.

    Returns:
        Its bucket. Derived from a hash of the id rather than from position or
        randomness, so the split is identical across runs and machines — two
        tuning results measured against different holdouts are not comparable,
        and "it improved" becomes unfalsifiable.
    """
    digest = hashlib.sha256(entry_id.encode("utf-8")).digest()
    return digest[0] % 100


def split_eval_set(entries: Sequence[EvalEntry], *, holdout_percent: int = 25) -> TuningSplit:
    """Divide an eval set into tuning and holdout halves.

    Args:
        entries: The full eval set.
        holdout_percent: Percentage reserved for final validation.

    Returns:
        The split.

    Raises:
        ValueError: If ``holdout_percent`` is outside 1-99, or if the split
            would leave either half empty. An empty holdout means the final
            number measures nothing; an empty tuning half means there is
            nothing to tune against.
    """
    if not 1 <= holdout_percent <= 99:
        raise ValueError(f"holdout_percent must be between 1 and 99, got {holdout_percent}")

    tuning = [e for e in entries if _holdout_bucket(e.id) >= holdout_percent]
    holdout = [e for e in entries if _holdout_bucket(e.id) < holdout_percent]

    if not tuning or not holdout:
        raise ValueError(
            f"a {holdout_percent}% holdout over {len(entries)} entries left "
            f"{len(tuning)} tuning and {len(holdout)} holdout entries; "
            "the set is too small to split meaningfully"
        )
    return TuningSplit(tuning=tuning, holdout=holdout)


def measure(
    entries: Sequence[EvalEntry],
    config: RetrievalConfig,
    retriever: Retriever,
    *,
    holdout: bool = False,
) -> TuningReport:
    """Measure retrieval precision and recall per query type.

    Args:
        entries: Eval entries to measure against.
        config: The configuration to measure.
        retriever: Callable returning the retrieved document ids for an entry.
            Injected so this is testable without a live index.
        holdout: Whether these entries are the held-out split. Recorded on the
            report so a tuning-loop number cannot later be read as a
            generalisation claim.

    Returns:
        The report, with every query type present even if untested.
    """
    buckets: dict[QueryType, list[EvalEntry]] = {t: [] for t in QueryType}
    for entry in entries:
        buckets[classify_query(entry.query)].append(entry)

    metrics = []
    for query_type, bucket in buckets.items():
        retrieved_total = 0
        relevant_total = 0
        hits = 0
        for entry in bucket:
            expected = entry.expected_citation
            if expected is None:
                # An out-of-scope entry asserts nothing should be found, so it
                # has no expected document and cannot contribute to precision.
                continue
            retrieved = list(retriever(entry, config))
            retrieved_total += len(retrieved)
            relevant = sum(1 for doc_id in retrieved if doc_id == expected.document_id)
            relevant_total += relevant
            if relevant:
                hits += 1

        answerable = sum(1 for e in bucket if e.expected_citation is not None)
        metrics.append(
            CategoryMetrics(
                query_type=query_type,
                queries=answerable,
                hits=hits,
                precision=(relevant_total / retrieved_total) if retrieved_total else 0.0,
                recall=(hits / answerable) if answerable else 0.0,
            )
        )

    return TuningReport(config=config, by_category=metrics, holdout=holdout)


def tune(
    tuning_entries: Sequence[EvalEntry],
    retriever: Retriever,
    *,
    base: RetrievalConfig | None = None,
    candidate_weights: Iterable[float] = (0.15, 0.3, 0.5, 0.7, 0.85),
) -> RetrievalConfig:
    """Search for the blend weight that maximises precision, per query type.

    Each type is tuned independently against only its own entries: a weight
    that helps fault-code lookups can hurt symptom descriptions, and tuning
    them together picks whichever category has more entries.

    Args:
        tuning_entries: The tuning half of a :class:`TuningSplit`. Never pass
            the holdout — the whole point of the split is that these
            parameters are chosen without seeing it.
        retriever: Callable returning retrieved document ids.
        base: Starting configuration; defaults to a fresh one.
        candidate_weights: BM25 weights to try. The vector weight is the
            complement, so the pair always sums to 1.

    Returns:
        A configuration with the best-scoring weight per query type. A type
        with no tuning entries keeps its starting weights — tuning it against
        nothing would produce a number with no evidence behind it.
    """
    config = base or RetrievalConfig()
    chosen = dict(config.weights)

    for query_type in QueryType:
        bucket = [
            e
            for e in tuning_entries
            if classify_query(e.query) is query_type and e.expected_citation is not None
        ]
        if not bucket:
            continue

        best_score = -1.0
        for bm25 in candidate_weights:
            trial_weights = BlendWeights(bm25=bm25, vector=round(1.0 - bm25, 10))
            trial = config.model_copy(update={"weights": {**chosen, query_type: trial_weights}})
            report = measure(bucket, trial, retriever)
            measured = next(m for m in report.by_category if m.query_type is query_type)
            # Precision decides, with recall breaking ties: an irrelevant
            # passage reaching generation is worse than a missed one, because
            # the miss refuses safely and the irrelevant one gets cited.
            score = (measured.precision, measured.recall)
            if score > (best_score, -1.0):
                best_score = measured.precision
                chosen[query_type] = trial_weights

    return config.model_copy(update={"weights": chosen})


def format_tuning_report(report: TuningReport) -> str:
    """Render a report for a terminal or CI log.

    Args:
        report: The report to render.

    Returns:
        Per-category lines, weakest first, with untested categories named
        explicitly. There is deliberately no aggregate score: a single number
        across query types is exactly what hides one category being broken.
    """
    scope = "HOLDOUT" if report.holdout else "tuning split"
    lines = [f"Retrieval quality ({scope})"]

    tested = sorted((m for m in report.by_category if m.queries), key=lambda m: m.precision)
    for metric in tested:
        weights = report.config.weights_for(metric.query_type)
        lines.append(
            f"  {metric.query_type.value:20} "
            f"precision={metric.precision:.2f} recall={metric.recall:.2f} "
            f"({metric.hits}/{metric.queries} found)  "
            f"bm25={weights.bm25:.2f}/vector={weights.vector:.2f}"
        )

    untested = report.untested_categories
    if untested:
        lines.append("")
        lines.append(
            "  no eval entries for: "
            + ", ".join(t.value for t in untested)
            + " — retrieval could be arbitrarily bad here and nothing would show it"
        )

    if not report.holdout:
        lines.append("")
        lines.append("  measured on the tuning split; not a generalisation claim")

    return "\n".join(lines)
