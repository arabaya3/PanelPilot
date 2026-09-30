"""The PLC writer: a forced tool call in, a program or rungs out, nothing judged."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.plc import writer
from app.ai.plc.generation import GenerationError
from app.models.schemas.plc import PlcDialect, PlcGenerationRequest, PlcLanguage

REQUEST = PlcGenerationRequest(
    description="Start/stop a conveyor motor with a seal-in.", dialect=PlcDialect.SIEMENS_SCL
)


class _Client:
    def __init__(self, *, name: str, payload: Any, stop: str = "tool_use") -> None:
        self.sent: list[dict[str, Any]] = []
        self._reply = SimpleNamespace(
            stop_reason=stop,
            content=[SimpleNamespace(type="tool_use", name=name, input=payload)],
        )
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return self._reply


@pytest.fixture(autouse=True)
def _model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        writer, "get_settings", lambda: SimpleNamespace(generation_model="test-model")
    )


def test_structured_text_is_asked_for_by_a_forced_tool_naming_the_dialect() -> None:
    client = _Client(name=writer.SOURCE_TOOL_NAME, payload={"source": "PROGRAM P END_PROGRAM"})

    assert writer.write_source(REQUEST, client=client) == "PROGRAM P END_PROGRAM"
    sent = client.sent[0]
    assert sent["tool_choice"] == {"type": "tool", "name": writer.SOURCE_TOOL_NAME}
    assert sent["model"] == "test-model"
    assert "Siemens SCL" in sent["messages"][0]["content"]
    assert REQUEST.description in sent["messages"][0]["content"]


@pytest.mark.parametrize("payload", [None, {}, {"source": "   "}, {"source": 7}])
def test_no_usable_program_is_a_refusal(payload: Any) -> None:
    client = _Client(name=writer.SOURCE_TOOL_NAME, payload=payload)
    with pytest.raises(GenerationError):
        writer.write_source(REQUEST, client=client)


def test_a_cut_off_program_is_refused_rather_than_returned() -> None:
    client = _Client(
        name=writer.SOURCE_TOOL_NAME, payload={"source": "PROGRAM P"}, stop="max_tokens"
    )
    with pytest.raises(GenerationError, match="cut off"):
        writer.write_source(REQUEST, client=client)


def test_ladder_comes_back_as_validated_rungs() -> None:
    rungs = [
        {
            "comment": "Seal-in",
            "elements": [
                {"paths": [[{"tag": "Start", "kind": "no"}], [{"tag": "Motor", "kind": "no"}]]},
                {"tag": "Stop", "kind": "nc"},
            ],
            "output": {"tag": "Motor", "kind": "coil"},
        }
    ]
    client = _Client(name=writer.LADDER_TOOL_NAME, payload={"rungs": rungs})

    result = writer.write_ladder(
        REQUEST.model_copy(update={"language": PlcLanguage.LADDER}), client=client
    )

    assert result[0].output.tag == "Motor"
    assert client.sent[0]["tools"][0]["input_schema"]["required"] == ["rungs"]


@pytest.mark.parametrize("payload", [{"rungs": []}, {"rungs": [{"comment": "no output"}]}, {}])
def test_rungs_that_do_not_fit_are_a_refusal(payload: Any) -> None:
    client = _Client(name=writer.LADDER_TOOL_NAME, payload=payload)
    with pytest.raises(GenerationError):
        writer.write_ladder(REQUEST, client=client)


def test_the_ladder_schema_resolves_every_reference_from_its_root() -> None:
    """Nested `$defs` with root `$ref`s: found live, the model invented a shape."""
    import json

    schema = writer._ladder_tool()["input_schema"]
    rendered = json.dumps(schema)
    for ref in {part.split('"')[0] for part in rendered.split('"$ref": "')[1:]}:
        assert ref.startswith("#/$defs/")
        assert ref.removeprefix("#/$defs/") in schema["$defs"], ref
