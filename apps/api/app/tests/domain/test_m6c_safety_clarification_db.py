"""M6c: clarifying a (possibly) life-safety mention as not life-safety (``panelpilot_test_*`` only).

A clarification is four-eyes (a member holding ``approve_revision`` who did
not create the AI proposal), append-only on the record's ``ai`` block under
``requirements_sha256``, and never makes an item a supported load or clears
anything while the proposal holds a life-safety item or an undecided
possibly-safety item. Synthetic data only. Uses the M6b adversarial fixtures.
"""

# ruff: noqa: RUF001  (Arabic/Hebrew and dash-variant probe text)
from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    error,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    AI,
    CONTROL,
    StubProvider,
    _app_fixture,
    _approve,
    _As,
    _as_fixture,
    _decide,
    _open,
    _payload,
    _propose,
    _provider_fixture,
    _save,
    _who_fixture,
    request_for,
    span,
)

#: "Emergency" alone is a possibly-safety word: one mention, no item.
POSSIBLY = "Main board MDB. Office lighting 1 kW on MDB. Emergency contact on the door label."
#: "Fire main" is a life-safety term: one mention.
DEFINITE = "Main board MDB. Office lighting 1 kW on MDB. The fire main is by others."


def _saved(
    as_: Any,
    provider: StubProvider,
    description: str = POSSIBLY,
    items: list[dict[str, Any]] | None = None,
    creator: str = "engineer",
    decide: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """A project whose revision 1 applies a proposal for ``description`` with no placements."""
    provider.payload = _payload(items or [])
    created = _propose(as_(creator), description)
    assert created.status_code == 201, created.text[:400]
    proposal = created.json()
    for item_id, decision in (decide or {}).items():
        assert (
            _decide(as_(creator), proposal["id"], item_id, {"decision": decision}).status_code
            == 200
        )
    saved = _save(
        as_(creator),
        request_for(),
        {
            "intake": "ai",
            "control": CONTROL,
            "ai": {"proposal_id": proposal["id"], "placements": []},
        },
    )
    assert saved.status_code == 201, saved.text[:400]
    return str(saved.json()["id"]), proposal


def _record(engine: Engine, pid: str, n: int = 1) -> dict[str, Any]:
    row = revision_row(engine, pid, n)
    assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])
    record: dict[str, Any] = row["requirements"]
    return record


def _mentions(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [o for o in record["open_items"] if o["subject"].get("mention_id")]


def _clarify(
    c: _As,
    pid: str,
    n: int,
    mention_id: str,
    reason: str = "A label text, not equipment.",
    **extra: Any,
) -> Any:
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/requirements/safety-clarifications",
        json={"mention_id": mention_id, "statement": "not_life_safety", "reason": reason, **extra},
    )


def _refused(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text[:400]
    assert error(response).get("code") == code, response.text[:400]


def _code(response: Any) -> str:
    return str(error(response).get("code"))


# --------------------------------------------------------------------------- #
# Detection reaches the record: one open item per mention
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("description", "code", "term"),
    [
        (POSSIBLY, "ai_possibly_life_safety", "emergency"),
        (DEFINITE, "ai_life_safety_mentioned", "fire main"),
        ("Main board MDB. Duty/standby pumps on MDB.", "ai_possibly_life_safety", "standby"),
        ("Main board MDB. Fire‐fighting lift on MDB.", "ai_life_safety_mentioned", "fire fighting"),
        (
            "Main board MDB. Stair pressurisation fans on MDB.",
            "ai_life_safety_mentioned",
            "stair pressur",
        ),
        ("Main board MDB. مضخة حريق on MDB.", "ai_life_safety_mentioned", None),
        ("Main board MDB. תאורת חירום on MDB.", "ai_life_safety_mentioned", None),
    ],
    ids=["emergency", "fire_main", "duty_standby", "dash_variant", "stair", "arabic", "hebrew"],
)
def test_each_mention_is_an_open_item(
    engine: Engine, as_: Any, provider: StubProvider, description: str, code: str, term: str | None
) -> None:
    pid, proposal = _saved(as_, provider, description)
    found = _mentions(_record(engine, pid))
    assert [o["code"] for o in found] == [code]
    if term is not None:
        assert found[0]["subject"]["term"] == term
    assert {m["mention_id"] for m in proposal["safety_mentions"]} == {
        o["subject"]["mention_id"] for o in found
    }


@pytest.mark.parametrize(
    ("description", "phrase", "reason"),
    [
        (
            "Main board MDB. Standby generator feeder 5.5 kW on MDB.",
            "Standby generator feeder 5.5 kW",
            "possibly_life_safety",
        ),
        ("Main board MDB. Duty pump 5.5 kW on MDB. Alarm panel too.", "Duty pump 5.5 kW", None),
        (
            "Main board MDB. Critical cooling fan 5.5 kW on MDB.",
            "Critical cooling fan 5.5 kW",
            "possibly_life_safety",
        ),
        (
            "Main board MDB. Sprinkler booster 5.5 kW on MDB.",
            "Sprinkler booster 5.5 kW",
            "fire-pump or life-safety equipment is out of scope",
        ),
        (
            "Main board MDB. Pump 5.5 kW on MDB. Lighting 1 kW. Both serve the sprinklers.",
            "Pump 5.5 kW",
            "fire-pump or life-safety equipment is out of scope",
        ),
        (
            "Main board MDB. Pump 5.5 kW on MDB. Fan 1 kW. The above pump is a jockey.",
            "Pump 5.5 kW",
            "fire-pump or life-safety equipment is out of scope",
        ),
    ],
    ids=[
        "standby",
        "alarm_other_sentence_not_followed",
        "critical",
        "sprinkler",
        "both",
        "the_above",
    ],
)
def test_an_ambiguous_or_safety_load_is_never_supported(
    as_: Any, provider: StubProvider, description: str, phrase: str, reason: str | None
) -> None:
    provider.payload = _payload(
        [
            {
                "id": "l1",
                "kind": "load",
                "source": span(description, phrase),
                "value": {
                    "board": "MDB",
                    "description": "Load",
                    "kind": "motor",
                    "power_kw": "5.5",
                },
            }
        ]
    )
    created = _propose(as_(), description, request={"boards": [{"name": "MDB"}]})
    assert created.status_code == 201, created.text[:400]
    (item,) = created.json()["items"]
    if reason is None:
        # The ambiguous word is in another sentence with no reference: the
        # load stays a load, and the record-level net still raises it.
        assert item["kind"] == "load"
        assert any(m["level"] == "possibly" for m in created.json()["safety_mentions"])
    else:
        assert item["kind"] == "unsupported"
        assert item["value"]["reason"] == reason


def test_an_unclarified_mention_prints_open(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    from app.design import requirements

    pid, _ = _saved(as_, provider, DEFINITE)
    lines = requirements.open_item_lines(_record(engine, pid))
    assert (
        "[OPEN] The description mentions fire-pump or life-safety equipment ('fire main'): "
        "out of scope and not designed; check that no placed load is that equipment"
    ) in lines


def test_a_placement_of_a_possibly_item_is_refused(as_: Any, provider: StubProvider) -> None:
    description = "Main board MDB. Emergency pump 5.5 kW on MDB."
    provider.payload = _payload(
        [
            {
                "id": "b",
                "kind": "board",
                "source": span(description, "Main board MDB"),
                "value": {"name": "MDB"},
            },
            {
                "id": "l1",
                "kind": "load",
                "source": span(description, "Emergency pump 5.5 kW"),
                "value": {
                    "board": "MDB",
                    "description": "Pump",
                    "kind": "motor",
                    "power_kw": "5.5",
                },
            },
        ]
    )
    created = _propose(as_(), description)
    pid = created.json()["id"]
    for item in ("i1", "i2"):
        assert _decide(as_(), pid, item, {"decision": "accept"}).status_code == 200
    placed = _save(
        as_(),
        request_for(),
        {
            "intake": "ai",
            "control": CONTROL,
            "ai": {
                "proposal_id": pid,
                "placements": [
                    {"item_id": "i1", "board_index": 0},
                    {"item_id": "i2", "board_index": 0, "load_index": 0},
                ],
            },
        },
    )
    _refused(placed, 422, "ai_placement_invalid")


# --------------------------------------------------------------------------- #
# M6e: the route is retired; stored clarifications keep counting and printing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("caller", ["reviewer", "owner", "engineer", "trial_id"])
def test_the_clarification_route_is_retired(
    engine: Engine, as_: Any, provider: StubProvider, caller: str
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    before = _record(engine, pid)
    _refused(_clarify(as_(caller), pid, 1, mid), 410, "clarification_route_retired")
    assert _record(engine, pid) == before


def test_a_stored_clarification_still_counts_and_prints(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    record["ai"]["clarifications"] = [
        {
            "mention_id": mid,
            "statement": "not_life_safety",
            "reason": "The door label lists a phone number.",
            "by_user_id": "00000000-0000-0000-0000-000000000000",
            "by_name": "Earlier Reviewer",
            "at": "2026-11-04T10:00:00+00:00",
        }
    ]
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    approved = _approve(as_("reviewer"), pid, 1)
    assert approved.status_code == 200, approved.text[:400]
    exported = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/1/export", json={"format": "json"}
    )
    assert exported.status_code == 200, exported.text[:400]
    (line,) = json.loads(exported.content)["approval"]["false_positives"]
    assert line.startswith("[FALSE POSITIVE] 'emergency' judged not a life-safety requirement")
