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


# --- choosing the provider --------------------------------------------------------


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _settings(provider: str, *, anthropic: str | None = None, openai: str | None = None) -> Any:
    class _Settings:
        llm_provider = provider
        anthropic_api_key = _Secret(anthropic) if anthropic else None
        openai_api_key = _Secret(openai) if openai else None

    return _Settings


def test_openai_answers_behind_the_same_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.openai_transport import OpenAIMessages

    monkeypatch.setattr(anthropic_client, "get_settings", _settings("openai", openai="sk-o"))
    anthropic_client._openai_client.cache_clear()
    try:
        client = anthropic_client.get_llm_client()
        assert isinstance(client, OpenAIMessages)
        assert client is anthropic_client.get_llm_client()
        inner: Any = client.messages._client
        assert inner.timeout.read == anthropic_client.LLM_READ_TIMEOUT_S
        assert inner.max_retries == anthropic_client.LLM_MAX_RETRIES
    finally:
        anthropic_client._openai_client.cache_clear()


def test_claude_is_the_default_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic_client, "get_settings", _settings("anthropic", anthropic="sk"))
    assert anthropic_client.get_llm_client() is anthropic_client.get_anthropic_client()


@pytest.mark.parametrize(
    ("provider", "names"),
    [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")],
)
def test_the_chosen_provider_without_its_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, provider: str, names: str
) -> None:
    """Never quietly answered by the other vendor."""
    from app.core.config import ConfigurationError

    monkeypatch.setattr(
        anthropic_client,
        "get_settings",
        _settings(provider, anthropic="sk" if provider == "openai" else None),
    )
    anthropic_client._openai_client.cache_clear()
    with pytest.raises(ConfigurationError, match=names):
        anthropic_client.get_llm_client()
