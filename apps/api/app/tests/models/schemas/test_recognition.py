"""Tests for `app/models/schemas/recognition.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.recognition import (
    DisplayVerdict,
    FaultRecognitionResult,
    RecognisedField,
)

# --- a confidence has to be about something ---------------------------------


def test_a_confidence_without_a_value_is_refused() -> None:
    """A number next to nothing is the shape of an answer.

    Something downstream eventually reads it without checking the value
    beside it.
    """
    with pytest.raises(ValidationError, match="confidence without a value"):
        RecognisedField(confidence=0.9)


def test_an_unread_field_is_allowed() -> None:
    """Ordinary, not an error: a display rarely shows the manufacturer."""
    field = RecognisedField()
    assert field.value is None
    assert field.confidence == 0.0


def test_confidence_stays_in_the_unit_interval() -> None:
    with pytest.raises(ValidationError):
        RecognisedField(value="F1", confidence=1.5)


def test_a_value_is_stripped() -> None:
    """So " F0001" and "F0001" are not two different codes."""
    assert RecognisedField(value=" F0001 ", confidence=0.9).value == "F0001"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_is_refused(blank: str) -> None:
    """It would read as "the model read something" when it read nothing."""
    with pytest.raises(ValidationError):
        RecognisedField(value=blank, confidence=0.9)


# --- the trust check --------------------------------------------------------


def test_a_field_at_the_threshold_is_trusted() -> None:
    assert RecognisedField(value="F1", confidence=0.8).trusted_at(0.8)


def test_a_field_below_the_threshold_is_not() -> None:
    assert not RecognisedField(value="F1", confidence=0.79).trusted_at(0.8)


def test_an_unread_field_is_never_trusted() -> None:
    """Whatever the number beside it.

    The default confidence is 0.0, but the check is on the value: a field
    with no value has nothing to trust.
    """
    assert not RecognisedField().trusted_at(0.0)


# --- the verdict gates the fields -------------------------------------------


def test_a_readable_display_may_report_a_code() -> None:
    result = FaultRecognitionResult(
        verdict=DisplayVerdict.FAULT_DISPLAY,
        fault_code=RecognisedField(value="F0001", confidence=0.9),
    )
    assert result.fault_code.value == "F0001"


@pytest.mark.parametrize("verdict", [DisplayVerdict.NOT_A_FAULT_DISPLAY, DisplayVerdict.UNREADABLE])
def test_a_rejected_photo_cannot_report_a_code(verdict: DisplayVerdict) -> None:
    """The failure this schema exists to prevent.

    A model that has decided the photo is a wiring diagram, or that the screen
    is illegible, and reported a code anyway has invented the code — and it
    will look exactly like a real one.
    """
    with pytest.raises(ValidationError, match="invented"):
        FaultRecognitionResult(
            verdict=verdict,
            fault_code=RecognisedField(value="F0001", confidence=0.95),
        )


def test_a_rejected_photo_may_still_say_what_it_saw() -> None:
    """The note is how the engineer learns what to photograph instead."""
    result = FaultRecognitionResult(
        verdict=DisplayVerdict.NOT_A_FAULT_DISPLAY,
        note="It appears to be a motor nameplate.",
    )
    assert result.note


def test_the_verdicts_distinguish_wrong_subject_from_unreadable() -> None:
    """The engineer's fix differs: photograph something else, or retake it.

    Collapsing them into one "no" would tell someone with a glare-affected
    screen to go find a different display.
    """
    assert {v.value for v in DisplayVerdict} == {
        "fault_display",
        "not_a_fault_display",
        "unreadable",
    }


def test_every_field_defaults_to_unread() -> None:
    """So a model reporting only a code does not imply a brand it never saw."""
    result = FaultRecognitionResult(verdict=DisplayVerdict.FAULT_DISPLAY)
    assert result.fault_code.value is None
    assert result.brand.value is None
    assert result.model.value is None
