"""Tests for `app/ai/evaluation/construction.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.evaluation.construction import (
    EvalCandidate,
    VerifiedItem,
    candidates_from_verification,
    promote_candidate,
)
from app.models.schemas.evaluation import EvalCategory
from app.models.schemas.ingestion import VerificationDecision


def _item(**overrides: object) -> VerifiedItem:
    payload: dict[str, object] = {
        "document_id": "abb-acs880-fw",
        "decision": VerificationDecision.APPROVED,
        "title": "ACS880 firmware manual",
        "brand": "ABB",
        "page": 88,
        "reviewer_notes": "Covers overcurrent trips during acceleration.",
    }
    payload.update(overrides)
    return VerifiedItem.model_validate(payload)


def _candidate() -> EvalCandidate:
    return candidates_from_verification([_item()])[0]


# --- only verified content becomes a candidate -----------------------------


def test_an_approved_item_becomes_a_candidate() -> None:
    candidates = candidates_from_verification([_item()])
    assert len(candidates) == 1
    assert candidates[0].expected_citation.document_id == "abb-acs880-fw"
    assert candidates[0].expected_citation.page == 88


def test_a_rejected_item_never_becomes_a_candidate() -> None:
    """The eval set is the reference point for "verified".

    Sourcing it from content the reviewers refused would have it certify
    against exactly what verification exists to keep out.
    """
    assert candidates_from_verification([_item(decision=VerificationDecision.REJECTED)]) == []


def test_rejected_items_are_dropped_from_a_mixed_batch() -> None:
    items = [
        _item(document_id="approved-1"),
        _item(document_id="rejected-1", decision=VerificationDecision.REJECTED),
        _item(document_id="approved-2"),
    ]
    ids = [c.expected_citation.document_id for c in candidates_from_verification(items)]
    assert ids == ["approved-1", "approved-2"]


def test_candidates_keep_their_input_order() -> None:
    items = [_item(document_id=f"doc-{n}") for n in range(3)]
    ids = [c.expected_citation.document_id for c in candidates_from_verification(items)]
    assert ids == ["doc-0", "doc-1", "doc-2"]


def test_an_empty_batch_yields_nothing() -> None:
    assert candidates_from_verification([]) == []


def test_a_candidate_carries_the_reviewer_notes() -> None:
    """Carry the reviewer notes through to the candidate.

    They are usually the clearest statement of what the document answers,
    and the raw material for writing the query.
    """
    assert _candidate().reviewer_notes == "Covers overcurrent trips during acceleration."


def test_a_page_less_item_yields_a_page_less_citation() -> None:
    candidate = candidates_from_verification([_item(page=None)])[0]
    assert candidate.expected_citation.page is None


# --- a candidate is not an entry -------------------------------------------


def test_a_candidate_is_not_an_eval_entry() -> None:
    """The editorial parts are a human's judgement, not derivable.

    A guessed query or a guessed phrase is a false pass waiting to happen, so
    construction stops at the citation and hands the rest to the pod.
    """
    candidate = _candidate()
    assert not hasattr(candidate, "required_phrases")
    assert not hasattr(candidate, "query")


def test_promoting_a_candidate_produces_a_usable_entry() -> None:
    entry = promote_candidate(
        _candidate(),
        entry_id="e1",
        query="Why does the ACS880 trip on overcurrent during acceleration?",
        expected_answer_summary="Ramp time too short for the load inertia.",
        required_phrases=["acceleration time", "load inertia"],
    )
    assert entry.expected_citation is not None
    assert entry.expected_citation.document_id == "abb-acs880-fw"
    assert entry.brand == "ABB"
    assert entry.required_phrases == ["acceleration time", "load inertia"]


def test_promoting_carries_the_category_and_notes() -> None:
    entry = promote_candidate(
        _candidate(),
        entry_id="e1",
        query="q",
        expected_answer_summary="s",
        required_phrases=["p"],
        category=EvalCategory.REGRESSION,
        notes="Added after the 2026-08 mis-citation incident.",
    )
    assert entry.category is EvalCategory.REGRESSION
    assert entry.notes is not None


def test_promoting_without_phrases_is_refused() -> None:
    """The entry validator catches it, so no path produces an always-passing entry."""
    with pytest.raises(ValidationError, match="passes regardless"):
        promote_candidate(
            _candidate(),
            entry_id="e1",
            query="q",
            expected_answer_summary="s",
            required_phrases=[],
        )


def test_an_out_of_scope_entry_cannot_come_from_a_verified_document() -> None:
    """It asserts the corpus does *not* answer something.

    Deriving that from a document that does is a contradiction, and the
    resulting entry would fail the moment retrieval worked correctly.
    """
    with pytest.raises(ValueError, match="cannot be built from a verified document"):
        promote_candidate(
            _candidate(),
            entry_id="e1",
            query="q",
            expected_answer_summary="s",
            required_phrases=["p"],
            category=EvalCategory.OUT_OF_SCOPE,
        )
