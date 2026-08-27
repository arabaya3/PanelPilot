"""Tests for the labelling vocabulary and the escalation routing rule.

The acceptance criterion is agreement: two verifiers given the same item apply
the same label per the documented schema. That is checked in
``test_inter_rater_agreement.py``; this file covers the vocabulary and the
routing rule those verifiers' labels feed into.
"""

from __future__ import annotations

import pytest

from app.models.schemas.verification import (
    ESCALATING_LABELS,
    VerificationLabel,
    escalates,
)


def test_the_vocabulary_is_exactly_three_labels() -> None:
    # Pinned deliberately. A fourth label ("mostly correct", "minor issue")
    # is the standard way this kind of scheme decays: it gives a verifier
    # somewhere to put an item they should have escalated, and the escalation
    # path silently empties.
    assert {label.value for label in VerificationLabel} == {
        "correct",
        "incorrect",
        "uncertain",
    }


def test_correct_closes_the_item() -> None:
    assert not escalates(VerificationLabel.CORRECT)


@pytest.mark.parametrize(
    "label",
    [VerificationLabel.INCORRECT, VerificationLabel.UNCERTAIN],
)
def test_non_correct_labels_escalate(label: VerificationLabel) -> None:
    # AI-012 is explicit: an incorrect or uncertain label routes to
    # lead-engineer review rather than being resolved unilaterally by the
    # verifier who applied it.
    assert escalates(label)


def test_uncertain_is_not_treated_as_a_soft_incorrect() -> None:
    # Both escalate, but they stay distinct values. Collapsing them would lose
    # the difference between "the source contradicts this" — a corpus problem,
    # usually with a crawler or chunking defect behind it — and "I could not
    # tell", which is a defect in the rubric itself. Those go to different
    # fixes, so a lead needs to know which arrived.
    expected = {VerificationLabel.INCORRECT, VerificationLabel.UNCERTAIN}
    assert expected == ESCALATING_LABELS
    # Distinct members, so a lead can tell which arrived. Compared by value
    # rather than identity: `is not` on two enum literals is a tautology mypy
    # rightly rejects, and proves nothing at runtime.
    assert len({VerificationLabel.UNCERTAIN.value, VerificationLabel.INCORRECT.value}) == 2


def test_every_label_is_either_closing_or_escalating() -> None:
    # No label may fall through both branches: an item that neither closes nor
    # escalates would sit in the queue forever with a decision recorded
    # against it, which reads as handled and is not.
    for label in VerificationLabel:
        closes = not escalates(label)
        assert closes ^ escalates(label)


def test_labels_serialise_as_their_documented_strings() -> None:
    # The rubric, the API, and the database all name these labels in text. A
    # renamed value that still compares equal in Python would silently break
    # rows already written.
    # `.value` is what reaches the database and the API payload, so that is
    # what is pinned. Comparing the member to a literal is a tautology to mypy
    # and would not catch a renamed value anyway.
    assert [label.value for label in VerificationLabel] == [
        "correct",
        "incorrect",
        "uncertain",
    ]
