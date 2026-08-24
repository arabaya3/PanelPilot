"""Tool registry exposed to the model.

Adding a calc tool is two steps: write the pure function in its own module, and
register it here. Nothing else in the codebase enumerates tools, so the model's
view of what it can call cannot drift from what exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """A calc tool as offered to the model.

    Attributes:
        name: Stable identifier used in tool-call payloads.
        description: One-line description shown to the model.
        input_schema: JSON Schema for the tool's arguments.
        handler: The pure function implementing the tool.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]


def get_registry() -> dict[str, ToolSpec]:
    """Return every registered calc tool, keyed by name.

    Returns:
        The tool registry.
    """
    raise NotImplementedError


def to_api_tools() -> list[dict[str, Any]]:
    """Render the registry as tool definitions for the Claude API.

    Returns:
        Tool definitions in the shape the Messages API expects.
    """
    raise NotImplementedError
