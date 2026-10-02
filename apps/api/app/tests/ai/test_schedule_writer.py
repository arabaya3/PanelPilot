"""Tests for `app/ai/schedule_writer.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai import schedule_writer
from app.core.errors import ServiceUnavailableError, ValidationError
from app.models.schemas.design import LoadKind, ScheduleSuggestionRequest

REQUEST = ScheduleSuggestionRequest(description="Hall: 20 sockets, 30 lights, two ACs")

GOOD = {
    "items": [
        {
            "description": "Hall sockets",
            "load": "socket",
            "quantity": 20,
            "unit_power_kw": 0.15,
            "three_phase": False,
            "power_stated": True,
        }
    ],
    "assumptions": ["No diversity applied."],
}


class _Client:
    def __init__(self, payload: Any, stop: str = "tool_use", fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self._fail = fail
        self._reply = SimpleNamespace(
            stop_reason=stop,
            content=[
                SimpleNamespace(type="tool_use", name=schedule_writer.TOOL_NAME, input=payload)
            ],
        )
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        if self._fail:
            raise RuntimeError("provider down")
        self.sent.append(kwargs)
        return self._reply


@pytest.fixture(autouse=True)
def _model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        schedule_writer, "get_settings", lambda: SimpleNamespace(generation_model="test-model")
    )


def test_the_schedule_is_asked_for_by_a_forced_tool() -> None:
    client = _Client(GOOD)
    output = schedule_writer.write_schedule(REQUEST, client=client)
    assert output.items[0].load is LoadKind.SOCKET
    assert output.items[0].quantity == 20
    sent = client.sent[0]
    assert sent["tool_choice"] == {"type": "tool", "name": schedule_writer.TOOL_NAME}
    assert "three-phase" in sent["messages"][0]["content"]
    assert "Write descriptions and assumptions in English" in sent["messages"][0]["content"]
    assert REQUEST.description in sent["messages"][0]["content"]
    schema = sent["tools"][0]["input_schema"]
    assert "$defs" not in json.dumps(schema)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"items": [], "assumptions": []},
        {"items": [{**GOOD["items"][0], "load": "toaster"}], "assumptions": []},  # type: ignore[index]
        {"items": [{**GOOD["items"][0], "quantity": 0}], "assumptions": []},  # type: ignore[index]
    ],
)
def test_an_unusable_schedule_is_refused(payload: Any) -> None:
    with pytest.raises(ValidationError):
        schedule_writer.write_schedule(REQUEST, client=_Client(payload))


def test_a_cut_off_schedule_is_refused() -> None:
    with pytest.raises(ValidationError, match="cut off"):
        schedule_writer.write_schedule(REQUEST, client=_Client(GOOD, stop="max_tokens"))


def test_an_unreachable_model_is_a_service_error() -> None:
    with pytest.raises(ServiceUnavailableError):
        schedule_writer.write_schedule(REQUEST, client=_Client(GOOD, fail=True))


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("قاعة فيها ٢٠ مأخذ و٣٠ إنارة LED", "Arabic"),
        ("Hall with 20 sockets", "English"),
        ("Hall: 20 مأخذ", "English"),
    ],
)
def test_description_language(text: str, language: str) -> None:
    assert schedule_writer.description_language(text) == language


def test_an_arabic_description_is_answered_in_arabic() -> None:
    client = _Client(GOOD)
    schedule_writer.write_schedule(
        ScheduleSuggestionRequest(description="قاعة فيها ٢٠ مأخذ"), client=client
    )
    assert "in Arabic" in client.sent[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("text", "stated"),
    [
        ("Hall: 20 sockets, two ACs", False),
        ("Hall: two 2-ton split units", True),
        ("Pump 7.5 kW", True),
        ("Fan 3 HP", True),
        ("مكيفين ٢ طن", True),
        ("سخان 3 كيلو", True),
        ("قاعة فيها ٢٠ مأخذ و٣٠ إنارة", False),
    ],
)
def test_states_power(text: str, stated: bool) -> None:
    assert schedule_writer.states_power(text) is stated


def test_a_power_the_description_never_gave_is_not_called_given() -> None:
    output = schedule_writer.write_schedule(REQUEST, client=_Client(GOOD))
    assert output.items[0].power_stated is False
    stated = ScheduleSuggestionRequest(description="Hall: 20 sockets at 200 W")
    output = schedule_writer.write_schedule(stated, client=_Client(GOOD))
    assert output.items[0].power_stated is True
