"""M6a adversarial tests against the disposable Postgres (``panelpilot_test_m6a_*``).

Written against ``docs/architecture/m6a-guided-intake.md`` independently of the
implementation. They drive the real routes with the Phase 1 fixtures:

* server-owned feeders: a saved project or revision cannot carry ``feeds`` or a
  typed ``sub_board`` row; nothing is saved when refused;
* the requirements record ``pp-req/1``: questions for each missing value and
  for the default power factor; answering changes the request (a new
  revision); acknowledging records the server's user and clock, and a body
  naming who or when is **refused** (422, the strict-model convention), never
  merged; acknowledging changes neither the design nor the revision count;
* self-approval: 403 ``approval_by_author``, by user id whatever the email's
  case; another member with approve permission succeeds; a revision with no
  recorded author approves with an ``author_unknown`` finding and no invented
  author;
* duplicate designations: blocking ``designation_duplicate``, approval
  refused, the review export prints it;
* another tenant gets 404 for open, questions and approval;
* a historical revision holding a ``sub_board`` row opens from its stored
  snapshot with the same hash and its approval still counts; re-designing it
  is refused;
* execution export and restricted formats stay refused.

Synthetic data only; every account is synthetic.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.models.schemas.auth import CurrentUser, Role
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    BLOCKED_FORMATS,
    REQUEST,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    error,
    pdf_pages,
    pytestmark,
    revision_export,
    revision_row,
    save,
    update_row,
)
from app.tests.domain import review_support
from app.tests.domain.test_design_projects_configured_db import CASE, _configuration, _revise
from app.tests.domain.test_design_projects_configured_db import _save as _save_configured

# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #

#: A main board and a sub-board fed from it, every required value given.
FED: dict[str, Any] = {
    "info": {"name": "M6a ADV fed", "number": "M6A-001"},
    "boards": [
        {
            "name": "MDB",
            "supply": {
                "voltage_v": 400,
                "phases": 3,
                "fault_level_ka": 10,
                "earth_loop_ohm": "0.1",
            },
            "loads": [
                {
                    "description": "MDB lights",
                    "load": "lighting",
                    "power_kw": "1",
                    "power_factor": "0.95",
                    "length_m": 10,
                },
            ],
        },
        {
            "name": "DB2",
            "fed_from": "MDB",
            "feeder_length_m": 10,
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {
                    "description": "DB2 sockets",
                    "load": "socket",
                    "power_kw": "2",
                    "power_factor": "0.95",
                    "length_m": 15,
                },
                {
                    "description": "DB2 lights",
                    "load": "lighting",
                    "power_kw": "1",
                    "power_factor": "0.95",
                    "length_m": 15,
                },
            ],
        },
    ],
}

#: The same project with the listed values left empty: MDB's fault level and
#: Ze, DB2's feeder length, a motor (nameplate current unknown) and a load
#: with no power factor (the designer's 0.9 default).
MISSING: dict[str, Any] = copy.deepcopy(FED)
MISSING["info"]["number"] = "M6A-002"
del MISSING["boards"][0]["supply"]["fault_level_ka"]
del MISSING["boards"][0]["supply"]["earth_loop_ohm"]
del MISSING["boards"][1]["feeder_length_m"]
MISSING["boards"][1]["loads"].append(
    {
        "description": "DB2 pump",
        "load": "motor",
        "power_kw": "5.5",
        "phases": 3,
        "starter": "dol",
        "power_factor": "0.85",
        "length_m": 20,
    }
)
del MISSING["boards"][0]["loads"][0]["power_factor"]

GUIDED = {"intake": "guided"}


def _typed_feeder(request: dict[str, Any], *, feeds: bool) -> dict[str, Any]:
    out = copy.deepcopy(request)
    row: dict[str, Any] = {
        "description": "Feeder to DB2 (typed)",
        "load": "sub_board",
        "power_kw": "50",
        "phases": 3,
    }
    if feeds:
        row["feeds"] = "DB2"
    out["boards"][0]["loads"].append(row)
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _save(client: TestClient, request: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        "/design/projects", json={"name": "M6a ADV", "request": request, "note": "r1", **extra}
    )


def _saved_id(client: TestClient, request: dict[str, Any], **extra: Any) -> str:
    response = _save(client, request, **extra)
    assert response.status_code == 201, response.text[:600]
    project_id: str = response.json()["id"]
    return project_id


def _open(client: TestClient, project_id: str, number: int | None = None) -> dict[str, Any]:
    params = {} if number is None else {"revision": number}
    response = client.get(f"/design/projects/{project_id}", params=params)
    assert response.status_code == 200, response.text[:600]
    body: dict[str, Any] = response.json()
    return body


def _requirements(opened: dict[str, Any]) -> dict[str, Any]:
    assert "requirements" in opened, "an opened revision carries no requirements record"
    record: dict[str, Any] = opened["requirements"]
    assert record["schema"] == "pp-req/1"
    return record


def _question(record: dict[str, Any], *fields: str, finding: str | None = None) -> dict[str, Any]:
    """The one question whose field names any of ``fields`` (or raises ``finding``)."""
    found = [
        q
        for q in record["questions"]
        if any(f in (q.get("field") or "") for f in fields)
        or (finding is not None and q.get("finding_code") == finding)
    ]
    assert found, (
        fields,
        finding,
        [(q.get("field"), q.get("finding_code")) for q in record["questions"]],
    )
    return found[0]


def _ask(client: TestClient, project_id: str, number: int, qid: str, body: Any) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/questions/{qid}", json=body
    )


def _numbers(client: TestClient, project_id: str) -> list[int]:
    return sorted(r["number"] for r in _open(client, project_id)["revisions"])


def _reviewer(engine: Engine, tenant: str) -> CurrentUser:
    """Another member of the tenant who may approve (the reviewer role)."""
    return review_support.new_account(engine, tenant, role=Role.REVIEWER)


# --------------------------------------------------------------------------- #
# 1. Server-owned feeders, through the saved-project routes
# --------------------------------------------------------------------------- #


def _project_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM design_project_revisions")).scalar_one())


def test_saving_a_client_feeds_is_refused_and_saves_nothing(
    client: TestClient, engine: Engine
) -> None:
    before = _project_count(engine)
    refused = _save(client, _typed_feeder(FED, feeds=True))
    assert refused.status_code == 422, refused.text[:600]
    assert error(refused)["code"] == "feeder_server_owned"
    assert error(refused)["params"] == {"board": "MDB", "load_index": 1}
    assert _project_count(engine) == before


def test_saving_a_sub_board_row_with_fed_from_is_refused(
    client: TestClient, engine: Engine
) -> None:
    before = _project_count(engine)
    refused = _save(client, _typed_feeder(FED, feeds=False))
    assert refused.status_code == 422, refused.text[:600]
    assert error(refused)["code"] == "sub_board_load_server_owned"
    assert error(refused)["params"] == {"board": "MDB", "load_index": 1}
    assert _project_count(engine) == before


def test_a_new_revision_with_a_typed_feeder_is_refused(client: TestClient) -> None:
    project_id = _saved_id(client, FED)
    for feeds, code in ((True, "feeder_server_owned"), (False, "sub_board_load_server_owned")):
        refused = client.post(
            f"/design/projects/{project_id}/revisions",
            json={"request": _typed_feeder(FED, feeds=feeds), "note": "typed"},
        )
        assert refused.status_code == 422, refused.text[:600]
        assert error(refused)["code"] == code
    assert _numbers(client, project_id) == [1]


def test_the_saved_feeder_is_drawn_once_and_traced(client: TestClient) -> None:
    project_id = _saved_id(client, FED, requirements_input=GUIDED)
    opened = _open(client, project_id, 1)
    record = _requirements(opened)
    # One inter-board wiring open item for the one sub-board feeder.
    wiring = [i for i in record["open_items"] if i["code"] == "inter_board_wiring_not_modelled"]
    assert len(wiring) == 1, record["open_items"]
    # Every load and the generated feeder is traced to a design element.
    traced = {t["requirement_ref"] for t in record["trace"]}
    assert {load["ref"] for load in record["loads"]} <= traced
    assert len(record["trace"]) >= len(record["loads"]) + 1
    # The requirements never list the generated feeder as a typed load.
    assert all(
        load.get("source") != "typed" or "Feeder to" not in load["description"]
        for load in record["loads"]
    )
    assert record.get("derived") is not True


# --------------------------------------------------------------------------- #
# 2. Missing inputs: questions, answers, acknowledgements
# --------------------------------------------------------------------------- #


def test_each_missing_value_and_the_default_power_factor_is_a_question(
    client: TestClient,
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    record = _requirements(_open(client, project_id, 1))
    fault = _question(record, "fault_level")
    ze = _question(record, "earth_loop", "ze")
    feeder = _question(record, "feeder_length")
    motor = _question(record, "nameplate")
    pf = _question(record, "power_factor", finding="default_power_factor")
    assert {q["kind"] for q in (fault, ze, feeder, motor)} == {"missing_value"}
    assert pf["kind"] == "assumption"
    assert pf["finding_code"] == "default_power_factor"
    every = (fault, ze, feeder, motor, pf)
    assert len({q["id"] for q in every}) == 5
    assert all(q["state"] == "open" and q["by_user_id"] is None and q["at"] is None for q in every)
    # The designer is unchanged: the 0.9 default still applied.
    assert any(
        f["code"] == "default_power_factor" for f in _open(client, project_id, 1)["findings"]
    )


def test_a_complete_project_asks_no_missing_value(client: TestClient) -> None:
    project_id = _saved_id(client, FED, requirements_input=GUIDED)
    record = _requirements(_open(client, project_id, 1))
    assert [q for q in record["questions"] if q["kind"] == "missing_value"] == []


def test_answering_a_question_changes_the_request_in_a_new_revision(
    client: TestClient, engine: Engine
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    record = _requirements(_open(client, project_id, 1))
    fault = _question(record, "fault_level")
    others = {q["id"] for q in record["questions"] if q["id"] != fault["id"]}
    before = revision_row(engine, project_id, 1)
    answered = _ask(client, project_id, 1, fault["id"], {"action": "answer", "value": 15})
    assert answered.status_code in (200, 201), answered.text[:600]
    assert _numbers(client, project_id) == [1, 2]
    # Revision 1 is untouched; revision 2 has the value as typed input.
    assert revision_row(engine, project_id, 1)["request"] == before["request"]
    new = _open(client, project_id, 2)
    assert float(new["request"]["boards"][0]["supply"]["fault_level_ka"]) == 15
    renewed = _requirements(new)
    # Answered: no longer an open missing value; the other questions keep their ids.
    assert all(q["state"] != "open" for q in renewed["questions"] if q["id"] == fault["id"])
    assert others <= {q["id"] for q in renewed["questions"]}


def test_answering_the_power_factor_assumption_types_the_value(client: TestClient) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    pf = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    answered = _ask(client, project_id, 1, pf["id"], {"action": "answer", "value": "0.8"})
    assert answered.status_code in (200, 201), answered.text[:600]
    latest = _open(client, project_id, max(_numbers(client, project_id)))
    assert float(latest["request"]["boards"][0]["loads"][0]["power_factor"]) == 0.8
    assert not any(f["code"] == "default_power_factor" for f in latest["findings"])


def test_acknowledging_records_the_server_user_and_clock_and_keeps_the_design(
    client: TestClient, engine: Engine, caller: _Caller
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    pf = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    sha = revision_row(engine, project_id, 1)["snapshot_sha256"]
    start = datetime.now(UTC) - timedelta(seconds=5)
    done = _ask(client, project_id, 1, pf["id"], {"action": "acknowledge"})
    assert done.status_code in (200, 201), done.text[:600]
    end = datetime.now(UTC) + timedelta(seconds=5)
    assert _numbers(client, project_id) == [1]
    assert revision_row(engine, project_id, 1)["snapshot_sha256"] == sha
    acked = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    assert acked["state"] == "acknowledged"
    assert acked["by_user_id"] == caller.user.id
    at = datetime.fromisoformat(acked["at"].replace("Z", "+00:00"))
    assert start <= at <= end


@pytest.mark.parametrize(
    "forged",
    [
        {"by_user_id": "00000000-0000-0000-0000-000000000001"},
        {"at": "2001-01-01T00:00:00Z"},
        {"by_user_id": "00000000-0000-0000-0000-000000000001", "at": "2001-01-01T00:00:00Z"},
    ],
)
def test_a_client_naming_who_or_when_is_refused(client: TestClient, forged: dict[str, Any]) -> None:
    """Specified: refused (422), not silently ignored, and nothing is recorded."""
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    pf = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    refused = _ask(client, project_id, 1, pf["id"], {"action": "acknowledge", **forged})
    assert refused.status_code == 422, refused.text[:600]
    still = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    assert still["state"] == "open"
    assert still["by_user_id"] is None
    assert still["at"] is None


def test_an_unknown_question_or_action_is_refused(client: TestClient) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    pf = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    # The question endpoint exists for a real question ...
    assert _ask(client, project_id, 1, pf["id"], {"action": "acknowledge"}).status_code in (
        200,
        201,
    )
    # ... and refuses a made-up one, and an unknown action.
    assert _ask(client, project_id, 1, "q-forged", {"action": "acknowledge"}).status_code == 404
    assert _ask(client, project_id, 1, pf["id"], {"action": "approve"}).status_code == 422


def test_an_acknowledged_assumption_prints_as_not_engineer_validated(
    client: TestClient,
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    pf = _question(
        _requirements(_open(client, project_id, 1)), "power_factor", finding="default_power_factor"
    )
    assert _ask(client, project_id, 1, pf["id"], {"action": "acknowledge"}).status_code in (
        200,
        201,
    )
    exported = revision_export(client, project_id, 1, "pdf")
    assert exported.status_code == 200, exported.text[:600]
    words = " ".join(" ".join(pdf_pages(exported.content)).split())
    assert "Assumption acknowledged by" in words
    assert "not engineer-validated" in words


def test_an_older_revision_gets_a_derived_record_that_never_counts_as_acknowledged(
    client: TestClient, engine: Engine
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    update_row(engine, project_id, 1, requirements=None)
    record = _requirements(_open(client, project_id, 1))
    assert record["derived"] is True
    assert all(q["state"] != "acknowledged" for q in record["questions"])


# --------------------------------------------------------------------------- #
# 3. Self-approval
# --------------------------------------------------------------------------- #


def test_the_author_cannot_approve_their_own_revision(client: TestClient, engine: Engine) -> None:
    saved = save(client)
    refused = approve(client, saved["id"], 1)
    assert refused.status_code == 403, refused.text[:600]
    assert error(refused)["code"] == "approval_by_author"
    assert revision_row(engine, saved["id"], 1)["approved_at"] is None


def test_the_author_is_matched_by_account_not_by_the_emails_case(
    client: TestClient, engine: Engine, caller: _Caller
) -> None:
    saved = save(client)
    caller.user = CurrentUser(
        id=caller.user.id,
        email=caller.user.email.upper(),
        tenant_id=caller.user.tenant_id,
        roles=caller.user.roles,
    )
    refused = approve(client, saved["id"], 1)
    assert refused.status_code == 403, refused.text[:600]
    assert error(refused)["code"] == "approval_by_author"


def test_another_member_with_approve_permission_approves(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = save(client)
    caller.user = _reviewer(engine, tenants[0])
    approved = approve(client, saved["id"], 1)
    assert approved.status_code == 200, approved.text[:600]
    row = revision_row(engine, saved["id"], 1)
    assert row["approved_account"] == caller.user.email
    assert str(row["approved_user_id"]) == caller.user.id
    assert "author_unknown" not in json.dumps(approved.json())


def test_a_revision_with_no_recorded_author_approves_without_inventing_one(
    client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    update_row(engine, saved["id"], 1, author="")
    approved = approve(client, saved["id"], 1)
    assert approved.status_code == 200, approved.text[:600]
    assert "author_unknown" in json.dumps(approved.json())
    assert revision_row(engine, saved["id"], 1)["author"] == ""
    listed = next(r for r in _open(client, saved["id"], 1)["revisions"] if r["number"] == 1)
    assert listed["author"] == ""


# --------------------------------------------------------------------------- #
# 4. Duplicate designations (v2: a component's printed designation)
# --------------------------------------------------------------------------- #


def _duplicated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/2")
    configuration = _configuration(CASE)
    first, second = configuration["components"][:2]
    second["designation"] = copy.deepcopy(first["designation"])
    project_id = _save_configured(client)
    saved = _revise(client, project_id, configuration, CASE)
    assert saved.status_code == 201, saved.text[:600]
    return project_id


def test_a_duplicate_designation_is_blocking_refuses_approval_and_is_exported(
    client: TestClient,
    engine: Engine,
    caller: _Caller,
    tenants: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = _duplicated(client, monkeypatch)
    row = revision_row(engine, project_id, 2)
    found = [f for f in row["findings"] if f["code"] == "designation_duplicate"]
    assert found, [f["code"] for f in row["findings"]]
    assert {f["severity"] for f in found} == {"blocking"}
    caller_author = caller.user
    caller.user = _reviewer(engine, tenants[0])
    refused = approve(client, project_id, 2)
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "revision_has_blocking_findings"
    assert "designation_duplicate" in error(refused)["params"]["codes"]
    assert revision_row(engine, project_id, 2)["approved_at"] is None
    caller.user = caller_author
    exported = revision_export(client, project_id, 2, "pdf")
    assert exported.status_code == 200, exported.text[:600]
    assert "designation_duplicate" in " ".join(" ".join(pdf_pages(exported.content)).split())


# --------------------------------------------------------------------------- #
# 5. Tenant isolation
# --------------------------------------------------------------------------- #


def test_another_tenant_cannot_read_ask_or_approve(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _saved_id(client, MISSING, requirements_input=GUIDED)
    record = _requirements(_open(client, project_id, 1))
    pf = _question(record, "power_factor", finding="default_power_factor")
    fault = _question(record, "fault_level")
    caller.user = review_support.new_account(engine, tenants[1], owner=True)
    assert client.get(f"/design/projects/{project_id}").status_code == 404
    assert _ask(client, project_id, 1, pf["id"], {"action": "acknowledge"}).status_code == 404
    assert (
        _ask(client, project_id, 1, fault["id"], {"action": "answer", "value": 20}).status_code
        == 404
    )
    assert approve(client, project_id, 1).status_code == 404
    preview = client.post(
        "/design/projects/requirements/preview",
        json={"request": MISSING, "requirements_input": GUIDED},
    )
    assert preview.status_code == 200  # a preview saves nothing and reads no project
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM design_project_revisions WHERE project_id = :p"),
                {"p": project_id},
            ).scalar_one()
            == 1
        )
    revision = revision_row(engine, project_id, 1)
    assert revision["approved_at"] is None
    assert "acknowledged" not in json.dumps(revision.get("requirements"))


def test_the_preview_saves_nothing(client: TestClient, engine: Engine) -> None:
    before = _project_count(engine)
    preview = client.post(
        "/design/projects/requirements/preview",
        json={"request": MISSING, "requirements_input": GUIDED},
    )
    assert preview.status_code == 200, preview.text[:600]
    assert preview.json()["requirements"]["schema"] == "pp-req/1"
    assert _project_count(engine) == before


# --------------------------------------------------------------------------- #
# 6. History
# --------------------------------------------------------------------------- #


def test_a_historical_sub_board_row_opens_unchanged_and_keeps_its_approval(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    """A row saved before M6a: its request holds a typed sub_board feeder.

    Written straight into the row (the request is what the engineer entered;
    the stored snapshot is what was designed and approved).
    """
    project_id = _saved_id(client, FED)
    author = caller.user
    caller.user = _reviewer(engine, tenants[0])
    assert approve(client, project_id, 1).status_code == 200
    caller.user = author
    legacy = _typed_feeder(FED, feeds=True)
    update_row(engine, project_id, 1, request=legacy)
    row = revision_row(engine, project_id, 1)
    opened = _open(client, project_id, 1)
    after = revision_row(engine, project_id, 1)
    assert after["snapshot_sha256"] == row["snapshot_sha256"]
    assert snapshot.sha256_of(after["design_snapshot"]) == row["snapshot_sha256"]
    assert after["design_snapshot"] == row["design_snapshot"]
    assert after["request"] == legacy
    assert opened["approval_current"] is True, opened.get("approval_changed")
    assert opened["validated"] is True
    # Re-designing it (a new revision from that request) is refused.
    refused = client.post(
        f"/design/projects/{project_id}/revisions", json={"request": legacy, "note": "again"}
    )
    assert refused.status_code == 422
    assert error(refused)["code"] == "feeder_server_owned"


# --------------------------------------------------------------------------- #
# 7. Existing restrictions
# --------------------------------------------------------------------------- #


def test_execution_and_restricted_formats_stay_refused_after_an_approval(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _saved_id(client, FED)
    author = caller.user
    caller.user = _reviewer(engine, tenants[0])
    assert approve(client, project_id, 1).status_code == 200
    caller.user = author
    execution = revision_export(client, project_id, 1, "pdf", purpose="execution")
    assert execution.status_code in (409, 422), execution.text[:300]
    for fmt in BLOCKED_FORMATS:
        refused = revision_export(client, project_id, 1, fmt)
        assert refused.status_code in (409, 422), (fmt, refused.status_code)
        assert error(refused)["code"] == "export_format_unprotected", fmt
