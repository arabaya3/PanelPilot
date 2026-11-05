"""A deterministic stand-in for the AI intake provider, for tests only.

It lives under ``app.tests`` and nowhere else: production builds only
``LlmProposalProvider`` (``app.domain.design_ai.get_proposal_provider``), so
no configuration can reach this stub. Tests install it by overriding that
FastAPI dependency.
"""

from __future__ import annotations

from typing import Any

from app.ai import intake_proposer
from app.design.ai_intake import PROMPT_VERSION


def span(text: str, part: str, nth: int = 0) -> dict[str, int]:
    """The ``source`` of ``part`` in ``text`` (its ``nth`` occurrence)."""
    start = -1
    for _ in range(nth + 1):
        start = text.index(part, start + 1)
    return {"start": start, "end": start + len(part)}


def item(text: str, part: str, item_id: str, item_kind: str, /, **value: Any) -> dict[str, Any]:
    """One output item reading ``part`` of ``text``."""
    return {"id": item_id, "kind": item_kind, "source": span(text, part), "value": value}


class StubProvider:
    """Answers every call with ``payload``, or raises ``raises``; records what it was asked."""

    def __init__(self, payload: Any = None, raises: Exception | None = None) -> None:
        """Hold the canned answer."""
        self.payload = payload
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def propose(self, *, description: str, boards: list[str]) -> intake_proposer.ProviderAnswer:
        """Return the canned payload."""
        self.calls.append({"description": description, "boards": boards})
        if self.raises is not None:
            raise self.raises
        return intake_proposer.ProviderAnswer(
            payload=self.payload, provider="stub", model="stub-model", prompt_version=PROMPT_VERSION
        )


#: A complete synthetic description, and the output a careful model gives.
TEXT = (
    "Main board MDB. Sub-board DB-1 fed from MDB. "
    "Lighting on MDB 1.2 kW single-phase. "
    "On DB-1 a pump 5.5 kW three-phase, switched by the PLC, needs start/stop. "
    "Contactor Schneider LC1D18 for the pump. "
    "Feeder length to DB-1 unknown."
)


def complete_payload() -> dict[str, Any]:
    """The output for :data:`TEXT`."""
    t = TEXT
    return {
        "items": [
            item(t, "Main board MDB", "b1", "board", name="MDB"),
            item(t, "Sub-board DB-1 fed from MDB", "b2", "board", name="DB-1", fed_from="MDB"),
            item(
                t,
                "Lighting on MDB 1.2 kW single-phase",
                "l1",
                "load",
                board="MDB",
                description="Lighting",
                kind="lighting",
                power_kw=1.2,
                phases=1,
            ),
            item(
                t,
                "On DB-1 a pump 5.5 kW three-phase, switched by the PLC",
                "l2",
                "load",
                board="DB-1",
                description="Pump",
                kind="motor",
                power_kw=5.5,
                phases=3,
                controlled=True,
            ),
            item(t, "needs start/stop", "c1", "control_need", load="l2", need="start_stop"),
            item(
                t,
                "Contactor Schneider LC1D18 for the pump",
                "u1",
                "unsupported",
                text="Contactor Schneider LC1D18",
                reason="a product number",
            ),
            item(
                t,
                "Feeder length to DB-1 unknown",
                "q1",
                "question",
                text="What is the feeder length to DB-1?",
                about="b2",
                reason="missing",
            ),
        ]
    }


#: The request a user saves after accepting every item of :func:`complete_payload`.
SAVED_REQUEST: dict[str, Any] = {
    "info": {"name": "M6b AI", "number": "M6B-1"},
    "boards": [
        {
            "name": "MDB",
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {"description": "Lighting", "load": "lighting", "power_kw": "1.2", "phases": 1}
            ],
        },
        {
            "name": "DB-1",
            "fed_from": "MDB",
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {
                    "description": "Pump",
                    "load": "motor",
                    "power_kw": "5.5",
                    "phases": 3,
                    "starter": "dol",
                    "controlled": True,
                }
            ],
        },
    ],
}


def placements_by_item(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where :data:`SAVED_REQUEST` holds each placeable item of the complete output."""
    where = {
        ("board", "MDB"): (0, None),
        ("board", "DB-1"): (1, None),
        ("load", "Lighting"): (0, 0),
        ("load", "Pump"): (1, 0),
        ("control_need", "start_stop"): (1, 0),
    }
    out = []
    for found in items:
        key_value = (
            found["value"].get("name")
            or found["value"].get("description")
            or found["value"].get("need")
        )
        place = where.get((found["kind"], key_value))
        if place is not None:
            out.append({"item_id": found["id"], "board_index": place[0], "load_index": place[1]})
    return out
