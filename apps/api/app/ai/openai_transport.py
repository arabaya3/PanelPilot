"""OpenAI behind the request shape every generation path already speaks.

The diagnosis, localisation and photo-recognition paths are written against
the Claude Messages API: a system prompt, content blocks (text and base64
images), one tool, and ``tool_choice`` forcing it. Rather than teach each of
them a second vendor, this translates that request to Chat Completions and the
answer back into the same blocks -- ``tool_use`` with a parsed ``input`` --
so the parsers, validators and guardrails downstream see nothing different.

A transport, not a generation path: it is handed a request that a guarded
caller has already decided to make, and answers it. The architecture test
names it beside the Claude client factory for that reason.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: OpenAI's finish reasons, in the Messages API's words. ``max_tokens`` is the
#: one callers act on: a cut-off tool call can still validate, and a diagnosis
#: that lost its last step must be refused, not shown.
_STOP_REASONS = {
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "stop": "end_turn",
    "content_filter": "refusal",
}


@dataclass(frozen=True)
class Block:
    """One content block, shaped like the Messages API's."""

    type: str
    text: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass(frozen=True)
class Message:
    """A reply, shaped like the Messages API's."""

    content: list[Block] = field(default_factory=list)
    stop_reason: str | None = None


def _system_text(system: Any) -> str:
    """Flatten a system prompt given as text or as text blocks.

    Args:
        system: The Messages API ``system`` argument.

    Returns:
        The prompt as one string.
    """
    if isinstance(system, str):
        return system
    parts = [block.get("text", "") for block in system or [] if isinstance(block, dict)]
    return "\n\n".join(part for part in parts if part)


def _content(content: Any) -> Any:
    """Translate one message's content: text passes, images become data URLs.

    Args:
        content: A string, or a list of Messages API content blocks.

    Returns:
        The Chat Completions equivalent.

    Raises:
        ValueError: For a block kind with no translation.
    """
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif kind == "image":
            source = block["source"]
            url = f"data:{source['media_type']};base64,{source['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ValueError(f"cannot translate a {kind!r} content block for OpenAI")
    return parts


def to_chat_request(**kwargs: Any) -> dict[str, Any]:
    """Translate Messages API arguments into Chat Completions arguments.

    Args:
        **kwargs: What a caller passes to ``messages.create``.

    Returns:
        The equivalent ``chat.completions.create`` arguments.
    """
    messages: list[dict[str, Any]] = []
    system = _system_text(kwargs.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    for message in kwargs["messages"]:
        messages.append({"role": message["role"], "content": _content(message["content"])})

    request: dict[str, Any] = {
        "model": kwargs["model"],
        "messages": messages,
        "max_tokens": kwargs["max_tokens"],
    }
    if "temperature" in kwargs:
        request["temperature"] = kwargs["temperature"]
    tools = kwargs.get("tools")
    if tools:
        request["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
    choice = kwargs.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "tool":
        request["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif isinstance(choice, dict) and choice.get("type") == "any":
        request["tool_choice"] = "required"
    return request


def from_chat_response(response: Any) -> Message:
    """Translate a Chat Completions reply into a Messages API-shaped one.

    Args:
        response: What ``chat.completions.create`` returned.

    Returns:
        The reply as content blocks. A tool call whose arguments are not a
        JSON object becomes a block with no ``input``, which the callers'
        extractors already read as "no answer" -- never as an empty one.
    """
    choice = response.choices[0]
    reply = choice.message
    blocks: list[Block] = []
    if reply.content:
        blocks.append(Block(type="text", text=reply.content))
    for call in reply.tool_calls or []:
        try:
            arguments = json.loads(call.function.arguments)
        except (TypeError, ValueError):
            arguments = None
        blocks.append(
            Block(
                type="tool_use",
                name=call.function.name,
                input=arguments if isinstance(arguments, dict) else None,
            )
        )
    return Message(content=blocks, stop_reason=_STOP_REASONS.get(choice.finish_reason))


class _Messages:
    """The ``messages`` namespace of ``OpenAIMessages``."""

    def __init__(self, client: Any) -> None:
        """Wrap an OpenAI client.

        Args:
            client: What answers ``chat.completions.create``.
        """
        self._client = client

    def create(self, **kwargs: Any) -> Message:
        """Answer a Messages API request through Chat Completions.

        Args:
            **kwargs: The Messages API arguments.

        Returns:
            The reply in the Messages API's shape.
        """
        return from_chat_response(self._client.chat.completions.create(**to_chat_request(**kwargs)))


class OpenAIMessages:
    """An OpenAI client that answers the Messages API's ``create`` call.

    Args:
        client: An ``openai.OpenAI`` client, or a test double with the same
            ``chat.completions.create``.
    """

    def __init__(self, client: Any) -> None:
        """Wrap an OpenAI client.

        Args:
            client: What answers ``chat.completions.create``.
        """
        self.messages = _Messages(client)
