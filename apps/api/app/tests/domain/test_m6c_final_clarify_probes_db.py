"""Review probes: what a clarification may clear (integrated M6c head, Postgres).

A life-safety mention whose equipment the model did not return as an
unsupported item (it omitted it, asked about it, or proposed it as a plain
load the context check missed) leaves no safety concern, so one eligible
member can clarify the mention away and its [OPEN] line disappears.
Documented in §6.10 as a person's judgement; the strict xfail states the
stricter rule the review recommends (only possibly-safety mentions are
clarifiable). Synthetic data only.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    pytestmark,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    StubProvider,
    _app_fixture,
    _as_fixture,
    _provider_fixture,
    _who_fixture,
    span,
)
from app.tests.domain.test_m6c_safety_clarification_db import _clarify, _mentions, _record, _saved

TEXT = "Main board MDB. Pump 15 kW on MDB. Lighting 2 kW. Also, it serves the sprinkler system."


def _items(kind: str) -> list[dict[str, Any]]:
    board = {
        "id": "b1",
        "kind": "board",
        "source": span(TEXT, "Main board MDB"),
        "value": {"name": "MDB"},
    }
    if kind == "omitted":
        return [board]
    if kind == "question":
        return [
            board,
            {
                "id": "q1",
                "kind": "question",
                "source": span(TEXT, "it serves the sprinkler system"),
                "value": {"text": "Is the sprinkler pump by others?"},
            },
        ]
    return [
        board,
        {
            "id": "l1",
            "kind": "load",
            "source": span(TEXT, "Pump 15 kW on MDB"),
            "value": {"board": "MDB", "description": "Pump", "kind": "motor", "power_kw": 15},
        },
    ]


@pytest.mark.parametrize("kind", ["omitted", "question", "missed_as_load"])
def test_a_life_safety_mention_cannot_be_clarified_away(
    engine: Engine, as_: Any, provider: StubProvider, kind: str
) -> None:
    pid, proposal = _saved(as_, provider, description=TEXT, items=_items(kind))
    assert not any(i["kind"] == "unsupported" for i in proposal["items"])
    (mention,) = _mentions(_record(engine, pid))
    assert mention["code"] == "ai_life_safety_mentioned"
    done = _clarify(
        as_("reviewer"), pid, 1, mention["subject"]["mention_id"], "Sprinklers are by others."
    )
    assert done.status_code == 409, done.text[:300]
    assert done.json()["code"] == "mention_is_life_safety"
    # It stays open, and the exports keep printing it.
    assert [m["code"] for m in _mentions(_record(engine, pid))] == ["ai_life_safety_mentioned"]
