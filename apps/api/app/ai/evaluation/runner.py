"""Scoring the eval set against the current pipeline.

**The scorer is the thing most worth distrusting.** Every other check in this
repo fails loudly when it breaks. A scorer that is too generous fails
*quietly*: the run goes green, the pass rate looks healthy, and the regression
it was built to catch ships anyway. So the rules here are deliberately strict
and deliberately dumb — no fuzzy matching, no partial credit, no "close
enough". An entry passes when every assertion holds and fails otherwise.

**Citation correctness is checked separately from answer correctness**, and a
right answer resting on the wrong source fails. An engineer who follows a
correct-sounding procedure to the wrong page of the wrong manual is in exactly
the situation the product exists to prevent, and no check that reads only the
prose would notice.

**A refusal is a real outcome, not an error.** For an ``OUT_OF_SCOPE`` entry
the refusal *is* the pass condition, and answering it is the worst failure the
set can report — that is the guardrail failing open.

The runner takes the pipeline as a callable rather than importing it, so the
scoring logic can be tested against known-pass and known-fail fixtures without
a live corpus. That is the spec's requirement: the scorer is proven correct
before it is trusted to gate anything.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Sequence

from app.models.schemas.evaluation import (
    EvalEntry,
    EvalResult,
    EvalRun,
    ExpectedCitation,
    FailureMode,
)

# What the runner needs back from the pipeline for one query: the answer text
# (None when it refused) and the citations it rested on.
PipelineAnswer = tuple[str | None, list[ExpectedCitation]]
Pipeline = Callable[[EvalEntry], PipelineAnswer]


def _normalise(text: str) -> str:
    """Fold text for phrase comparison.

    Case and surrounding whitespace are not meaningful differences in an
    answer, and NFKC folding keeps a phrase from missing because the model
    emitted a non-breaking space or a full-width character. Nothing beyond
    that: stemming or synonym matching would let "do not de-energise" satisfy
    a phrase requiring "de-energise", which is the generosity this scorer
    exists to avoid.

    Args:
        text: Raw text.

    Returns:
        The folded form used for comparison.
    """
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _missing_phrases(answer: str, required: Iterable[str]) -> list[str]:
    """Find required phrases absent from an answer.

    Args:
        answer: The pipeline's answer text.
        required: Phrases that must all appear.

    Returns:
        The phrases that did not appear, in the order they were required.
    """
    folded = _normalise(answer)
    return [phrase for phrase in required if _normalise(phrase) not in folded]


def _citation_satisfied(expected: ExpectedCitation, actual: Sequence[ExpectedCitation]) -> bool:
    """Report whether the expected source is among those actually cited.

    Args:
        expected: The citation the entry requires.
        actual: Citations the pipeline produced.

    Returns:
        ``True`` if one actual citation matches. When the entry specifies a
        page, the page must match too — the right manual opened to the wrong
        procedure is still the wrong answer. When the entry omits the page,
        any page of the right document satisfies it, because the entry author
        chose not to assert one.
    """
    for citation in actual:
        if citation.document_id != expected.document_id:
            continue
        if expected.page is None or citation.page == expected.page:
            return True
    return False


def score_entry(
    entry: EvalEntry, answer: str | None, citations: Sequence[ExpectedCitation]
) -> EvalResult:
    """Score one entry against what the pipeline returned.

    Args:
        entry: The eval entry.
        answer: The pipeline's answer text, or ``None`` if it refused.
        citations: The citations the pipeline rested on.

    Returns:
        The result, carrying the specific failure mode when it did not pass.
    """
    actual_ids = [c.document_id for c in citations]

    if entry.expected_citation is None:
        # OUT_OF_SCOPE: refusing is the pass condition.
        if answer is None:
            return EvalResult(entry_id=entry.id, passed=True, actual_citations=actual_ids)
        return EvalResult(
            entry_id=entry.id,
            passed=False,
            failure=FailureMode.ANSWERED_OUT_OF_SCOPE,
            detail=(
                "the corpus does not answer this, but the pipeline answered anyway — "
                "the guardrail failed open"
            ),
            actual_citations=actual_ids,
        )

    if answer is None:
        return EvalResult(
            entry_id=entry.id,
            passed=False,
            failure=FailureMode.UNEXPECTED_REFUSAL,
            detail=f"refused a question the corpus answers (expected {entry.expected_citation.document_id})",
            actual_citations=actual_ids,
        )

    # Citation first. A wrong-sourced answer is reported as such even when the
    # prose happens to contain every required phrase, because that combination
    # — right words, wrong source — is the most misleading outcome possible
    # and would otherwise be filed as a mere wording problem.
    if not _citation_satisfied(entry.expected_citation, citations):
        expected = entry.expected_citation
        want = expected.document_id
        if expected.page is not None:
            want = f"{want} p{expected.page}"
        return EvalResult(
            entry_id=entry.id,
            passed=False,
            failure=FailureMode.WRONG_CITATION,
            detail=f"expected {want}, got {actual_ids or 'no citations'}",
            actual_citations=actual_ids,
        )

    missing = _missing_phrases(answer, entry.required_phrases)
    if missing:
        return EvalResult(
            entry_id=entry.id,
            passed=False,
            failure=FailureMode.WRONG_ANSWER,
            detail=f"answer omitted {len(missing)} required phrase(s)",
            missing_phrases=missing,
            actual_citations=actual_ids,
        )

    return EvalResult(entry_id=entry.id, passed=True, actual_citations=actual_ids)


def run_eval_set(
    entries: Sequence[EvalEntry],
    pipeline: Pipeline,
    *,
    corpus_brands: Iterable[str] = (),
) -> EvalRun:
    """Run every entry and summarise.

    Args:
        entries: The eval set.
        pipeline: Callable taking an entry and returning ``(answer, citations)``.
            Injected rather than imported so the scoring logic is testable
            without a live corpus — the spec requires the runner itself be
            proven correct before it gates anything.
        corpus_brands: Brands present in the corpus, for coverage reporting.

    Returns:
        The run, including any brands the set does not cover.

    Raises:
        ValueError: If ``entries`` is empty, or contains duplicate ids. An
            empty set reports a 100% pass rate over nothing, which reads as
            success; duplicate ids make results ambiguous to attribute.
    """
    if not entries:
        raise ValueError("an empty eval set would report a perfect pass rate over nothing")

    ids = [e.id for e in entries]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate eval entry ids: {duplicates}")

    results = []
    for entry in entries:
        try:
            answer, citations = pipeline(entry)
        except Exception as exc:
            # One entry raising must not abandon the rest of the run: the whole
            # point is a complete picture of what regressed.
            results.append(
                EvalResult(
                    entry_id=entry.id,
                    passed=False,
                    failure=FailureMode.PIPELINE_ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        results.append(score_entry(entry, answer, citations))

    return EvalRun(results=results, coverage_gaps=find_coverage_gaps(entries, corpus_brands))


def find_coverage_gaps(entries: Sequence[EvalEntry], corpus_brands: Iterable[str]) -> list[str]:
    """Report corpus brands with no eval entry.

    Args:
        entries: The eval set.
        corpus_brands: Brands present in the corpus.

    Returns:
        Sorted brands the set does not cover. As brand coverage expands the
        set must expand with it — a brand with zero entries is one where a
        regression ships without anything going red.
    """
    covered = {e.brand.casefold() for e in entries if e.brand}
    return sorted(b for b in corpus_brands if b.casefold() not in covered)


def format_report(run: EvalRun) -> str:
    """Render a run for a terminal or CI log.

    Args:
        run: The completed run.

    Returns:
        A report leading with failures rather than the pass rate. A summary
        that opens with "94% passing" invites skimming past the 6% that is the
        entire reason the run happened.
    """
    lines: list[str] = []

    if run.failed:
        lines.append(f"FAILED {len(run.failed)} of {len(run.results)} eval entries")
        lines.append("")
        for result in run.failed:
            mode = result.failure.value if result.failure else "unknown"
            lines.append(f"  {result.entry_id} [{mode}] {result.detail or ''}".rstrip())
            if result.missing_phrases:
                for phrase in result.missing_phrases:
                    lines.append(f"      missing: {phrase!r}")
        lines.append("")
        counts = run.failures_by_mode()
        summary = ", ".join(f"{mode.value}={n}" for mode, n in sorted(counts.items()))
        lines.append(f"  by mode: {summary}")
    else:
        lines.append(f"PASSED all {len(run.results)} eval entries")

    if run.coverage_gaps:
        lines.append("")
        lines.append(
            "  brands in the corpus with no eval entry (a regression here ships silently): "
            + ", ".join(run.coverage_gaps)
        )

    return "\n".join(lines)
