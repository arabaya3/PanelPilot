"""Turning verification output into eval-set candidates.

The eval set is built from the verification pipeline's own output: an item
that cleared review with an ``APPROVED`` decision is a *candidate* entry.

**Candidate, not entry.** Not every verified document belongs in the
regression set — the set is deliberately smaller than the corpus, because a
set that grows with every promotion becomes too slow to run on every change
and stops being run at all. So this module produces candidates for the
QA/verification pod to review for inclusion, and cannot produce a finished
entry on its own: the query and the phrases that make an answer correct are
editorial judgements, and inventing them here would fill the set with entries
nobody chose.

**A rejected or unreviewed item is never a candidate.** The eval set is the
reference point for "verified"; sourcing it from anything that did not clear
verification would make it certify against content the reviewers refused.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from app.models.schemas.evaluation import (
    EvalCategory,
    EvalEntry,
    ExpectedCitation,
    NonBlankText,
)
from app.models.schemas.ingestion import VerificationDecision


class VerifiedItem(BaseModel):
    """One reviewed document, as the eval set sees it.

    A projection of ``VerificationItemRow`` joined to its staged document,
    carried as a plain schema so this module needs no database session and
    stays testable without one.

    Attributes:
        document_id: The production document id, once promoted.
        decision: What the reviewer decided.
        title: Document title, for the human choosing candidates.
        brand: Manufacturer, used for coverage accounting.
        page: The page reviewed, when the review was page-scoped.
        reviewer_notes: What the reviewer wrote — often the clearest statement
            of what the document actually answers.
    """

    document_id: NonBlankText
    decision: VerificationDecision
    title: NonBlankText
    brand: str | None = None
    page: int | None = Field(default=None, ge=1)
    reviewer_notes: str | None = None


class EvalCandidate(BaseModel):
    """A verified item proposed for the eval set, pending pod review.

    Deliberately **not** an :class:`EvalEntry`. It carries the citation, which
    is a fact of the verification record, and leaves the query, the required
    phrases and the category to a human — those are the parts that decide what
    the entry actually tests, and a guessed phrase is a false pass waiting to
    happen.

    Attributes:
        expected_citation: The source, taken from the verification record.
        source_title: Document title, so a reviewer can judge relevance.
        brand: Manufacturer, for coverage accounting.
        reviewer_notes: The verifying reviewer's notes, as raw material for
            writing the query.
    """

    expected_citation: ExpectedCitation
    source_title: NonBlankText
    brand: str | None = None
    reviewer_notes: str | None = None


def candidates_from_verification(items: Iterable[VerifiedItem]) -> list[EvalCandidate]:
    """Propose eval candidates from reviewed items.

    Args:
        items: Reviewed documents.

    Returns:
        One candidate per approved item, in input order. Rejected items are
        dropped: the eval set is the reference point for "verified", so
        sourcing it from content the reviewers refused would have it certify
        against exactly what verification exists to keep out.
    """
    return [
        EvalCandidate(
            expected_citation=ExpectedCitation(document_id=item.document_id, page=item.page),
            source_title=item.title,
            brand=item.brand,
            reviewer_notes=item.reviewer_notes,
        )
        for item in items
        if item.decision is VerificationDecision.APPROVED
    ]


def promote_candidate(
    candidate: EvalCandidate,
    *,
    entry_id: str,
    query: str,
    expected_answer_summary: str,
    required_phrases: Sequence[str],
    category: EvalCategory = EvalCategory.STRAIGHTFORWARD,
    notes: str | None = None,
) -> EvalEntry:
    """Turn a pod-reviewed candidate into an eval entry.

    The editorial parts are arguments rather than derived: a human decides
    what the query is, which phrases make an answer correct, and why the entry
    exists. That is the pod review the spec calls for, expressed as a function
    signature — there is no way to obtain an entry from a candidate without
    supplying them.

    Args:
        candidate: The reviewed candidate.
        entry_id: Stable id for the new entry.
        query: The question this entry asks.
        expected_answer_summary: Prose description of the correct answer.
        required_phrases: The phrases that must appear. Choose ones that are
            load-bearing to the answer being right, not merely present in it.
        category: Why this entry exists.
        notes: Any additional context, such as the incident that prompted it.

    Returns:
        The entry, validated — which refuses it if the phrases are missing or
        duplicated.

    Raises:
        ValueError: If ``category`` is ``OUT_OF_SCOPE``. Those entries assert
            that the corpus does *not* answer something, so they cannot be
            derived from a verified document that does.
    """
    if category is EvalCategory.OUT_OF_SCOPE:
        raise ValueError(
            "an out-of-scope entry asserts the corpus does not answer a question; "
            "it cannot be built from a verified document that does"
        )

    return EvalEntry(
        id=entry_id,
        query=query,
        category=category,
        expected_answer_summary=expected_answer_summary,
        required_phrases=list(required_phrases),
        expected_citation=candidate.expected_citation,
        brand=candidate.brand,
        notes=notes,
    )
