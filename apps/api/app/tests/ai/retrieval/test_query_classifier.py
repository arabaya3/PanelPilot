"""Tests for `app/ai/retrieval/query_classifier.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Misclassification is not a crash; it is a query silently retrieved under the
wrong blend. So these pin the actual forms engineers type, not tidy examples.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval.query_classifier import classify_query
from app.models.schemas.retrieval_config import QueryType


@pytest.mark.parametrize(
    "query",
    [
        "F0001",
        "F0001 on the ACS880",
        "E-024",
        "AL 5091",
        "fault 2340",
        "alarm code 7081",
        "error F 0001",
        "what does E-024 mean",
    ],
)
def test_a_fault_code_is_lexical(query: str) -> None:
    """Every fault code in a manual is semantically adjacent to every other.

    The vector leg is close to noise for these; the code is a literal token.
    """
    assert classify_query(query) is QueryType.FAULT_CODE


@pytest.mark.parametrize(
    "query",
    [
        "par 21.03",
        "parameter 99.04",
        "P-1204",
        "set 20.01",
        "par. 30.11 range",
    ],
)
def test_a_parameter_reference_is_a_parameter_lookup(query: str) -> None:
    assert classify_query(query) is QueryType.PARAMETER_LOOKUP


@pytest.mark.parametrize(
    "query",
    [
        "the drive trips when the conveyor starts under load",
        "motor runs hot after about twenty minutes",
        "why does the contactor chatter",
        "what causes nuisance tripping on acceleration",
        "the panel won't reset",
        "it randomly loses communication",
    ],
)
def test_a_described_symptom_is_semantic(query: str) -> None:
    """The engineer's words are not the manual's words."""
    assert classify_query(query) is QueryType.SYMPTOM_DESCRIPTION


def test_a_code_inside_a_description_is_still_a_description() -> None:
    """The engineer is asking what the code means *in their situation*.

    Only the semantic leg reaches the explanation; under fault-code weights
    the vector leg is 0.15 and the paraphrase is effectively unfindable.
    """
    assert (
        classify_query("F0001 keeps coming back when the line starts")
        is QueryType.SYMPTOM_DESCRIPTION
    )


def test_ambiguity_resolves_toward_the_semantic_leg() -> None:
    """The asymmetry is deliberate.

    A lexical match still contributes under semantic weights (bm25=0.3);
    a paraphrase under lexical weights (vector=0.15) largely does not. So the
    cost of guessing wrong is much lower in this direction.
    """
    ambiguous = classify_query("par 21.03 sometimes does not hold")
    assert ambiguous is QueryType.SYMPTOM_DESCRIPTION


def test_a_bare_number_is_not_a_fault_code() -> None:
    """A bare number is a measurement, not a code.

    "24" alone is far more likely a voltage or a count.

    Treating it as one would route a measurement question to weights that
    all but disable the semantic leg.
    """
    assert classify_query("24") is QueryType.SYMPTOM_DESCRIPTION
    assert classify_query("is 24 volts correct here") is QueryType.SYMPTOM_DESCRIPTION


@pytest.mark.parametrize(
    "query",
    [
        "is 24 volts correct here",
        "we measured 480 across the terminals",
        "the setpoint is 50 hz",
    ],
)
def test_a_number_after_an_ordinary_word_is_not_a_code(query: str) -> None:
    """A letter-and-digit pattern must not match across a word boundary.

    "is 24" would otherwise read as a code and route a measurement question
    to weights that all but disable the semantic leg. This is a real bug the
    suite caught, not a hypothetical.
    """
    assert classify_query(query) is QueryType.SYMPTOM_DESCRIPTION


def test_an_empty_query_does_not_raise() -> None:
    """Rejecting it belongs to the caller's validation, not here."""
    assert classify_query("") is QueryType.SYMPTOM_DESCRIPTION
    assert classify_query("   ") is QueryType.SYMPTOM_DESCRIPTION


def test_classification_is_case_insensitive() -> None:
    assert classify_query("f0001") is classify_query("F0001")
    assert classify_query("WHY DOES IT TRIP") is QueryType.SYMPTOM_DESCRIPTION


def test_every_query_type_is_reachable() -> None:
    """A type nothing classifies to is a type that is never tuned or measured."""
    reached = {
        classify_query("F0001"),
        classify_query("par 21.03"),
        classify_query("it trips when starting"),
    }
    assert reached == set(QueryType)
