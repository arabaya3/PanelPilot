"""The OpenAI transport: the Claude request shape in, the Claude reply shape out."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.openai_transport import OpenAIMessages, from_chat_response, to_chat_request
from app.ai.structured_output import extract_named_tool_payload

TOOL = {
    "name": "emit_diagnosis",
    "description": "Return the diagnosis.",
    "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
}


def _reply(
    *, arguments: str | None = None, text: str | None = None, finish: str = "tool_calls"
) -> Any:
    calls = (
        [SimpleNamespace(function=SimpleNamespace(name="emit_diagnosis", arguments=arguments))]
        if arguments is not None
        else None
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=text, tool_calls=calls),
            )
        ]
    )


def test_a_forced_tool_request_translates_whole() -> None:
    request = to_chat_request(
        model="gpt-4o-mini",
        max_tokens=512,
        system="You are careful.",
        messages=[{"role": "user", "content": "F0001?"}],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "emit_diagnosis"},
    )

    assert request == {
        "model": "gpt-4o-mini",
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": "You are careful."},
            {"role": "user", "content": "F0001?"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "emit_diagnosis",
                    "description": "Return the diagnosis.",
                    "parameters": TOOL["input_schema"],
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "emit_diagnosis"}},
    }


def test_a_system_prompt_in_blocks_is_joined() -> None:
    request = to_chat_request(
        model="m",
        max_tokens=1,
        system=[{"type": "text", "text": "One."}, {"type": "text", "text": "Two."}],
        messages=[{"role": "user", "content": "q"}],
    )
    assert request["messages"][0] == {"role": "system", "content": "One.\n\nTwo."}
    assert "tools" not in request


def test_an_image_becomes_a_data_url() -> None:
    """The photo-recognition path sends base64 image blocks."""
    request = to_chat_request(
        model="m",
        max_tokens=1,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
                    },
                    {"type": "text", "text": "Report what this photograph shows."},
                ],
            }
        ],
    )
    assert request["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
        {"type": "text", "text": "Report what this photograph shows."},
    ]


def test_an_untranslatable_block_is_refused_not_dropped() -> None:
    with pytest.raises(ValueError, match="document"):
        to_chat_request(
            model="m",
            max_tokens=1,
            messages=[{"role": "user", "content": [{"type": "document"}]}],
        )


def test_a_tool_call_comes_back_as_a_tool_use_block() -> None:
    message = from_chat_response(_reply(arguments=json.dumps({"summary": "Undervoltage."})))

    assert extract_named_tool_payload(message, "emit_diagnosis") == {"summary": "Undervoltage."}
    assert message.stop_reason == "tool_use"


def test_a_cut_off_answer_reads_as_max_tokens() -> None:
    """The diagnosis path refuses on this; a truncated call can still validate."""
    message = from_chat_response(_reply(arguments='{"summary": "Under', finish="length"))
    assert message.stop_reason == "max_tokens"


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", "null"])
def test_unusable_arguments_are_no_answer_rather_than_an_empty_one(arguments: str) -> None:
    message = from_chat_response(_reply(arguments=arguments))
    assert extract_named_tool_payload(message, "emit_diagnosis") is None


def test_prose_instead_of_a_call_is_no_answer() -> None:
    message = from_chat_response(_reply(text="I think it is the motor.", finish="stop"))
    assert extract_named_tool_payload(message, "emit_diagnosis") is None
    assert message.content[0].text == "I think it is the motor."


def test_the_wrapper_sends_the_translation_and_returns_the_reply() -> None:
    sent: list[dict[str, Any]] = []

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            sent.append(kwargs)
            return _reply(arguments='{"summary": "ok"}')

    client = OpenAIMessages(SimpleNamespace(chat=SimpleNamespace(completions=_Completions())))
    message = client.messages.create(
        model="gpt-4o-mini",
        max_tokens=10,
        messages=[{"role": "user", "content": "q"}],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "emit_diagnosis"},
    )

    assert sent[0]["tool_choice"] == {"type": "function", "function": {"name": "emit_diagnosis"}}
    assert extract_named_tool_payload(message, "emit_diagnosis") == {"summary": "ok"}
