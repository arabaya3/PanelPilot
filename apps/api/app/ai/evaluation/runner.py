"""Scoring the eval set against the current pipeline.

**The scorer is the thing most worth distrusting.** Every other check in this
repo fails loudly when it breaks. A scorer that is too generous fails
*quietly*: the run goes green, the pass rate looks healthy, and the regression
it was built to catch ships anyway. So the rules here are deliberately strict
and deliberately dumb — no fuzzy matching, no partial credit, no "close
enough". An entry passes when every assertion holds and fails otherwise.

Strict here means more than "not fuzzy". A bare substring test is *generous*:
it matches inside longer words, across sentence boundaries, and — worst —
inside negations, so "do not extend the acceleration time" would satisfy a
requirement for "acceleration time" while saying the opposite. Phrases are
therefore matched at word boundaries, within one sentence, and only outside a
negated clause.

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

import re
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

    Case is not a meaningful difference in an answer, and NFKC folding keeps a
    phrase from missing because the model emitted a full-width character, a
    ligature, or a non-breaking space. Runs of spaces and tabs collapse,
    because line wrapping is not a regression.

    Sentence and paragraph breaks are deliberately **preserved** as a marker
    rather than flattened to a space. Collapsing them lets a required phrase
    be satisfied by two fragments that merely abut across a paragraph break —
    text a human reads as two unrelated statements. Nothing beyond this:
    stemming or synonym matching would decide for itself what an answer meant.

    Args:
        text: Raw text.

    Returns:
        The folded form used for comparison.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    # A PARAGRAPH break becomes a sentinel no phrase can contain, so a match
    # cannot silently span two unrelated statements. A single newline is only
    # line wrapping and collapses like any other space — treating it as a
    # boundary would fail a correct answer for where the text happened to wrap.
    folded = re.sub(r"\n[^\S\n]*\n\s*", "\x00", folded)
    # `\x00` is not a regex whitespace character, so the sentinel survives
    # this collapse without needing to be excluded from the class.
    return re.sub(r"\s+", " ", folded).strip()


# A phrase must land on word boundaries. Without this, "deceleration" matches
# inside "decelerationtimer" and "acceleration time" matches inside
# "acceleration timezone" — a scorer that accepts either is passing answers no
# engineer would accept.
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Build a word-boundary-anchored pattern for one phrase.

    Args:
        phrase: The normalised required phrase.

    Returns:
        A compiled pattern matching the phrase only at word boundaries.
    """
    escaped = re.escape(phrase)
    # `\b` only asserts a boundary next to a word character. A phrase starting
    # or ending in punctuation ("30 A." ) has none there, so the assertion is
    # applied conditionally at each end rather than unconditionally.
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""
    return re.compile(f"{prefix}{escaped}{suffix}")


# Auxiliary negations, which reverse the verb that follows them. Only these:
# every other candidate scopes unreliably. "without", "avoid", "rather than"
# and "instead of" are *contrastive* — the thing they negate is usually the
# clause before the phrase, not the phrase itself, so "rather than replace the
# drive, extend the acceleration time" is a correct answer that treating them
# as negations would reject. Rejecting correct answers is not the safe side of
# this trade: it trains people to ignore a red eval run.
_NEGATIONS = (
    "do not",
    "don't",
    "does not",
    "doesn't",
    "must not",
    "mustn't",
    "should not",
    "shouldn't",
    "never",
    "cannot",
    "can't",
)


# Sentence terminators. A comma is deliberately *not* one: an interrupting
# clause ("do not, under any circumstances, extend the ramp") would otherwise
# put the negation out of scope and pass the answer that says the opposite.
_SENTENCE_BREAKS = ("\x00", ". ", "; ", "! ", "? ")


def _is_negated(answer: str, start: int) -> bool:
    """Report whether a match at ``start`` sits in a negated sentence.

    Args:
        answer: The normalised answer.
        start: Index where the phrase matched.

    Returns:
        ``True`` if an auxiliary negation appears between the start of the
        match's sentence and the match itself. Scoped to the sentence — a
        negation two sentences earlier does not negate this one, and treating
        it as though it did would fail correct answers.

        This is a heuristic and is documented as one on ``_missing_phrases``.
        It catches the blunt "do not X" form that a substring test would pass;
        it cannot parse English, and an entry that needs a guarantee should use
        ``forbidden_phrases`` instead.
    """
    sentence_start = max(
        (answer.rfind(marker, 0, start) for marker in _SENTENCE_BREAKS),
        default=-1,
    )
    sentence = answer[sentence_start + 1 : start]
    return any(marker in sentence for marker in _NEGATIONS)


def _missing_phrases(answer: str, required: Iterable[str]) -> list[str]:
    """Find required phrases absent from — or negated within — an answer.

    A phrase must appear at word boundaries, within one sentence, and not
    after an auxiliary negation in that sentence. "Do not extend the
    acceleration time" does not satisfy a requirement for "acceleration time":
    it says the opposite, and a scorer that passes it certifies exactly the
    answer that would hurt someone.

    **The negation check is a heuristic, not a guarantee.** It matches a fixed
    list of auxiliary negations; it does not parse English, and it will miss
    forms that express negation some other way ("the acceleration time is
    irrelevant here"). It is worth having because it catches the blunt form a
    bare substring test would pass, but an entry that must assert an answer
    does *not* say something should say so explicitly with
    ``EvalEntry.forbidden_phrases``, which is exact rather than inferred.

    Args:
        answer: The pipeline's answer text.
        required: Phrases that must all appear.

    Returns:
        The phrases that did not genuinely appear, in the order required.
    """
    folded = _normalise(answer)
    missing = []
    for phrase in required:
        pattern = _phrase_pattern(_normalise(phrase))
        if not any(not _is_negated(folded, m.start()) for m in pattern.finditer(folded)):
            missing.append(phrase)
    return missing


def _present_phrases(answer: str, forbidden: Iterable[str]) -> list[str]:
    """Find forbidden phrases that appear in an answer.

    Deliberately *not* negation-aware, unlike :func:`_missing_phrases`. A
    forbidden phrase is an exact assertion — "this answer must not contain the
    string" — and inferring that a negated mention is acceptable would put the
    guessing back into the one check that exists to avoid it.

    Args:
        answer: The pipeline's answer text.
        forbidden: Phrases that must not appear.

    Returns:
        The phrases that did appear, in the order they were listed.
    """
    folded = _normalise(answer)
    return [
        phrase
        for phrase in forbidden
        if _phrase_pattern(_normalise(phrase)).search(folded) is not None
    ]


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

    forbidden = _present_phrases(answer, entry.forbidden_phrases)
    if forbidden:
        return EvalResult(
            entry_id=entry.id,
            passed=False,
            failure=FailureMode.WRONG_ANSWER,
            detail=f"answer contained forbidden phrase(s): {forbidden}",
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
            Defaults to empty, which reports no gaps — pass the real list, or
            the coverage check certifies nothing while looking clean.

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
        corpus_brands: Brands present in the corpus. Pass the real list — an
            empty one reports no gaps, which reads identically to full
            coverage.

    Returns:
        Sorted brands the set does not cover. As brand coverage expands the
        set must expand with it — a brand with zero entries is one where a
        regression ships without anything going red.

        ``brand`` is optional on an entry, so a set whose entries all omit it
        reports *every* corpus brand as a gap. That is deliberate: an entry
        that does not say which brand it covers cannot be credited to one, and
        a loud false alarm is recoverable where a silent gap is not.
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
