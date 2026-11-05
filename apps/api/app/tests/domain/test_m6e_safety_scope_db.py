"""M6e: the safety hold sees every revision and the request itself, whatever the intake.

A concern raised on an earlier revision (its stored description, items or
request) holds a later revision's approval until resolved there; the
request's own text (project information, board names, load descriptions) is
read by the same detector on every approval; a life-safety board or load
still in the request cannot be resolved, only removed. Both
``DESIGN_SNAPSHOT_SCHEMA`` modes. Synthetic data only.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    PRICING,
    _engine,
    _tenants,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    CONTROL,
    StubProvider,
    _app_fixture,
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
from app.tests.domain.test_m6d_safety_resolution_db import (
    EXCLUDE_REASON,
    FIRE_PUMP,
    FP_REASON,
    _approve,
    _export,
    _held,
    _load,
    _pdf_text,
    _refused,
    _saved,
)


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["v1", "v2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


def _manual(as_: Any, loads: list[str], name: str = "Manual") -> str:
    """A manual-intake project whose board MDB holds the request's loads, renamed."""
    request = request_for()
    request["info"]["name"] = name
    rows = request["boards"][0]["loads"]
    for row, description in zip(rows, loads, strict=False):
        row["description"] = description
    saved = as_().post("/design/projects", json={"name": name, "request": request, "note": "r1"})
    assert saved.status_code == 201, saved.text[:400]
    return str(saved.json()["id"])


def _revise(as_: Any, pid: str, request: dict[str, Any], **extra: Any) -> None:
    revised = as_().post(
        f"/design/projects/{pid}/revisions", json={"request": request, "note": "rn", **extra}
    )
    assert revised.status_code == 201, revised.text[:400]


def _concerns(as_: Any, pid: str, n: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = _open(as_("reviewer"), pid, n)["safety_concerns"]
    return found


def _post(
    as_: Any,
    pid: str,
    n: int,
    c: dict[str, Any],
    resolution: str,
    reason: str,
    confirm: bool = True,
) -> Any:
    """Resolve ``c``; an exclusion asked for confirmation confirms every candidate (``confirm``)."""
    body: dict[str, Any] = {"resolution": resolution, "reason": reason}
    key = {"mention": "mention_id", "item": "item_id", "request": "request_id"}[c["kind"]]
    body[key] = c["target_id"]
    if c["kind"] != "request" and c.get("carried"):
        body["proposal_id"] = c["proposal_id"]
    if confirm and resolution == "excluded_from_scope" and c.get("confirmation_candidates"):
        body["not_this_equipment"] = [
            {"board": x["board"], "load_index": x["load_index"]}
            for x in c["confirmation_candidates"]
        ]
    return as_("reviewer").post(
        f"/design/projects/{pid}/revisions/{n}/requirements/safety-resolutions", json=body
    )


# --------------------------------------------------------------------------- #
# 1. Concerns of earlier revisions carry
# --------------------------------------------------------------------------- #


def test_a_fresh_proposal_without_the_pump_keeps_the_earlier_concerns(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(
        as_, provider, FIRE_PUMP, [_load(FIRE_PUMP, "A fire pump of 30 kW", power_kw="30")]
    )
    text = "Main board MDB. Office lighting 1 kW on MDB."
    provider.payload = _payload([])
    fresh = _propose(as_(), text, request={"boards": [{"name": "MDB"}]})
    assert fresh.status_code == 201, fresh.text[:400]
    _revise(
        as_,
        pid,
        request_for(),
        requirements_input={
            "intake": "ai",
            "control": CONTROL,
            "ai": {"proposal_id": fresh.json()["id"], "placements": []},
        },
    )
    concerns = _concerns(as_, pid, 2)
    carried = [c for c in concerns if c["carried"]]
    assert {c["kind"] for c in carried} == {"mention", "item"}
    assert all(c["raised_on"] == 1 for c in carried)
    _held(_approve(as_("owner"), pid, 2), {"ai_life_safety_mentioned", "ai_life_safety_item"})
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 2).content)
    assert "[OPEN] Safety concern (raised on revision 1): 'fire pump'" in printed
    for c in carried:
        done = _post(as_, pid, 2, c, "excluded_from_scope", EXCLUDE_REASON)
        assert done.status_code == 200, done.text[:400]
    # On the revision approved, carried forward: not on the earlier one.
    stored = revision_row(engine, pid, 2)["requirements"]
    assert len(stored["safety_resolutions"]) == 2
    assert revision_row(engine, pid, 1)["requirements"]["ai"].get("resolutions", []) == []
    assert _approve(as_("owner"), pid, 2).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 2).content)
    assert "[EXCLUDED FROM SCOPE] 'fire pump' (raised on revision 1)" in printed
    assert "[OPEN] Safety concern" not in printed
    # And a revision 3 carries the resolutions too.
    _revise(as_, pid, request_for())
    assert _open(as_("reviewer"), pid, 3)["approval_held"] is None


# --------------------------------------------------------------------------- #
# 2. The request itself, whatever the intake
# --------------------------------------------------------------------------- #


def test_a_manual_fire_pump_load_holds_approval_until_removed_then_excluded(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid = _manual(as_, ["Booster pump", "Fire pump FP-1"])
    (concern,) = _concerns(as_, pid, 1)
    assert concern["kind"] == "request"
    assert concern["code"] == "request_life_safety"
    assert concern["where"].startswith("MDB")
    _held(_approve(as_("reviewer"), pid), {"request_life_safety"})
    # Still in the request: neither resolution.
    refused = _refused(
        _post(as_, pid, 1, concern, "excluded_from_scope", EXCLUDE_REASON),
        409,
        "exclusion_not_allowed",
    )
    assert refused["params"]["reason"] == "in_request"
    _refused(
        _post(as_, pid, 1, concern, "false_positive", FP_REASON), 409, "false_positive_not_allowed"
    )
    # Renamed in revision 2: still drawn, so not excludable (M6e re-check).
    request = copy.deepcopy(revision_row(engine, pid, 1)["request"])
    request["boards"][0]["loads"][1]["description"] = "FP-1"
    _revise(as_, pid, request)
    (renamed,) = _concerns(as_, pid, 2)
    assert renamed["carried"] is True
    assert renamed["exclusion_refusal"] == "still_in_request"
    refused = _refused(
        _post(as_, pid, 2, renamed, "excluded_from_scope", EXCLUDE_REASON),
        409,
        "exclusion_not_allowed",
    )
    assert refused["params"]["reason"] == "still_in_request"
    _held(_approve(as_("owner"), pid, 2), {"request_life_safety"})
    # Removed in revision 3: the carried concern is excluded, then approval.
    del request["boards"][0]["loads"][1]
    _revise(as_, pid, request)
    (carried,) = _concerns(as_, pid, 3)
    assert carried["carried"] is True
    assert carried["raised_on"] == 1
    assert carried["exclusion_refusal"] is None
    _held(_approve(as_("owner"), pid, 3), {"request_life_safety"})
    done = _post(as_, pid, 3, carried, "excluded_from_scope", EXCLUDE_REASON)
    assert done.status_code == 200, done.text[:400]
    assert _approve(as_("owner"), pid, 3).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 3).content)
    assert "[EXCLUDED FROM SCOPE] 'fire pump' (raised on revision 1)" in printed
    # The resolver is named by display name, never "a team member" (M6e re-check).
    reviewer = _open(as_("reviewer"), pid, 3)["approving_as"]
    assert reviewer
    assert f"(recorded by {reviewer} on" in printed
    assert "— not engineer-validated" in printed.replace("- ", "-")


def test_a_possibly_safety_load_or_project_name_may_be_a_false_positive(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid = _manual(as_, ["Standby pump", "Office lighting"], name="Fire Station Road works")
    concerns = _concerns(as_, pid, 1)
    assert {c["code"] for c in concerns} == {"request_possibly_life_safety"}
    assert {c["exclusion_refusal"] for c in concerns} == {"in_request", None}
    _held(_approve(as_("owner"), pid), {"request_possibly_life_safety"})
    for c in concerns:
        done = _post(as_, pid, 1, c, "false_positive", FP_REASON)
        assert done.status_code == 200, done.text[:400]
    assert _approve(as_("owner"), pid).status_code == 200
    block = json.loads(_export(as_("owner"), pid, "json").content)["approval"]
    assert len(block["false_positives"]) == len(concerns)


def test_the_author_and_a_trial_may_not_resolve_a_request_concern(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    request = request_for()
    request["boards"][0]["loads"][0]["description"] = "Standby pump"
    saved = as_("owner").post(
        "/design/projects", json={"name": "M", "request": request, "note": "r1"}
    )
    pid = str(saved.json()["id"])
    (c,) = _concerns(as_, pid, 1)
    body = {"request_id": c["target_id"], "resolution": "false_positive", "reason": FP_REASON}
    url = f"/design/projects/{pid}/revisions/1/requirements/safety-resolutions"
    _refused(as_("owner").post(url, json=body), 403, "safety_resolution_by_revision_author")
    _refused(as_("trial_id").post(url, json=body), 403, "safety_resolution_requires_account")
    _refused(as_("other_tenant").post(url, json=body), 404, "revision_not_found")
    assert as_("reviewer").post(url, json={**body, "proposal_id": "x"}).status_code == 422


# --------------------------------------------------------------------------- #
# 4/8. Every review export prints the resolutions, never validated
# --------------------------------------------------------------------------- #


def test_the_quotation_prints_exclusions_and_no_safety_open_line_under_approval(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _saved(
        as_, provider, FIRE_PUMP, [_load(FIRE_PUMP, "A fire pump of 30 kW", power_kw="30")]
    )
    for c in _concerns(as_, pid, 1):
        assert _post(as_, pid, 1, c, "excluded_from_scope", EXCLUDE_REASON).status_code == 200
    assert _approve(as_("owner"), pid).status_code == 200
    for fmt in ("pdf", "calculations_pdf", "quotation_pdf"):
        extra: dict[str, Any] = {"pricing": PRICING} if fmt == "quotation_pdf" else {}
        done = _export(as_("owner"), pid, fmt, 1, **extra)
        assert done.status_code == 200, (fmt, done.text[:300])
        printed = _pdf_text(done.content)
        assert "[EXCLUDED FROM SCOPE] 'fire pump'" in printed, fmt
        assert "— not engineer-validated" in printed.replace("- ", "-"), fmt
        for open_safety in ("[OPEN] Safety concern", "life-safety equipment ('", "may refer to"):
            assert open_safety not in printed, (fmt, open_safety)


# --------------------------------------------------------------------------- #
# 5. Cross-row conflicts
# --------------------------------------------------------------------------- #

TWO_ROWS = "Main board MDB. Chiller CH-1 11 kW on MDB. Later: chiller CH-1 15 kW on MDB."


def test_one_load_placed_on_two_rows_with_different_values_is_open(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    items = [
        {
            **_load(TWO_ROWS, "Chiller CH-1 11 kW", "a", power_kw="11"),
            "value": {
                "board": "MDB",
                "description": "Chiller CH-1",
                "kind": "motor",
                "power_kw": "11",
            },
        },
        {
            **_load(TWO_ROWS, "chiller CH-1 15 kW", "b", power_kw="15"),
            "value": {
                "board": "MDB",
                "description": "chiller  CH-1",
                "kind": "motor",
                "power_kw": "15",
            },
        },
    ]
    provider.payload = _payload(items)
    created = _propose(as_(), TWO_ROWS, request={"boards": [{"name": "MDB"}]})
    assert created.status_code == 201, created.text[:400]
    proposal = created.json()
    for item in proposal["items"]:
        assert _decide(as_(), proposal["id"], item["id"], {"decision": "accept"}).status_code == 200
    request = request_for()
    rows = request["boards"][0]["loads"]
    rows[0].update(description="Chiller CH-1", power_kw="11")
    rows[1].update(description="chiller CH-1", load="motor", power_kw="15", phases=3, starter="dol")
    rows[1].pop("controlled", None)
    saved = _save(
        as_(),
        request,
        {
            "intake": "ai",
            "control": CONTROL,
            "ai": {
                "proposal_id": proposal["id"],
                "placements": [
                    {"item_id": proposal["items"][0]["id"], "board_index": 0, "load_index": 0},
                    {"item_id": proposal["items"][1]["id"], "board_index": 0, "load_index": 1},
                ],
            },
        },
    )
    assert saved.status_code == 201, saved.text[:400]
    pid = str(saved.json()["id"])
    conflicts = [
        o["subject"]
        for o in revision_row(engine, pid, 1)["requirements"]["open_items"]
        if o["code"] == "ai_conflicting_values"
    ]
    assert ("rows_disagree", "power_kw") in {(s["kind"], s["field"]) for s in conflicts}
    printed = _pdf_text(_export(as_("reviewer"), pid, "pdf").content)
    assert "[OPEN] Conflicting values for power_kw of one load placed on two rows: 11" in printed


# --------------------------------------------------------------------------- #
# The renderer bump: an approval under the weaker rules stops counting
# --------------------------------------------------------------------------- #

#: The renderer versions before M6e, by snapshot schema mode.
BEFORE_M6E = {None: "2026.11.04.7", "pp-design/2": "2026.11.04.v2-22"}


def test_an_approval_recorded_before_the_m6e_bump_is_not_current(
    engine: Engine, as_: Any, provider: StubProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    pid = _manual(as_, ["Booster pump", "Office lighting"])
    assert _approve(as_("reviewer"), pid).status_code == 200
    assert _open(as_("reviewer"), pid, 1)["approval_current"] is True
    previous = BEFORE_M6E[os.environ.get("DESIGN_SNAPSHOT_SCHEMA")]
    update_row(engine, pid, 1, approved_renderer_version=previous)
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["approval_current"] is False
    assert "renderer_version" in opened["approval_changed"]
    _refused(_export(as_("reviewer"), pid, "pdf"), 409, "revision_stale")


# --------------------------------------------------------------------------- #
# The per-load confirmation of a carried life-safety exclusion
# --------------------------------------------------------------------------- #


def test_excluding_a_moved_pump_demands_and_records_a_per_load_confirmation(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid = _manual(as_, ["Booster pump", "Fire pump FP-1"])
    request = copy.deepcopy(revision_row(engine, pid, 1)["request"])
    # The pump moves to another board under a new name and power: unmatched.
    pump = {**request["boards"][0]["loads"][0], "description": "P-7", "power_kw": "18.5"}
    del request["boards"][0]["loads"][1]
    request["boards"].append(
        {**copy.deepcopy(request["boards"][0]), "name": "DB-2", "loads": [pump]}
    )
    request["boards"][1]["fed_from"] = "MDB"
    revised = as_().post(
        f"/design/projects/{pid}/revisions", json={"request": request, "note": "r2"}
    )
    if revised.status_code != 201:
        request["boards"][1].pop("fed_from", None)
        revised = as_().post(
            f"/design/projects/{pid}/revisions", json={"request": request, "note": "r2"}
        )
    assert revised.status_code == 201, revised.text[:400]
    (c,) = _concerns(as_, pid, 2)
    assert c["exclusion_refusal"] is None
    listed = {(x["board"], x["load_index"]) for x in c["confirmation_candidates"]}
    assert ("DB-2", 0) in listed
    # Without the confirmation: 409 naming the missing loads.
    refused = _refused(
        _post(as_, pid, 2, c, "excluded_from_scope", EXCLUDE_REASON, confirm=False),
        409,
        "exclusion_confirmation_incomplete",
    )
    assert "DB-2:0" in refused["params"]["missing"]
    # A load that is not a candidate: 422.
    body = {
        "request_id": c["target_id"],
        "resolution": "excluded_from_scope",
        "reason": EXCLUDE_REASON,
        "not_this_equipment": [
            *({"board": b, "load_index": i} for b, i in listed),
            {"board": "MDB", "load_index": 99},
        ],
    }
    url = f"/design/projects/{pid}/revisions/2/requirements/safety-resolutions"
    assert as_("reviewer").post(url, json=body).status_code == 422
    _held(_approve(as_("owner"), pid, 2), {"request_life_safety"})
    # Confirmed in full (falsely, here): recorded with the loads, printed, approvable.
    done = _post(as_, pid, 2, c, "excluded_from_scope", EXCLUDE_REASON)
    assert done.status_code == 200, done.text[:400]
    (entry,) = revision_row(engine, pid, 2)["requirements"]["safety_resolutions"]
    assert {(x["board"], x["load_index"]) for x in entry["confirmed_loads"]} == listed
    assert any(
        x["description"] == "P-7" and x["power_kw"] == "18.5" for x in entry["confirmed_loads"]
    )
    assert _approve(as_("owner"), pid, 2).status_code == 200
    printed = _pdf_text(_export(as_("owner"), pid, "pdf", 2).content)
    assert "confirmed these loads are not that equipment:" in printed
    assert "P-7 (18.5 kW)" in printed
    # The request changes: the confirmation no longer covers it; confirm again.
    request["boards"][1]["loads"].append({**pump, "description": "P-8"})
    revised = as_().post(
        f"/design/projects/{pid}/revisions", json={"request": request, "note": "r3"}
    )
    assert revised.status_code == 201, revised.text[:400]
    _held(_approve(as_("owner"), pid, 3), {"request_life_safety"})
