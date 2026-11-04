"""M6d: an unresolved safety concern holds a review approval; two auditable resolutions.

Drives the HTTP routes against a disposable ``panelpilot_test_*`` Postgres.
A revision whose stored requirements record holds a life-safety or
possibly-safety mention or item that no resolution covers is refused
approval (409 ``approval_blocked_by_safety``). A second member who may
approve, did not create the proposal and did not save the revision records
either ``excluded_from_scope`` (real, not covered here) or ``false_positive``
(not a life-safety requirement), append-only under ``requirements_sha256``.
The approval confirms its scope (``scope_confirmed``) and every review
export prints what it covers. Both ``DESIGN_SNAPSHOT_SCHEMA`` modes run
every test. Synthetic data only.
"""

from __future__ import annotations

import copy
import io
import json
import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from app.design import approval_scope, snapshot
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
    CONTROL,
    StubProvider,
    _app_fixture,
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

NORMAL = "Main board MDB. Office lighting 1 kW on MDB. Booster pump 5.5 kW on MDB."
FIRE_PUMP = "Main board MDB. Office lighting 1 kW on MDB. A fire pump of 30 kW on MDB."
FIRE_MAIN = "Main board MDB. Office lighting 1 kW on MDB. The fire main is by others."
NO_PUMP = "Main board MDB. Office lighting 1 kW on MDB. No fire pump is required on this site."
ROAD = "Main board MDB. Office lighting 1 kW on MDB. The site is on Fire Station Road."
STANDBY = "Main board MDB. Office lighting 1 kW on MDB. Duty/standby pumps of 4 kW on MDB."
CONFLICT = "Main board MDB. Office lighting 1 kW on MDB, or maybe 2 kW; length not known."
OUTSIDE = "Main board MDB. Office lighting 1 kW on MDB. A UPS room with its own inverter."

EXCLUDE_REASON = "Fire pump is designed separately by the fire-protection contractor."
FP_REASON = "A street name in the site address, not equipment."


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["v1", "v2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


def _load(description: str, phrase: str, item_id: str = "l1", **value: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": "load",
        "source": span(description, phrase),
        "value": {
            "board": "MDB",
            "description": "Pump",
            "kind": "motor",
            "power_kw": "5.5",
            **value,
        },
    }


def _saved(
    as_: Any,
    provider: StubProvider,
    description: str,
    items: list[dict[str, Any]] | None = None,
    creator: str = "engineer",
    decide: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Revision 1 of a project applying a proposal for ``description``, nothing placed."""
    provider.payload = _payload(items or [])
    created = _propose(as_(creator), description, request={"boards": [{"name": "MDB"}]})
    assert created.status_code == 201, created.text[:400]
    proposal = created.json()
    for item_id, decision in (decide or {}).items():
        decided = _decide(as_(creator), proposal["id"], item_id, {"decision": decision})
        assert decided.status_code == 200, decided.text[:400]
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


def _approve(c: _As, pid: str, n: int = 1, **body: Any) -> Any:
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/approval", json={"scope_confirmed": True, **body}
    )


def _resolve(
    c: _As,
    pid: str,
    n: int,
    resolution: str,
    *,
    mention: str | None = None,
    item: str | None = None,
    reason: str = EXCLUDE_REASON,
    **extra: Any,
) -> Any:
    body: dict[str, Any] = {"resolution": resolution, "reason": reason, **extra}
    if mention is not None:
        body["mention_id"] = mention
    if item is not None:
        body["item_id"] = item
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/requirements/safety-resolutions", json=body
    )


def _concerns(c: _As, pid: str, n: int = 1) -> list[dict[str, Any]]:
    concerns: list[dict[str, Any]] = _open(c, pid, n)["safety_concerns"]
    return concerns


def _one(concerns: list[dict[str, Any]], kind: str, level: str | None = None) -> str:
    (found,) = [c for c in concerns if c["kind"] == kind and (level is None or c["level"] == level)]
    return str(found["target_id"])


def _refused(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text[:600]
    body = error(response)
    assert body.get("code") == code, response.text[:600]
    return body


def _export(c: _As, pid: str, fmt: str, n: int = 1, **extra: Any) -> Any:
    return c.post(f"/design/projects/{pid}/revisions/{n}/export", json={"format": fmt, **extra})


def _pdf_text(content: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return " ".join(" ".join((page.extract_text() or "").split()) for page in pdf.pages)


def _held(response: Any, codes: set[str]) -> None:
    body = _refused(response, 409, "approval_blocked_by_safety")
    assert set(body["params"]["codes"].split(",")) == codes, body
    assert int(body["params"]["count"]) >= len(codes), body


# --------------------------------------------------------------------------- #
# The hold: each concern type
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("description", "items", "codes"),
    [
        (FIRE_MAIN, None, {"ai_life_safety_mentioned"}),
        (
            FIRE_PUMP,
            [_load(FIRE_PUMP, "A fire pump of 30 kW", power_kw="30")],
            {"ai_life_safety_mentioned", "ai_life_safety_item"},
        ),
        (ROAD, None, {"ai_possibly_life_safety"}),
        (
            STANDBY,
            [_load(STANDBY, "Duty/standby pumps of 4 kW", power_kw="4")],
            {"ai_possibly_life_safety", "ai_possibly_life_safety_item"},
        ),
    ],
    ids=["life_safety_mention", "life_safety_item", "possibly_mention", "possibly_item"],
)
def test_each_unresolved_concern_holds_approval(
    engine: Engine,
    as_: Any,
    provider: StubProvider,
    description: str,
    items: list[dict[str, Any]] | None,
    codes: set[str],
) -> None:
    pid, _ = _saved(as_, provider, description, items)
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["approval_held"]["code"] == "approval_blocked_by_safety"
    assert set(opened["approval_held"]["codes"]) == codes
    assert opened["approval_held"]["count"] == len(codes)
    _held(_approve(as_("reviewer"), pid), codes)
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_model_made_life_safety_unsupported_item_holds_too(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    item = {
        "id": "u1",
        "kind": "unsupported",
        "source": span(NORMAL, "Booster pump 5.5 kW"),
        "value": {"text": "sprinkler booster", "reason": "sprinkler equipment"},
    }
    pid, _ = _saved(as_, provider, NORMAL, [item], decide={"i1": "reject"})
    # Rejecting the item (its decision) does not resolve it.
    _held(_approve(as_("reviewer"), pid), {"ai_life_safety_item"})


def test_a_generic_acknowledgement_never_unblocks(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    record = _record(engine, pid)
    question = next(q for q in record["questions"] if q["kind"] == "assumption")
    done = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/1/questions/{question['id']}",
        json={"action": "acknowledge"},
    )
    assert done.status_code == 200, done.text[:400]
    _held(_approve(as_("reviewer"), pid), {"ai_life_safety_mentioned"})
    # And the deprecated clarification route cannot clear a life-safety mention.
    mid = _one(_concerns(as_("reviewer"), pid), "mention")
    old = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/1/requirements/safety-clarifications",
        json={"mention_id": mid, "statement": "not_life_safety", "reason": FP_REASON},
    )
    _refused(old, 409, "mention_is_life_safety")
    _held(_approve(as_("reviewer"), pid), {"ai_life_safety_mentioned"})


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


def test_scenario_a_normal_board_request_is_approvable(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, NORMAL, [_load(NORMAL, "Booster pump 5.5 kW")])
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["safety_concerns"] == []
    assert opened["approval_held"] is None
    assert opened["safety_resolution"] is None
    approved = _approve(as_("reviewer"), pid, note="checked")
    assert approved.status_code == 200, approved.text[:400]
    assert approved.json()["approval_current"] is True
    assert approved.json()["approved_scope_version"] == approval_scope.APPROVAL_SCOPE_VERSION
    row = revision_row(engine, pid, 1)
    assert row["approved_scope_version"] == approval_scope.APPROVAL_SCOPE_VERSION
    exported = _export(as_("reviewer"), pid, "json")
    assert exported.status_code == 200, exported.text[:400]
    block = json.loads(exported.content)["approval"]
    assert block["approved"] is True
    assert block["scope_confirmed"] is True
    assert block["engineer_validated"] is False
    assert block["does_not_cover"] == list(approval_scope.NOT_COVERED)
    assert block["excluded_from_scope"] == []
    assert block["false_positives"] == []


def test_scenario_an_explicit_fire_pump_is_held_then_excluded_then_approvable(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(
        as_, provider, FIRE_PUMP, [_load(FIRE_PUMP, "A fire pump of 30 kW", power_kw="30")]
    )
    concerns = _concerns(as_("reviewer"), pid)
    mention = _one(concerns, "mention")
    item = _one(concerns, "item")
    assert {c["false_positive_refusal"] for c in concerns} == {
        "life_safety_item_overlaps",
        "item",
    }
    # Not a false positive: the proposal itself reads the span as a fire pump.
    fp = _refused(
        _resolve(as_("reviewer"), pid, 1, "false_positive", mention=mention, reason=FP_REASON),
        409,
        "false_positive_not_allowed",
    )
    assert fp["params"]["reason"] == "life_safety_item_overlaps"
    fp_item = _refused(
        _resolve(as_("reviewer"), pid, 1, "false_positive", item=item, reason=FP_REASON),
        409,
        "false_positive_not_allowed",
    )
    assert fp_item["params"]["reason"] == "item"
    done = _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", item=item)
    assert done.status_code == 200, done.text[:400]
    _held(_approve(as_("owner"), pid), {"ai_life_safety_mentioned"})
    assert (
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention=mention).status_code == 200
    )
    # Twice: never replaced.
    _refused(
        _resolve(as_("owner"), pid, 1, "excluded_from_scope", mention=mention),
        409,
        "safety_concern_already_resolved",
    )
    record = _record(engine, pid)
    entries = record["ai"]["resolutions"]
    assert [(e["target_kind"], e["resolution"]) for e in entries] == [
        ("item", "excluded_from_scope"),
        ("mention", "excluded_from_scope"),
    ]
    assert all(e["by_user_id"] and e["by_name"] and e["at"] for e in entries)
    # Nothing became a load or placeable item.
    assert [i["kind"] for i in record["ai"]["proposal"]["items"]] == ["unsupported"]
    assert record["ai"]["placements"] == []
    approved = _approve(as_("owner"), pid)
    assert approved.status_code == 200, approved.text[:400]
    assert revision_row(engine, pid, 1)["approved_requirements_sha256"] == snapshot.sha256_of(
        record
    )
    for fmt in ("pdf", "calculations_pdf"):
        exported = _export(as_("owner"), pid, fmt)
        assert exported.status_code == 200, exported.text[:400]
        printed = _pdf_text(exported.content)
        assert "[EXCLUDED FROM SCOPE] 'fire pump' — not designed or reviewed here:" in printed
        assert "fire-protection contractor" in printed
        assert "[SCOPE] It does not cover: control circuits" in printed
        assert "It is not engineer validation." in printed
        assert "[SCOPE] The approver confirmed this scope" in printed
    exported = json.loads(_export(as_("owner"), pid, "json").content)
    excluded = exported["approval"]["excluded_from_scope"]
    assert len(excluded) == 2
    assert all(line.startswith("[EXCLUDED FROM SCOPE]") for line in excluded)
    assert all("(recorded by " in line for line in excluded)
    assert not any("@" in line for line in excluded)


@pytest.mark.parametrize("description", [ROAD, NO_PUMP], ids=["road", "no_pump"])
def test_scenario_ambiguous_wording_with_a_harmless_false_positive(
    engine: Engine, as_: Any, provider: StubProvider, description: str
) -> None:
    pid, _ = _saved(as_, provider, description)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    done = _resolve(as_("reviewer"), pid, 1, "false_positive", mention=mention, reason=FP_REASON)
    assert done.status_code == 200, done.text[:400]
    assert not any(o["subject"].get("mention_id") for o in _record(engine, pid)["open_items"])
    assert _approve(as_("owner"), pid).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf").content)
    assert "[FALSE POSITIVE] '" in printed
    assert "judged not a life-safety requirement by" in printed
    assert "not engineer-validated" in printed
    assert json.loads(_export(as_("owner"), pid, "json").content)["approval"]["false_positives"]


def test_a_possibly_item_follows_its_words_judged_harmless(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(
        as_, provider, STANDBY, [_load(STANDBY, "Duty/standby pumps of 4 kW", power_kw="4")]
    )
    concerns = _concerns(as_("reviewer"), pid)
    mention = _one(concerns, "mention")
    done = _resolve(
        as_("reviewer"),
        pid,
        1,
        "false_positive",
        mention=mention,
        reason="Duty and standby of a booster set.",
    )
    assert done.status_code == 200, done.text[:400]
    concerns = _concerns(as_("reviewer"), pid)
    assert all(c["resolved"] for c in concerns)
    assert _approve(as_("owner"), pid).status_code == 200
    # Still not a load, still printed as not covered.
    record = _record(engine, pid)
    assert [i["kind"] for i in record["ai"]["proposal"]["items"]] == ["unsupported"]
    printed = _pdf_text(_export(as_("owner"), pid, "pdf").content)
    assert "[OPEN] Not covered by this design (from AI intake)" in printed


def test_scenario_conflicting_or_missing_information_stays_open_and_does_not_hold(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    question = {
        "id": "q1",
        "kind": "question",
        "source": span(CONFLICT, "or maybe 2 kW; length not known"),
        "value": {
            "text": "Is the lighting 1 kW or 2 kW, and how far?",
            "about": None,
            "reason": "conflict",
        },
    }
    pid, proposal = _saved(as_, provider, CONFLICT, [question])
    assert [i["kind"] for i in proposal["items"]] == ["question"], proposal["server_rejected"]
    assert "ai_question" in {o["code"] for o in _record(engine, pid)["open_items"]}
    assert _open(as_("reviewer"), pid, 1)["approval_held"] is None
    assert _approve(as_("reviewer"), pid).status_code == 200
    printed = _pdf_text(_export(as_("reviewer"), pid, "pdf").content)
    assert "[OPEN] AI intake question, not answered" in printed


def test_scenario_a_requirement_outside_the_model_prints_as_not_covered(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    item = {
        "id": "u1",
        "kind": "unsupported",
        "source": span(OUTSIDE, "A UPS room with its own inverter"),
        "value": {"text": "UPS room with inverter", "reason": "not modelled"},
    }
    pid, _ = _saved(as_, provider, OUTSIDE, [item])
    assert _open(as_("reviewer"), pid, 1)["safety_concerns"] == []
    assert _approve(as_("reviewer"), pid).status_code == 200
    printed = _pdf_text(_export(as_("reviewer"), pid, "pdf").content)
    assert "[OPEN] Not covered by this design (from AI intake): UPS room with inverter" in printed
    assert "[SCOPE] It does not cover:" in printed


# --------------------------------------------------------------------------- #
# Who may resolve
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("caller", "status", "code", "flag"),
    [
        ("engineer", 403, "safety_resolution_not_permitted", "not_permitted"),
        ("ingestion", 403, "safety_resolution_not_permitted", "not_permitted"),
        ("trial_id", 403, "safety_resolution_requires_account", None),
        ("trial", 403, "safety_resolution_requires_account", None),
        ("other_tenant", 404, "revision_not_found", None),
    ],
)
def test_who_may_not_resolve(
    engine: Engine,
    as_: Any,
    provider: StubProvider,
    caller: str,
    status: int,
    code: str,
    flag: str | None,
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    if flag is not None:
        assert _open(as_(caller), pid, 1)["safety_resolution"] == {
            "eligible": False,
            "reason": flag,
        }
    before = _record(engine, pid)
    _refused(_resolve(as_(caller), pid, 1, "excluded_from_scope", mention=mention), status, code)
    assert _record(engine, pid) == before


def test_four_eyes_the_proposal_creator_may_not_resolve(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN, creator="reviewer")
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    assert _open(as_("reviewer"), pid, 1)["safety_resolution"] == {
        "eligible": False,
        "reason": "proposal_author",
    }
    _refused(
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention=mention),
        403,
        "safety_resolution_by_proposal_author",
    )


def test_four_eyes_the_revision_author_may_not_resolve(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    # The owner saves revision 2 (the block carries): its author.
    revised = as_("owner").post(
        f"/design/projects/{pid}/revisions", json={"request": request_for(), "note": "r2"}
    )
    assert revised.status_code == 201, revised.text[:400]
    mention = _one(_concerns(as_("owner"), pid, 2), "mention")
    assert _open(as_("owner"), pid, 2)["safety_resolution"] == {
        "eligible": False,
        "reason": "revision_author",
    }
    _refused(
        _resolve(as_("owner"), pid, 2, "excluded_from_scope", mention=mention),
        403,
        "safety_resolution_by_revision_author",
    )
    # The proposal's creator (engineer) cannot approve; a third member may resolve.
    assert (
        _resolve(as_("reviewer"), pid, 2, "excluded_from_scope", mention=mention).status_code == 200
    )


def test_approved_derived_and_non_latest_are_refused(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, ROAD)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    assert (
        _resolve(
            as_("reviewer"), pid, 1, "false_positive", mention=mention, reason=FP_REASON
        ).status_code
        == 200
    )
    assert _approve(as_("owner"), pid).status_code == 200
    _refused(
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention=mention),
        409,
        "revision_already_approved",
    )

    pid2, _ = _saved(as_, provider, ROAD)
    revised = as_().post(
        f"/design/projects/{pid2}/revisions", json={"request": request_for(), "note": "r2"}
    )
    assert revised.status_code == 201, revised.text[:400]
    _refused(
        _resolve(as_("reviewer"), pid2, 1, "false_positive", mention=mention, reason=FP_REASON),
        409,
        "revision_not_latest",
    )
    assert (
        _resolve(
            as_("reviewer"), pid2, 2, "false_positive", mention=mention, reason=FP_REASON
        ).status_code
        == 200
    )
    # The resolution carries to a later revision.
    revised = as_().post(
        f"/design/projects/{pid2}/revisions", json={"request": request_for(), "note": "r3"}
    )
    assert revised.status_code == 201, revised.text[:400]
    assert _open(as_("reviewer"), pid2, 3)["approval_held"] is None

    pid3, _ = _saved(as_, provider, ROAD)
    update_row(engine, pid3, 1, requirements=None, requirements_sha256=None)
    _refused(
        _resolve(as_("reviewer"), pid3, 1, "false_positive", mention=mention, reason=FP_REASON),
        409,
        "requirements_derived",
    )


def test_unknown_targets_and_strict_bodies(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    _refused(
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention="m0123456789abcdef"),
        404,
        "safety_target_not_found",
    )
    _refused(
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", item="i9"),
        404,
        "safety_target_not_found",
    )
    url = f"/design/projects/{pid}/revisions/1/requirements/safety-resolutions"
    good = {"mention_id": mention, "resolution": "excluded_from_scope", "reason": EXCLUDE_REASON}
    for body in (
        {**good, "item_id": "i1"},
        {k: v for k, v in good.items() if k != "mention_id"},
        {**good, "reason": "too short"},
        {**good, "reason": "x" * 501},
        {**good, "resolution": "validated"},
        {**good, "by_user_id": "x"},
        {**good, "by_name": "x"},
        {**good, "at": "2026-01-01"},
        {k: v for k, v in good.items() if k != "reason"},
    ):
        assert as_("reviewer").post(url, json=body).status_code == 422, body
    assert _record(engine, pid)["ai"].get("resolutions", []) == []


# --------------------------------------------------------------------------- #
# Integrity, concurrency and the approval body
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["ai"]["resolutions"][0].__setitem__("by_name", "Someone else"),
        lambda r: r["ai"]["resolutions"][0].__setitem__("reason", "forged reason text"),
        lambda r: r["ai"]["resolutions"].clear(),
    ],
    ids=["by_name", "reason", "removed"],
)
def test_a_tampered_resolution_is_detected(
    engine: Engine, as_: Any, provider: StubProvider, mutate: Any
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    assert (
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention=mention).status_code == 200
    )
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    mutate(record)
    update_row(engine, pid, 1, requirements=record)
    approval = _approve(as_("owner"), pid)
    assert approval.status_code == 409, approval.text[:400]
    assert error(approval)["params"]["reason"] == "requirements_mismatch"
    _refused(
        _resolve(as_("owner"), pid, 1, "excluded_from_scope", mention=mention),
        409,
        "snapshot_integrity_failed",
    )


def test_a_forged_resolution_rehashed_after_approval_makes_it_not_current(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    assert (
        _resolve(as_("reviewer"), pid, 1, "excluded_from_scope", mention=mention).status_code == 200
    )
    assert _approve(as_("owner"), pid).status_code == 200
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    record["ai"]["resolutions"][0]["resolution"] = "false_positive"
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["approval_current"] is False
    assert "requirements" in opened["approval_changed"]
    _refused(
        _export(as_("reviewer"), pid, "pdf", purpose="execution"),
        409,
        "execution_package_incomplete",
    )


def test_a_resolution_queued_behind_an_approval_is_refused_and_vice_versa(
    engine: Engine, app: FastAPI, as_: Any, provider: StubProvider
) -> None:
    def race(calls: dict[str, Any], pid: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
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
        return results

    # The resolution first: the approval queued behind it sees it and passes.
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    mention = _one(_concerns(as_("reviewer"), pid), "mention")
    results = race(
        {
            "resolve": lambda: _resolve(
                _As(app, "reviewer"), pid, 1, "excluded_from_scope", mention=mention
            ),
            "approve": lambda: _approve(_As(app, "owner"), pid),
        },
        pid,
    )
    assert results["resolve"].status_code == 200, results["resolve"].text[:400]
    assert results["approve"].status_code == 200, results["approve"].text[:400]
    row = revision_row(engine, pid, 1)
    assert row["approved_requirements_sha256"] == row["requirements_sha256"]

    # The approval first: held, and the resolution after it still records.
    pid2, _ = _saved(as_, provider, FIRE_MAIN)
    results = race(
        {
            "approve": lambda: _approve(_As(app, "owner"), pid2),
            "resolve": lambda: _resolve(
                _As(app, "reviewer"), pid2, 1, "excluded_from_scope", mention=mention
            ),
        },
        pid2,
    )
    _held(results["approve"], {"ai_life_safety_mentioned"})
    assert results["resolve"].status_code == 200, results["resolve"].text[:400]
    assert revision_row(engine, pid2, 1)["approved_at"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"note": "x"},
        {"scope_confirmed": False},
        {"scope_confirmed": "true"},
        {"scope_confirmed": 1},
        {"scope_confirmed": None},
    ],
)
def test_the_approval_requires_a_strict_scope_confirmation(
    engine: Engine, as_: Any, provider: StubProvider, body: dict[str, Any]
) -> None:
    pid, _ = _saved(as_, provider, NORMAL)
    refused = as_("reviewer").post(f"/design/projects/{pid}/revisions/1/approval", json=body)
    assert refused.status_code == 422, refused.text[:400]
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_an_approval_without_the_current_scope_does_not_count(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, NORMAL)
    assert _approve(as_("reviewer"), pid).status_code == 200
    update_row(engine, pid, 1, approved_scope_version=None)
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["approval_current"] is False
    assert "scope" in opened["approval_changed"]
    # Exported as approved never: the revision reads stale (``scope``).
    _refused(_export(as_("reviewer"), pid, "json"), 409, "revision_stale")


def test_execution_and_restricted_formats_stay_blocked(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(as_, provider, NORMAL)
    assert _approve(as_("reviewer"), pid).status_code == 200
    _refused(
        _export(as_("reviewer"), pid, "pdf", purpose="execution"),
        409,
        "execution_package_incomplete",
    )
    for fmt in ("devices_csv", "qet", "aml", "plc_st"):
        assert _export(as_("reviewer"), pid, fmt).status_code in (403, 409, 422), fmt
        assert error(_export(as_("reviewer"), pid, fmt))["code"] == "export_format_unprotected"


def test_a_dropped_mention_rehashed_still_holds_approval(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """The hold is re-derived from the stored description and items, not the open-item list."""
    pid, _ = _saved(as_, provider, FIRE_MAIN)
    record = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    record["open_items"] = [o for o in record["open_items"] if not o["subject"].get("mention_id")]
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    _held(_approve(as_("reviewer"), pid), {"ai_life_safety_mentioned"})
    # A forged resolution of another target resolves nothing either.
    record["ai"]["resolutions"] = [
        {
            "target_kind": "mention",
            "target_id": "m0123456789abcdef",
            "resolution": "excluded_from_scope",
            "reason": "forged for another target",
            "by_user_id": "x",
            "by_name": "x",
            "at": "2026-01-01",
        }
    ]
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    _held(_approve(as_("reviewer"), pid), {"ai_life_safety_mentioned"})


TWO = "Main board MDB. Booster pump 11 kW on MDB. Booster pump 15 kW on MDB."


def test_conflicting_accepted_values_are_open_and_printed(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    provider.payload = _payload(
        [
            _load(TWO, "Booster pump 11 kW", "a", power_kw="11"),
            _load(TWO, "Booster pump 15 kW", "b", power_kw="15"),
        ]
    )
    created = _propose(as_(), TWO, request={"boards": [{"name": "MDB"}]})
    assert created.status_code == 201, created.text[:400]
    proposal = created.json()
    for item in proposal["items"]:
        assert _decide(as_(), proposal["id"], item["id"], {"decision": "accept"}).status_code == 200
    saved = _save(
        as_(),
        request_for(power_kw="15"),
        {
            "intake": "ai",
            "control": CONTROL,
            "ai": {
                "proposal_id": proposal["id"],
                "placements": [
                    {"item_id": i["id"], "board_index": 0, "load_index": 0}
                    for i in proposal["items"]
                ],
            },
        },
    )
    assert saved.status_code == 201, saved.text[:400]
    pid = str(saved.json()["id"])
    conflicts = [
        o["subject"]
        for o in _record(engine, pid)["open_items"]
        if o["code"] == "ai_conflicting_values"
    ]
    kinds = {(s["kind"], s["field"]) for s in conflicts}
    assert ("items_disagree", "power_kw") in kinds
    assert ("changed_in_editor", "power_kw") in kinds
    # Not a safety hold.
    assert _open(as_("reviewer"), pid, 1)["approval_held"] is None
    exported = _export(as_("reviewer"), pid, "pdf")
    assert exported.status_code == 200, exported.text[:400]
    printed = _pdf_text(exported.content)
    assert "[OPEN] Conflicting values for power_kw: 11" in printed
    assert "[OPEN] Changed in the editor after acceptance: power_kw, AI value 11" in printed
    assert "entered value 15" in printed
