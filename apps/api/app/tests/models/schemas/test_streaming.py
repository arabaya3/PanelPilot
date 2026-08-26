"""Tests for `app/models/schemas/streaming.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models.schemas.streaming import DiagnosisEvent


def test_an_event_renders_as_an_sse_frame() -> None:
    """The wire format a browser's EventSource actually parses."""
    rendered = DiagnosisEvent(event="retrieving").render()
    assert rendered == "event: retrieving\ndata: {}\n\n"


def test_a_frame_ends_with_a_blank_line() -> None:
    """Without it the client waits for a frame that never terminates."""
    assert DiagnosisEvent(event="result", data={"a": 1}).render().endswith("\n\n")


def test_the_payload_is_serialised_on_one_line() -> None:
    """A raw newline inside `data:` splits the frame in two.

    The client would then parse the first half as a complete event and
    discard the rest — a truncated answer that looks well-formed.
    """
    event = DiagnosisEvent(
        event="result", data={"text": "first line\nsecond line", "nested": {"x": [1, 2]}}
    )
    body = event.render()
    data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
    assert len(data_lines) == 1


def test_the_payload_round_trips() -> None:
    """A client must be able to reconstruct exactly what was sent."""
    payload = {"session_id": "s1", "answered": True, "steps": [{"order": 1}]}
    body = DiagnosisEvent(event="result", data=payload).render()
    data = body.split("data: ", 1)[1].split("\n", 1)[0]
    assert json.loads(data) == payload


def test_progress_events_carry_no_payload_by_default() -> None:
    """They report a stage, never a fragment of the answer."""
    assert DiagnosisEvent(event="generated").data == {}


def test_the_event_vocabulary_is_closed() -> None:
    """An unknown event name reaches a client that has no branch for it."""
    with pytest.raises(ValidationError):
        DiagnosisEvent(event="whatever")


@pytest.mark.parametrize("name", ["retrieving", "generated", "refused", "result"])
def test_every_stage_is_representable(name: str) -> None:
    assert DiagnosisEvent(event=name).event == name
