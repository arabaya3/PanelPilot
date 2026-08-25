"""Tests for `app/ai/tools/registry.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.ai.tools import registry


def test_tool_spec_is_frozen() -> None:
    """The registry is read at request time; a mutable spec is a shared-state bug."""
    spec = registry.ToolSpec(
        name="cable_sizing",
        description="Size a feeder cable.",
        input_schema={"type": "object"},
        handler=lambda: None,
    )
    assert dataclasses.is_dataclass(spec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "something_else"  # type: ignore[misc]


def test_get_registry_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        registry.get_registry()


def test_to_api_tools_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        registry.to_api_tools()
