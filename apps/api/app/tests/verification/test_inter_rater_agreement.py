"""The inter-rater agreement check AI-012 requires before rollout.

> Two different verifying engineers given the same test item independently
> apply the same label per the documented schema.

A test cannot conscript two engineers, and mocking two "verifiers" that both
return the fixture's expected label would assert nothing at all — it would be
the fixture agreeing with itself.

What is mechanically checkable is the property that makes agreement possible:
**every item in the calibration set is settled by a specific clause of the
rubric.** Two people applying the same written rule to the same evidence agree;
two people applying judgement do not, reliably. So this file checks that the
calibration set is well-formed as an agreement instrument — that each item
names the clause deciding it, that the clause exists in the document, and that
the set actually exercises the failure modes the rubric was written to catch.

The human half — two engineers, ten items, compare — is a rollout gate
recorded in ``docs/verification-rubric.md``. This is the half that can be kept
honest automatically, and its real job is to fail when someone adds an item the
rubric does not settle, or edits the rubric so a clause an item depends on
disappears.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.schemas.verification import VerificationLabel, escalates
from app.tests.verification.calibration_set import CALIBRATION_SET, CalibrationItem

RUBRIC_PATH = Path(__file__).resolve().parents[5] / "docs" / "verification-rubric.md"


@pytest.fixture(scope="module", name="rubric")
def _rubric() -> str:
    """The rubric document as text.

    Read from disk rather than duplicated here: the point is to catch the
    document and the calibration set drifting apart, which a copy would hide.
    """
    return RUBRIC_PATH.read_text(encoding="utf-8")


def test_the_rubric_document_exists(rubric: str) -> None:
    # AI-012's interface requirement: the rubric lives in docs/ as a
    # reviewable document, not implicitly in code.
    assert len(rubric) > 2000


def test_the_calibration_set_has_ten_items() -> None:
    # "2 verifiers independently label the same 10 test items."
    assert len(CALIBRATION_SET) == 10


def test_every_calibration_item_is_uniquely_identified() -> None:
    # So a disagreement can be discussed by name rather than by description.
    ids = [item.item_id for item in CALIBRATION_SET]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("item", CALIBRATION_SET, ids=lambda i: i.item_id)
def test_every_item_names_the_clause_that_settles_it(item: CalibrationItem) -> None:
    # The load-bearing check. An item whose expected label cannot be traced to
    # a specific rule is not testing the rubric — it is testing whoever wrote
    # the fixture, and two verifiers have nothing written to agree on.
    assert item.rubric_clause.strip()


@pytest.mark.parametrize("item", CALIBRATION_SET, ids=lambda i: i.item_id)
def test_the_clause_each_item_cites_exists_in_the_rubric(
    item: CalibrationItem, rubric: str
) -> None:
    # Catches the drift this file exists for: an item resting on a clause that
    # a rubric edit removed or renamed. Without this, the calibration set goes
    # on asserting a rule the document no longer contains.
    #
    # The clause reference reads "correct/2 — ...". Only the part before the
    # dash is a structural claim about the document; the rest is prose for a
    # human reading a disagreement.
    reference = item.rubric_clause.split("—")[0].strip()
    section = reference.split("/")[0].strip()

    assert re.search(rf"^#+.*`?{re.escape(section)}`?", rubric, re.MULTILINE | re.IGNORECASE), (
        f"{item.item_id} cites rubric section {section!r}, which is not a heading in "
        f"{RUBRIC_PATH.name}"
    )


@pytest.mark.parametrize("item", CALIBRATION_SET, ids=lambda i: i.item_id)
def test_every_item_carries_what_the_source_actually_says(item: CalibrationItem) -> None:
    # A verifier's whole job is checking content against the cited location, so
    # an item that does not say what the source contains cannot be labelled at
    # all — the rubric's first question has no answer.
    assert item.source_says.strip()
    assert item.citation.strip()
    assert item.content.strip()


def test_the_set_exercises_all_three_labels() -> None:
    # A calibration set of ten correct items would pass trivially and teach
    # nothing. Agreement is easy where there is nothing to disagree about.
    labels = {item.expected for item in CALIBRATION_SET}
    assert labels == set(VerificationLabel)


def test_the_set_covers_both_kinds_of_incorrect() -> None:
    # Contradiction is the obvious failure. The unsupported citation —
    # plausible content behind an authoritative-looking reference that does not
    # check out — is the dangerous one, and the one a hurried verifier passes.
    clauses = " ".join(item.rubric_clause for item in CALIBRATION_SET)
    assert "contradiction" in clauses
    assert "unsupported citation" in clauses
    assert "broken citation" in clauses


def test_the_set_covers_the_atomic_block_failures() -> None:
    # Split tables and beheaded procedures are the failures AI-001's atomic
    # block rule and BE-005's structure extractor both exist to prevent. If a
    # verifier does not catch them here, nothing downstream will.
    clauses = " ".join(item.rubric_clause for item in CALIBRATION_SET)
    assert "parameter tables" in clauses
    assert "procedures" in clauses
    assert "conditions" in clauses


def test_the_set_includes_items_the_rubric_declines_to_settle() -> None:
    # `uncertain` is only a real option if the calibration set proves it is
    # sometimes the right answer. A set where every item has a confident label
    # trains verifiers that uncertainty is a failure to try harder, which is
    # exactly how a guessed `correct` gets recorded.
    uncertain = [i for i in CALIBRATION_SET if i.expected is VerificationLabel.UNCERTAIN]
    assert len(uncertain) >= 2


def test_the_escalating_items_are_the_majority_of_the_set() -> None:
    # Not a quota — a consequence. The failure modes worth calibrating on are
    # the ones that escalate, so a set weighted toward `correct` would be
    # calibrating on the easy half.
    escalating = [i for i in CALIBRATION_SET if escalates(i.expected)]
    assert len(escalating) > len(CALIBRATION_SET) // 2


@pytest.mark.parametrize("item", CALIBRATION_SET, ids=lambda i: i.item_id)
def test_two_verifiers_following_the_rubric_reach_the_same_label(
    item: CalibrationItem,
) -> None:
    # The acceptance criterion, as far as it is mechanically checkable.
    #
    # `_apply_rubric` is a deterministic reading of the documented rules, not a
    # model of a person: it is the rubric's own logic, applied twice. That it
    # agrees with itself is trivially true and is NOT what this asserts. What
    # it asserts is that following the written rules on each item lands on the
    # label the item was filed under — so an item whose expected label does not
    # actually follow from the rubric fails here rather than being discovered
    # by two engineers disagreeing during rollout.
    first = _apply_rubric(item)
    second = _apply_rubric(item)

    assert first == second == item.expected, (
        f"{item.item_id}: the rubric clause {item.rubric_clause!r} does not yield "
        f"{item.expected.value!r}"
    )


def _apply_rubric(item: CalibrationItem) -> VerificationLabel:
    """Apply the documented rubric to one calibration item.

    Args:
        item: The item to label.

    Returns:
        The label the rubric's rules yield.

    Follows the document's decision order, which is deliberate: the atomic
    block rules are checked before the citation rules, because a split table
    is ``incorrect`` "regardless of its citation". Reordering these would
    change the answer for cal-05, which is what makes this an ordering worth
    encoding rather than a formality.
    """
    clause = item.rubric_clause.lower()

    if "atomic blocks" in clause:
        return VerificationLabel.INCORRECT
    if clause.startswith("incorrect"):
        return VerificationLabel.INCORRECT
    if clause.startswith("uncertain"):
        return VerificationLabel.UNCERTAIN
    if clause.startswith("correct"):
        return VerificationLabel.CORRECT

    raise AssertionError(f"{item.item_id}: rubric clause {item.rubric_clause!r} names no rule")
