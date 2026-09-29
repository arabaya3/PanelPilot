"""The shared Claude client: built once, with bounded waits."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai import anthropic_client


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    class _Key:
        def get_secret_value(self) -> str:
            return "sk-test"

    class _Settings:
        anthropic_api_key = _Key()

    monkeypatch.setattr(anthropic_client, "get_settings", _Settings)
    anthropic_client.get_anthropic_client.cache_clear()
    yield
    anthropic_client.get_anthropic_client.cache_clear()


def test_one_client_is_shared() -> None:
    """Built per request, every question paid a new pool and TLS handshake."""
    assert anthropic_client.get_anthropic_client() is anthropic_client.get_anthropic_client()


def test_the_wait_is_bounded() -> None:
    """The SDK default is a 600 s read with two retries — half an hour per call."""
    client: Any = anthropic_client.get_anthropic_client()
    assert client.timeout.read == anthropic_client.LLM_READ_TIMEOUT_S
    assert client.timeout.connect == anthropic_client.LLM_CONNECT_TIMEOUT_S
    assert client.max_retries == anthropic_client.LLM_MAX_RETRIES
    assert anthropic_client.LLM_READ_TIMEOUT_S * (anthropic_client.LLM_MAX_RETRIES + 1) <= 300
