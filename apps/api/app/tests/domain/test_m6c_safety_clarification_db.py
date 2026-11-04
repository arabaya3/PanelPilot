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
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
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
from app.tests.domain.test_m6b_acknowledgement_concurrency_db import _project_held, _waiting
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


# --------------------------------------------------------------------------- #
# The clarification
# --------------------------------------------------------------------------- #


def test_a_reviewer_clarifies_a_mention_and_exports_print_it(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    (mention,) = _mentions(_record(engine, pid))
    mid = mention["subject"]["mention_id"]
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["safety_clarification"] == {"eligible": True, "reason": None}
    done = _clarify(as_("reviewer"), pid, 1, mid, "  The door label lists a phone number.  ")
    assert done.status_code == 200, done.text[:400]
    stored = _record(engine, pid)
    assert _mentions(stored) == []
    (entry,) = stored["ai"]["clarifications"]
    assert entry["mention_id"] == mid
    assert entry["statement"] == "not_life_safety"
    assert entry["reason"] == "The door label lists a phone number."
    assert all(entry[key] for key in ("by_user_id", "by_name", "at"))
    assert done.json()["requirements"]["ai"]["clarifications"][0]["mention_id"] == mid
    # Twice: no longer an open mention.
    again = _clarify(as_("owner"), pid, 1, mid)
    _refused(again, 404, "mention_not_found")
    # The approval binds the clarified record.
    approved = _approve(as_("owner"), pid, 1)
    assert approved.status_code == 200, approved.text[:400]
    assert revision_row(engine, pid, 1)["approved_requirements_sha256"] == snapshot.sha256_of(
        stored
    )
    # And the review exports print it (as they name an account: by display
    # name, else "a team member"), never as validated.
    from app.design import requirements

    lines = requirements.open_item_lines(stored) + requirements.acknowledged_lines(
        stored, name_of=lambda _id: "a team member"
    )
    # M6d: a clarification in force prints as a false positive.
    clarified = [line for line in lines if line.startswith("[FALSE POSITIVE]")]
    assert clarified == [
        f"[FALSE POSITIVE] 'emergency' judged not a life-safety requirement by a team member on "
        f"{entry['at'][:10]}: The door label lists a phone number. — not engineer-validated"
    ]
    assert not any("ai_possibly" in line or "may refer" in line for line in lines)


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


@pytest.mark.parametrize(
    ("caller", "status", "code", "flag"),
    [
        ("engineer", 403, "clarification_not_permitted", "not_permitted"),
        ("ingestion", 403, "clarification_not_permitted", "not_permitted"),
        ("trial_id", 403, "clarification_requires_account", None),
        ("other_tenant", 404, "revision_not_found", None),
    ],
)
def test_who_may_not_clarify(
    engine: Engine,
    as_: Any,
    provider: StubProvider,
    caller: str,
    status: int,
    code: str,
    flag: str | None,
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    if flag is not None:
        assert _open(as_(caller), pid, 1)["safety_clarification"] == {
            "eligible": False,
            "reason": flag,
        }
    refused = _clarify(as_(caller), pid, 1, mid)
    assert refused.status_code == status, refused.text[:400]
    assert _code(refused) == code
    assert (
        "clarifications" not in (_record(engine, pid)["ai"])
        or not _record(engine, pid)["ai"]["clarifications"]
    )


def test_four_eyes_the_proposal_creator_may_not_clarify(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, creator="reviewer")
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    assert _open(as_("reviewer"), pid, 1)["safety_clarification"] == {
        "eligible": False,
        "reason": "proposal_author",
    }
    refused = _clarify(as_("reviewer"), pid, 1, mid)
    _refused(refused, 403, "clarification_by_proposal_author")
    # Another approver may.
    assert _clarify(as_("owner"), pid, 1, mid).status_code == 200


def test_a_proposal_of_unknown_creator_is_refused_as_the_creators(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    record["ai"]["proposal"]["created_by_user_id"] = None
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    mid = _mentions(record)[0]["subject"]["mention_id"]
    refused = _clarify(as_("reviewer"), pid, 1, mid)
    _refused(refused, 403, "clarification_by_proposal_author")


def test_approved_non_latest_and_derived_are_refused(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    # M6d: an unresolved mention holds approval; a revision with no mention
    # is approved, then a clarification of the carried mention is refused.
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    _refused(_approve(as_("reviewer"), pid, 1), 409, "approval_blocked_by_safety")
    assert _clarify(as_("owner"), pid, 1, mid).status_code == 200
    assert _approve(as_("reviewer"), pid, 1).status_code == 200
    approved = _clarify(as_("owner"), pid, 1, mid)
    _refused(approved, 409, "revision_already_approved")

    pid2, _ = _saved(as_, provider)
    revised = as_().post(
        f"/design/projects/{pid2}/revisions", json={"request": request_for(), "note": "r2"}
    )
    assert revised.status_code == 201, revised.text[:400]
    # The block carries: revision 2 has the same mention open.
    assert [o["subject"]["mention_id"] for o in _mentions(_record(engine, pid2, 2))] == [mid]
    stale = _clarify(as_("reviewer"), pid2, 1, mid)
    _refused(stale, 409, "revision_not_latest")
    assert _clarify(as_("reviewer"), pid2, 2, mid).status_code == 200

    pid3, _ = _saved(as_, provider)
    update_row(engine, pid3, 1, requirements=None, requirements_sha256=None)
    derived = _clarify(as_("reviewer"), pid3, 1, mid)
    _refused(derived, 409, "requirements_derived")


def test_a_clarification_carries_to_the_next_revision(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    assert _clarify(as_("reviewer"), pid, 1, mid).status_code == 200
    revised = as_().post(
        f"/design/projects/{pid}/revisions", json={"request": request_for(), "note": "r2"}
    )
    assert revised.status_code == 201, revised.text[:400]
    second = _record(engine, pid, 2)
    assert _mentions(second) == []
    assert [c["mention_id"] for c in second["ai"]["clarifications"]] == [mid]


def test_a_life_safety_item_blocks_every_clarification(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    description = "Main board MDB. Fire pump 5.5 kW on MDB. Emergency contact on the label."
    items = [
        {
            "id": "l1",
            "kind": "load",
            "source": span(description, "Fire pump 5.5 kW"),
            "value": {"board": "MDB", "description": "Pump", "kind": "motor", "power_kw": "5.5"},
        }
    ]
    pid, proposal = _saved(as_, provider, description, items, decide={"i1": "reject"})
    assert proposal["items"][0]["kind"] == "unsupported"
    record = _record(engine, pid)
    assert {o["code"] for o in _mentions(record)} == {
        "ai_life_safety_mentioned",
        "ai_possibly_life_safety",
    }
    assert _open(as_("reviewer"), pid, 1)["safety_clarification"] == {
        "eligible": False,
        "reason": "safety_concern_unresolved",
    }
    for mention in _mentions(record):
        refused = _clarify(as_("reviewer"), pid, 1, mention["subject"]["mention_id"])
        expected = (
            "mention_is_life_safety"
            if mention["code"] == "ai_life_safety_mentioned"
            else "safety_concern_unresolved"
        )
        _refused(refused, 409, expected)
    assert _record(engine, pid) == record


def test_an_undecided_possibly_item_blocks_and_a_decided_one_does_not_become_a_load(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    description = "Main board MDB. Standby pump 5.5 kW on MDB."
    items = [
        {
            "id": "l1",
            "kind": "load",
            "source": span(description, "Standby pump 5.5 kW"),
            "value": {"board": "MDB", "description": "Pump", "kind": "motor", "power_kw": "5.5"},
        }
    ]
    pid, _ = _saved(as_, provider, description, items)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    refused = _clarify(as_("reviewer"), pid, 1, mid)
    _refused(refused, 409, "safety_concern_unresolved")

    pid2, _ = _saved(as_, provider, description, items, decide={"i1": "accept"})
    done = _clarify(as_("reviewer"), pid2, 1, mid)
    assert done.status_code == 200, done.text[:400]
    record = _record(engine, pid2)
    # The item stays unsupported and open; nothing was placed into the request.
    assert record["ai"]["proposal"]["items"][0]["kind"] == "unsupported"
    assert {"code": "ai_unsupported", "subject": {"item_id": "i1"}} in [
        {"code": o["code"], "subject": {k: v for k, v in o["subject"].items() if v is not None}}
        for o in record["open_items"]
    ]
    assert record["ai"]["placements"] == []


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


@pytest.mark.parametrize(
    "body",
    [
        {"statement": "not_life_safety", "reason": "short"},
        {"statement": "not_life_safety", "reason": " " * 30},
        {"statement": "not_life_safety", "reason": "x" * 501},
        {"statement": "validated", "reason": "A label text, not equipment."},
        {
            "statement": "not_life_safety",
            "reason": "A label text, not equipment.",
            "by_user_id": "x",
        },
        {
            "statement": "not_life_safety",
            "reason": "A label text, not equipment.",
            "by_name": "Eng",
        },
        {"statement": "not_life_safety", "reason": "A label text, not equipment.", "at": "2020"},
        {
            "statement": "not_life_safety",
            "reason": "A label text, not equipment.",
            "validated": True,
        },
    ],
    ids=["short", "blank", "long", "statement", "by_user_id", "by_name", "at", "validated"],
)
def test_the_body_is_strict(
    engine: Engine, as_: Any, provider: StubProvider, body: dict[str, Any]
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    refused = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/1/requirements/safety-clarifications",
        json={"mention_id": mid, **body},
    )
    assert refused.status_code == 422, refused.text[:400]


def test_an_unknown_mention_is_not_found(engine: Engine, as_: Any, provider: StubProvider) -> None:
    pid, _ = _saved(as_, provider)
    missing = _clarify(as_("reviewer"), pid, 1, "m0123456789abcdef")
    _refused(missing, 404, "mention_not_found")


# --------------------------------------------------------------------------- #
# Integrity and concurrency
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["ai"]["clarifications"][0].__setitem__("by_name", "Someone else"),
        lambda r: r["ai"]["clarifications"][0].__setitem__("reason", "forged reason text"),
        lambda r: r["ai"]["clarifications"].clear(),
    ],
    ids=["by_name", "reason", "removed"],
)
def test_a_tampered_clarification_is_detected(
    engine: Engine, as_: Any, provider: StubProvider, mutate: Any
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    assert _clarify(as_("reviewer"), pid, 1, mid).status_code == 200
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    mutate(record)
    update_row(engine, pid, 1, requirements=record)
    approval = _approve(as_("owner"), pid, 1)
    assert approval.status_code == 409, approval.text[:400]
    assert error(approval)["params"]["reason"] == "requirements_mismatch"


def test_a_forged_clarification_rehashed_after_approval_makes_it_not_current(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    assert _clarify(as_("owner"), pid, 1, mid).status_code == 200
    assert _approve(as_("reviewer"), pid, 1).status_code == 200
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    record["ai"]["clarifications"] = [
        {
            "mention_id": mid,
            "statement": "not_life_safety",
            "reason": "forged after approval",
            "by_user_id": "x",
            "by_name": "x",
            "at": "2026-01-01",
        }
    ]
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    assert _open(as_("reviewer"), pid, 1)["approval_current"] is not True


def test_two_concurrent_clarifications_record_one(
    engine: Engine, app: FastAPI, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    results: dict[str, Any] = {}
    threads = []
    with _project_held(engine, pid):
        for name in ("reviewer", "owner"):
            before = _waiting(engine)

            def run(who: str = name) -> None:
                results[who] = _clarify(_As(app, who), pid, 1, mid)

            thread = threading.Thread(target=run)
            thread.start()
            threads.append(thread)
            deadline = time.monotonic() + 20
            while _waiting(engine) <= before:
                assert thread.is_alive()
                assert time.monotonic() < deadline
                time.sleep(0.02)
    for thread in threads:
        thread.join()
    assert results["reviewer"].status_code == 200, results["reviewer"].text[:400]
    _refused(results["owner"], 404, "mention_not_found")
    assert len(_record(engine, pid)["ai"]["clarifications"]) == 1


def test_a_clarification_queued_behind_an_approval_is_refused(
    engine: Engine, app: FastAPI, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider)
    mid = _mentions(_record(engine, pid))[0]["subject"]["mention_id"]
    results: dict[str, Any] = {}
    calls = {
        "approve": lambda: _approve(_As(app, "reviewer"), pid, 1),
        "clarify": lambda: _clarify(_As(app, "owner"), pid, 1, mid),
    }
    threads = []
    with _project_held(engine, pid):
        for name, call in calls.items():
            before = _waiting(engine)

            def run(n: str = name, c: Any = call) -> None:
                results[n] = c()

            thread = threading.Thread(target=run)
            thread.start()
            threads.append(thread)
            deadline = time.monotonic() + 20
            while _waiting(engine) <= before:
                assert thread.is_alive()
                assert time.monotonic() < deadline
                time.sleep(0.02)
    for thread in threads:
        thread.join()
    # M6d: the approval is held by the open mention, so it records nothing;
    # the clarification queued behind it records.
    _refused(results["approve"], 409, "approval_blocked_by_safety")
    assert results["clarify"].status_code == 200, results["clarify"].text[:400]
    assert AI  # the proposal route the fixtures drive
