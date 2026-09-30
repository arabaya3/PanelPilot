"""An English search query for a question asked in another language."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.ai.query_translation import QUERY_TOOL_NAME, english_search_query


class _Client:
    def __init__(self, payload: Any = None, *, fails: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self._payload = payload
        self._fails = fails
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if self._fails:
            raise ConnectionError("provider down")
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name=QUERY_TOOL_NAME, input=self._payload)]
        )


QUESTION = "وحدة الفرملة ACS880 كيف أختار مصدر إيقاف الطوارئ؟"


def test_the_question_becomes_the_models_english_query() -> None:
    client = _Client({"query": " ACS880 brake unit emergency stop source selection "})

    query = english_search_query(QUESTION, client=client, model="m")

    assert query == "ACS880 brake unit emergency stop source selection"
    assert client.sent[0]["messages"][0]["content"] == QUESTION
    assert client.sent[0]["tool_choice"] == {"type": "tool", "name": QUERY_TOOL_NAME}


def test_a_failed_translation_searches_the_question_as_asked() -> None:
    """A worse search, never a failed turn."""
    assert english_search_query(QUESTION, client=_Client(fails=True), model="m") == QUESTION
    assert english_search_query(QUESTION, client=_Client(None), model="m") == QUESTION
    assert english_search_query(QUESTION, client=_Client({"query": "  "}), model="m") == QUESTION
