"""Tests for `app/ai/recognition.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

**On the acceptance criterion.** AI-008 asks for ≥20 real photographs spanning
lighting, angle and glare variation, plus at least 2 deliberately off-topic
images. Those photographs do not exist in this repository, and no synthetic
substitute is worth anything: the whole question is how the model behaves on a
glare-affected screen shot at an angle by someone standing on a ladder, and a
generated image proves nothing about that.

So the corpus test below is written and skips, keyed off a directory and a
manifest. Drop the photos in and it runs. That is a data gap, stated as one —
not a claim that the criterion is met.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.ai import recognition
from app.core.errors import ValidationError
from app.models.schemas.images import ImageFormat
from app.models.schemas.recognition import (
    DisplayVerdict,
    FaultRecognitionResult,
    RecognisedField,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60


def _result(**overrides: Any) -> FaultRecognitionResult:
    payload: dict[str, Any] = {
        "verdict": DisplayVerdict.FAULT_DISPLAY,
        "fault_code": RecognisedField(value="F0001", confidence=0.95),
    }
    payload.update(overrides)
    return FaultRecognitionResult.model_validate(payload)


class _Block:
    def __init__(self, kind: str, name: str | None = None, payload: Any = None) -> None:
        self.type = kind
        self.name = name
        self.input = payload


class _Message:
    def __init__(self, *blocks: _Block) -> None:
        self.content = list(blocks)


class _FakeClient:
    """Records the request and returns a canned report."""

    def __init__(self, payload: Any = None, *, blocks: list[_Block] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._blocks = blocks
        self._payload = payload
        self.messages = self

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        if self._blocks is not None:
            return _Message(*self._blocks)
        return _Message(_Block("tool_use", recognition.RECOGNITION_TOOL_NAME, self._payload))


# --- extraction is constrained, not requested -------------------------------


def test_the_tool_schema_is_derived_from_the_result_model() -> None:
    """One definition, so the constraint cannot disagree with the type."""
    definition = recognition.recognition_tool_definition()
    assert definition["name"] == recognition.RECOGNITION_TOOL_NAME
    assert set(definition["input_schema"]["properties"]) == set(FaultRecognitionResult.model_fields)


def test_the_schema_carries_no_refs() -> None:
    """Nested models arrive as `$ref`, which the API resolves ambiguously."""
    rendered = json.dumps(recognition.recognition_tool_definition())
    assert "$ref" not in rendered
    assert "$defs" not in rendered


def test_the_tool_call_is_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Left to choose, a model asked about an unreadable photo answers in prose.

    Prose is what this module exists to avoid parsing.
    """
    client = _FakeClient(_result().model_dump(mode="json"))
    recognition.recognise_fault_display(client, model="m", data=JPEG, image_format=ImageFormat.JPEG)
    request = client.calls[0]
    assert request["tool_choice"] == {
        "type": "tool",
        "name": recognition.RECOGNITION_TOOL_NAME,
    }


def test_the_image_travels_as_its_sniffed_format() -> None:
    """Never as the uploader declared it.

    BE-009 establishes what the bytes are; sending a client-supplied media
    type would hand that decision back to the uploader.
    """
    block = recognition.image_block(JPEG, ImageFormat.PNG)
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == JPEG


def test_the_prompt_forbids_normalising_what_is_read() -> None:
    """A code silently expanded from F001 to F0001 is a different fault."""
    assert "do not normalise" in recognition.SYSTEM_PROMPT.casefold()


def test_the_prompt_says_an_invented_code_is_worse_than_uncertainty() -> None:
    assert "invented code" in recognition.SYSTEM_PROMPT.casefold()


# --- a photo that is not a fault display ------------------------------------


def test_a_non_display_photo_yields_no_fields() -> None:
    """The verdict is checked before any field is read.

    A model that has decided the photo is a wiring diagram and reported a
    code anyway has invented the code.
    """
    result = _result(verdict=DisplayVerdict.NOT_A_FAULT_DISPLAY, fault_code=RecognisedField())
    trusted, unsure = recognition.confirmed_context(result)
    assert trusted == {}
    assert unsure == []


@pytest.mark.parametrize("verdict", [DisplayVerdict.NOT_A_FAULT_DISPLAY, DisplayVerdict.UNREADABLE])
def test_a_rejected_photo_leaks_no_brand_or_model(verdict: DisplayVerdict) -> None:
    """The runtime check is what stops these, not the schema.

    The schema refuses a *fault code* on a rejected photo, because that is the
    dangerous one. It permits a brand and a model — a nameplate photo really
    does show them, and the verdict for a nameplate is "not a fault display".
    So this path is the only thing between a confidently-read brand and a
    caller that treats it as established context for the whole conversation.
    """
    result = _result(
        verdict=verdict,
        fault_code=RecognisedField(),
        brand=RecognisedField(value="ABB", confidence=0.99),
        model=RecognisedField(value="ACS880", confidence=0.99),
    )
    trusted, unsure = recognition.confirmed_context(result)
    assert trusted == {}, "a rejected photo supplied equipment context anyway"
    assert unsure == []


def test_a_rejection_carrying_a_code_is_refused_by_the_schema() -> None:
    """The combination this schema exists to prevent."""
    with pytest.raises(Exception, match="invented"):
        FaultRecognitionResult(
            verdict=DisplayVerdict.NOT_A_FAULT_DISPLAY,
            fault_code=RecognisedField(value="F0001", confidence=0.9),
        )


def test_an_unreadable_photo_is_told_apart_from_a_wrong_one() -> None:
    """The engineer's fix differs: retake it, or photograph something else."""
    unreadable = recognition.rejection_message(
        _result(verdict=DisplayVerdict.UNREADABLE, fault_code=RecognisedField())
    )
    wrong = recognition.rejection_message(
        _result(verdict=DisplayVerdict.NOT_A_FAULT_DISPLAY, fault_code=RecognisedField())
    )
    assert unreadable is not None
    assert wrong is not None
    assert unreadable != wrong
    assert "glare" in unreadable
    assert "type the code in" in wrong


def test_a_usable_photo_has_no_rejection_message() -> None:
    assert recognition.rejection_message(_result()) is None


def test_the_rejection_includes_what_the_model_saw() -> None:
    """Say what was seen, not just that it was wrong.

    "That is not a fault display" alone leaves the engineer guessing at what
    to photograph instead.
    """
    message = recognition.rejection_message(
        _result(
            verdict=DisplayVerdict.NOT_A_FAULT_DISPLAY,
            fault_code=RecognisedField(),
            note="It appears to be a motor nameplate.",
        )
    )
    assert message is not None
    assert "nameplate" in message


# --- the confidence gate -----------------------------------------------------


def test_a_confident_field_is_used() -> None:
    trusted, unsure = recognition.confirmed_context(_result())
    assert trusted == {"fault_code": "F0001"}
    assert unsure == []


def test_a_low_confidence_field_must_be_confirmed() -> None:
    """A guess here sends an engineer to a real procedure for the wrong fault."""
    result = _result(fault_code=RecognisedField(value="F0001", confidence=0.4))
    trusted, unsure = recognition.confirmed_context(result)
    assert trusted == {}
    assert unsure == ["fault_code"]


def test_a_field_at_the_threshold_is_used() -> None:
    """The bar is met, not merely approached."""
    result = _result(
        fault_code=RecognisedField(value="F0001", confidence=recognition.MIN_FIELD_CONFIDENCE)
    )
    trusted, _ = recognition.confirmed_context(result)
    assert trusted == {"fault_code": "F0001"}


def test_fields_are_gated_independently() -> None:
    """A photo often shows the code clearly and no logo at all.

    One number for the whole result would discard a readable code because the
    manufacturer was out of frame, or accept a guessed brand because the code
    was sharp.
    """
    result = _result(
        fault_code=RecognisedField(value="F0001", confidence=0.97),
        brand=RecognisedField(value="ABB", confidence=0.3),
    )
    trusted, unsure = recognition.confirmed_context(result)
    assert trusted == {"fault_code": "F0001"}
    assert unsure == ["brand"]


def test_an_unread_field_is_neither_trusted_nor_queried() -> None:
    """There is nothing to confirm.

    Asking about a field the photo does not show invites the engineer to
    supply it from memory, which is the mistyping this feature removes.
    """
    trusted, unsure = recognition.confirmed_context(_result(brand=RecognisedField()))
    assert "brand" not in trusted
    assert "brand" not in unsure


def test_the_threshold_is_high_on_purpose() -> None:
    """The cost of a wrong code is a wasted call-out; of asking, one tap."""
    assert recognition.MIN_FIELD_CONFIDENCE >= 0.8


# --- malformed reports -------------------------------------------------------


def test_a_report_that_does_not_validate_is_refused() -> None:
    """Never repaired, never partially read.

    Half a report about a fault code is worse than none.
    """
    with pytest.raises(ValidationError, match="could not be validated"):
        recognition.parse_recognition({"verdict": "something else entirely"})


def test_no_tool_call_at_all_is_refused() -> None:
    client = _FakeClient(blocks=[_Block("text")])
    with pytest.raises(ValidationError, match="no report"):
        recognition.recognise_fault_display(
            client, model="m", data=JPEG, image_format=ImageFormat.JPEG
        )


def test_a_call_to_another_tool_is_not_a_report() -> None:
    client = _FakeClient(blocks=[_Block("tool_use", "something_else", {"verdict": "x"})])
    with pytest.raises(ValidationError, match="no report"):
        recognition.recognise_fault_display(
            client, model="m", data=JPEG, image_format=ImageFormat.JPEG
        )


def test_a_valid_report_round_trips() -> None:
    client = _FakeClient(_result().model_dump(mode="json"))
    result = recognition.recognise_fault_display(
        client, model="m", data=JPEG, image_format=ImageFormat.JPEG
    )
    assert result.fault_code.value == "F0001"


# --- the real-photo corpus ---------------------------------------------------
#
# Skipped until the photographs exist. See this module's docstring: the
# acceptance criterion is a data gap, and a synthetic image would answer a
# different question than the one being asked.

_CORPUS_ROOT = Path(os.environ.get("PANELPILOT_PHOTO_CORPUS", "tests/photo-corpus"))
_MANIFEST = _CORPUS_ROOT / "manifest.json"

requires_photo_corpus = pytest.mark.skipif(
    not _MANIFEST.is_file(),
    reason=(
        "needs a real photo corpus: >=20 photographs spanning lighting, angle "
        "and glare, plus >=2 off-topic images, with a manifest.json listing "
        "the expected reading for each. Set PANELPILOT_PHOTO_CORPUS to its "
        "directory."
    ),
)


def _load_manifest() -> list[dict[str, Any]]:
    return list(json.loads(_MANIFEST.read_text(encoding="utf-8")))


@requires_photo_corpus
def test_the_corpus_is_large_and_varied_enough_to_mean_anything() -> None:
    """A corpus of twenty easy photos would certify nothing.

    The criterion asks for variation because that is where recognition
    actually fails; a set that happens to be all clean straight-on shots
    would pass while the product still mistypes codes in the field.
    """
    entries = _load_manifest()
    assert len(entries) >= 20, "the criterion asks for at least 20 photographs"

    off_topic = [e for e in entries if e.get("verdict") != DisplayVerdict.FAULT_DISPLAY.value]
    assert len(off_topic) >= 2, "at least 2 photographs must be deliberately off-topic"

    conditions = {e.get("condition") for e in entries}
    assert len(conditions) >= 3, (
        "the photographs must span lighting, angle and glare — a manifest with "
        "one condition is twenty versions of the same test"
    )


@requires_photo_corpus
def test_every_good_photo_is_read_correctly() -> None:
    """The acceptance criterion, when the data exists to run it."""
    import anthropic

    from app.core.config import get_settings
    from app.domain.images import sniff_format

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    failures: list[str] = []
    for entry in _load_manifest():
        if entry.get("verdict") != DisplayVerdict.FAULT_DISPLAY.value:
            continue
        data = (_CORPUS_ROOT / entry["file"]).read_bytes()
        result = recognition.recognise_fault_display(
            client,
            model=settings.llm_model,
            data=data,
            image_format=sniff_format(data),
        )
        trusted, _ = recognition.confirmed_context(result)
        if trusted.get("fault_code") != entry["fault_code"]:
            failures.append(
                f"{entry['file']} ({entry.get('condition')}): expected "
                f"{entry['fault_code']}, got {trusted.get('fault_code')!r}"
            )

    assert not failures, "misread photographs:\n" + "\n".join(failures)


@requires_photo_corpus
def test_every_off_topic_photo_is_rejected() -> None:
    """The failure mode that matters most.

    A model asked only to extract a code will find something code-shaped in
    almost any image, and a fabricated code sends an engineer to a real
    procedure for a fault they do not have.
    """
    import anthropic

    from app.core.config import get_settings
    from app.domain.images import sniff_format

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    for entry in _load_manifest():
        if entry.get("verdict") == DisplayVerdict.FAULT_DISPLAY.value:
            continue
        data = (_CORPUS_ROOT / entry["file"]).read_bytes()
        result = recognition.recognise_fault_display(
            client,
            model=settings.llm_model,
            data=data,
            image_format=sniff_format(data),
        )
        assert (
            result.verdict is not DisplayVerdict.FAULT_DISPLAY
        ), f"{entry['file']} is not a fault display but was read as one"
        trusted, _ = recognition.confirmed_context(result)
        assert trusted == {}, f"{entry['file']} yielded fields despite being off-topic"
