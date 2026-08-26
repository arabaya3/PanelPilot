"""The regression eval set: entries, results, and the run summary.

An eval set is the only thing standing between "we changed retrieval" and
"we changed retrieval and silently broke forty answers nobody re-checked."
It is therefore worth more scepticism than most code: a scorer that is
generous certifies regressions as passes, and a green run that means nothing
is worse than no run at all, because it is trusted.

Two decisions follow from that:

**Citation correctness is not negotiable.** An entry passes only if the
expected citation is actually retrieved. An answer that reads correctly while
citing the wrong page is the precise failure cite-or-refuse exists to prevent,
and it is invisible to any check that only reads the prose.

**Entries carry the reason they exist.** ``category`` and ``notes`` are not
decoration — when an entry starts failing a year from now, the engineer
looking at it needs to know whether it was an easy sanity check or the
deliberately ambiguous query someone added after a production incident.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

# Same reasoning as the diagnosis contract: `min_length` alone accepts "   ",
# and an eval entry with a whitespace query is one that silently tests nothing.
NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]


class EvalCategory(StrEnum):
    """Why an entry is in the set.

    The set deliberately is not all clean queries. Regressions hide in the
    ambiguous and adversarial ones, so those are tracked distinctly rather
    than averaged into a single pass rate that looks healthy while the hard
    cases rot.
    """

    # A plain question with one clearly correct documented answer.
    STRAIGHTFORWARD = "straightforward"
    # Phrasing that could reasonably mean more than one thing.
    AMBIGUOUS = "ambiguous"
    # Near-miss wording that has previously retrieved the wrong document.
    EDGE_CASE = "edge_case"
    # The corpus genuinely does not answer this. The pipeline must refuse.
    OUT_OF_SCOPE = "out_of_scope"
    # Added in response to a specific production failure.
    REGRESSION = "regression"


class ExpectedCitation(BaseModel):
    """The source an entry's answer must rest on.

    Attributes:
        document_id: The production document that must be cited.
        page: Optional page. When set it is checked — a correct document at
            the wrong page still sends an engineer to the wrong procedure.
    """

    document_id: NonBlankText
    page: int | None = Field(default=None, ge=1)


class EvalEntry(BaseModel):
    """One query with the answer and citation it is expected to produce.

    Attributes:
        id: Stable identifier. Referenced in run history, so renaming one
            makes past results unreadable.
        query: What the engineer asks.
        category: Why this entry exists.
        expected_answer_summary: The substance the answer must contain,
            checked as required key phrases rather than as prose equality —
            see ``required_phrases``.
        required_phrases: Phrases that must all appear in the answer. This is
            the actual assertion; ``expected_answer_summary`` is for the human
            reading a failure. Empty only for ``OUT_OF_SCOPE`` entries, where
            the correct behaviour is to produce no answer at all.
        expected_citation: The source the answer must cite. ``None`` only for
            ``OUT_OF_SCOPE``.
        brand: Optional manufacturer filter the query should be run with.
        model: Optional equipment model filter.
        notes: Why this entry was added — especially the incident, for
            ``REGRESSION`` entries.
    """

    id: NonBlankText
    query: NonBlankText
    category: EvalCategory
    expected_answer_summary: NonBlankText
    required_phrases: list[NonBlankText] = Field(default_factory=list)
    expected_citation: ExpectedCitation | None = None
    brand: str | None = None
    model: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _expectations_match_the_category(self) -> EvalEntry:
        """Keep an entry from asserting nothing.

        Returns:
            The validated entry.

        Raises:
            ValueError: If an answerable entry has no citation or no required
                phrase, or if an out-of-scope entry carries either. The first
                is an entry that passes no matter what the pipeline returns —
                the most dangerous thing an eval set can contain, because it
                inflates the pass rate while testing nothing. The second is an
                entry whose category and content disagree, so it is unclear
                which the runner should believe.
        """
        if self.category is EvalCategory.OUT_OF_SCOPE:
            if self.expected_citation is not None:
                raise ValueError(f"{self.id}: an out-of-scope entry must not expect a citation")
            if self.required_phrases:
                raise ValueError(
                    f"{self.id}: an out-of-scope entry must not require answer phrases"
                )
            return self

        if self.expected_citation is None:
            raise ValueError(f"{self.id}: an answerable entry must name its expected citation")
        if not self.required_phrases:
            raise ValueError(
                f"{self.id}: an answerable entry must require at least one phrase, "
                "or it passes regardless of what the pipeline returns"
            )
        if len(set(self.required_phrases)) != len(self.required_phrases):
            raise ValueError(f"{self.id}: duplicate required phrases")
        return self


class FailureMode(StrEnum):
    """How an entry failed, so a run summary is diagnosable at a glance."""

    # The answer did not contain every required phrase.
    WRONG_ANSWER = "wrong_answer"
    # The answer was right but rested on the wrong source.
    WRONG_CITATION = "wrong_citation"
    # The pipeline refused a question the corpus does answer.
    UNEXPECTED_REFUSAL = "unexpected_refusal"
    # The pipeline answered a question it should have refused. The worst
    # outcome in the set: it is the guardrail failing open.
    ANSWERED_OUT_OF_SCOPE = "answered_out_of_scope"
    # The pipeline raised rather than answering or refusing.
    PIPELINE_ERROR = "pipeline_error"


class EvalResult(BaseModel):
    """The outcome of running one entry.

    Attributes:
        entry_id: Which entry this is for.
        passed: Whether every assertion held.
        failure: How it failed. Set exactly when ``passed`` is false.
        detail: What actually happened, for the engineer reading the failure.
        missing_phrases: Required phrases absent from the answer.
        actual_citations: Document ids the pipeline actually cited.
    """

    entry_id: str
    passed: bool
    failure: FailureMode | None = None
    detail: str | None = None
    missing_phrases: list[str] = Field(default_factory=list)
    actual_citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _failure_matches_outcome(self) -> EvalResult:
        """Keep a result from being unreadable.

        Returns:
            The validated result.

        Raises:
            ValueError: If a pass carries a failure mode, or a failure carries
                none. A failure with no mode cannot be triaged, and a pass with
                one cannot be trusted.
        """
        if self.passed and self.failure is not None:
            raise ValueError(f"{self.entry_id}: a passing result must not carry a failure mode")
        if not self.passed and self.failure is None:
            raise ValueError(f"{self.entry_id}: a failing result must say how it failed")
        return self


class EvalRun(BaseModel):
    """The result of running the whole set.

    Attributes:
        results: One result per entry, in the set's order.
        coverage_gaps: Brands present in the corpus with no eval entry. A
            brand with zero coverage is a brand where a regression ships
            silently, so it is reported rather than left to be noticed.
    """

    results: list[EvalResult] = Field(min_length=1)
    coverage_gaps: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> int:
        """Count of entries that passed."""
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> list[EvalResult]:
        """Failing results, for the report."""
        return [r for r in self.results if not r.passed]

    @property
    def pass_rate(self) -> float:
        """Fraction of entries passing, in [0, 1]."""
        return self.passed / len(self.results)

    def failures_by_mode(self) -> dict[FailureMode, int]:
        """Count failures per mode.

        Returns:
            A mapping of mode to count, omitting modes that did not occur.
            Read this before the pass rate: forty wrong-citation failures and
            forty wrong-answer failures are the same number and completely
            different problems.
        """
        counts: dict[FailureMode, int] = {}
        for result in self.failed:
            if result.failure is not None:
                counts[result.failure] = counts.get(result.failure, 0) + 1
        return counts
