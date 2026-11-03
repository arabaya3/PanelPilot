"""M6a against Postgres: the requirements record, its questions, self-approval and duplicates.

Synthetic data only. Uses the export-integrity fixtures (two tenants, a real
owner account as the caller).
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.models.schemas.auth import Role
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    REQUEST,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    approve,
    error,
    pdf_pages,
    pytestmark,
    revision_export,
    revision_row,
    update_row,
)
from app.tests.domain import review_support

#: A main board without fault level or Ze, a sub-board without feeder length,
#: a load without power factor, and a controlled motor.
GUIDED: dict[str, Any] = {
    "info": {"name": "M6a", "number": "M6A-1"},
    "boards": [
        {
            "name": "MDB",
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {"description": "Lights", "load": "lighting", "power_kw": "1", "length_m": 10},
                {
                    "description": "Pump",
                    "load": "motor",
                    "power_kw": "5.5",
                    "phases": 3,
                    "starter": "dol",
                    "controlled": True,
                    "length_m": 10,
                },
            ],
        },
        {
            "name": "DB-1",
            "fed_from": "MDB",
            "supply": {"voltage_v": 400, "phases": 3},
            "loads": [
                {
                    "description": "Sockets",
                    "load": "socket",
                    "power_kw": "2",
                    "power_factor": "0.95",
                    "length_m": 10,
                }
            ],
        },
    ],
}
INPUT = {
    "intake": "guided",
    "control": [{"board_index": 0, "load_index": 1, "needs": ["start_stop"]}],
}


def _save(client: TestClient, request: dict[str, Any], **extra: Any) -> dict[str, Any]:
    saved = client.post(
        "/design/projects", json={"name": "M6a", "request": request, "note": "r1", **extra}
    )
    assert saved.status_code == 201, saved.text[:600]
    body: dict[str, Any] = saved.json()
    return body


def _question(record: dict[str, Any], field: str) -> dict[str, Any]:
    return next(q for q in record["questions"] if q["field"] == field)


def _ask(client: TestClient, project_id: str, number: int, qid: str, body: Any) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/questions/{qid}", json=body
    )


def test_a_saved_revision_stores_its_record_under_its_hash(
    client: TestClient, engine: Engine
) -> None:
    saved = _save(client, GUIDED, requirements_input=INPUT)
    record = saved["requirements"]
    assert record["schema"] == "pp-req/1"
    assert record["derived"] is False
    assert record["control"][0]["needs"] == ["start_stop"]
    codes = sorted(item["code"] for item in record["open_items"])
    assert codes == ["control_not_designed", "inter_board_wiring_not_modelled"]
    row = revision_row(engine, saved["id"], 1)
    assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])
    # Altered outside the application: the revision fails integrity.
    altered = copy.deepcopy(row["requirements"])
    altered["intake"] = "import"
    update_row(engine, saved["id"], 1, requirements=altered)
    opened = client.get(f"/design/projects/{saved['id']}").json()
    assert opened["validated"] is False
    assert opened["requirements"] is None
    assert any(f["values"].get("reason") == "requirements_mismatch" for f in opened["findings"])


def test_control_needs_on_an_uncontrolled_load_are_refused(client: TestClient) -> None:
    given = {"control": [{"board_index": 0, "load_index": 0, "needs": ["start_stop"]}]}
    refused = client.post(
        "/design/projects",
        json={"name": "M6a", "request": GUIDED, "requirements_input": given},
    )
    assert refused.status_code == 422
    assert error(refused)["code"] == "control_need_requires_controlled"


def test_answer_makes_a_new_revision_and_acknowledge_records_who_and_when(
    client: TestClient, engine: Engine, caller: _Caller
) -> None:
    saved = _save(client, GUIDED, requirements_input=INPUT)
    record = saved["requirements"]
    fault = _question(record, "fault_level_ka")
    answered = _ask(client, saved["id"], 1, fault["id"], {"action": "answer", "value": "12"})
    assert answered.status_code == 201, answered.text[:600]
    body = answered.json()
    assert body["revision"] == 2
    assert body["request"]["boards"][0]["supply"]["fault_level_ka"] == "12"
    kept = next(q for q in body["requirements"]["questions"] if q["id"] == fault["id"])
    assert kept["state"] == "answered"
    assert kept["by_user_id"] == caller.user.id
    # The control needs carried over to the new revision.
    assert body["requirements"]["control"][0]["needs"] == ["start_stop"]

    motor = _question(body["requirements"], "motor_current_a")
    not_answerable = _ask(client, saved["id"], 2, motor["id"], {"action": "answer", "value": 9})
    assert not_answerable.status_code == 409
    assert error(not_answerable)["code"] == "question_not_answerable"
    sha = revision_row(engine, saved["id"], 2)["snapshot_sha256"]
    done = _ask(client, saved["id"], 2, motor["id"], {"action": "acknowledge"})
    assert done.status_code == 200, done.text[:600]
    acked = next(q for q in done.json()["requirements"]["questions"] if q["id"] == motor["id"])
    assert acked["state"] == "acknowledged"
    assert acked["by_user_id"] == caller.user.id
    assert acked["at"]
    assert revision_row(engine, saved["id"], 2)["snapshot_sha256"] == sha
    forged = _ask(client, saved["id"], 2, motor["id"], {"action": "acknowledge", "at": "2001"})
    assert forged.status_code == 422

    words = " ".join(
        " ".join(pdf_pages(revision_export(client, saved["id"], 2, "pdf").content)).split()
    )
    assert "Assumption acknowledged by" in words
    assert "not engineer-validated" in words


def test_a_derived_record_cannot_be_acknowledged(client: TestClient, engine: Engine) -> None:
    saved = _save(client, GUIDED)
    update_row(engine, saved["id"], 1, requirements=None, requirements_sha256=None)
    record = client.get(f"/design/projects/{saved['id']}").json()["requirements"]
    assert record["derived"] is True
    pf = _question(record, "power_factor")
    refused = _ask(client, saved["id"], 1, pf["id"], {"action": "acknowledge"})
    assert refused.status_code == 409
    assert error(refused)["code"] == "requirements_derived"


def test_the_author_may_not_approve_but_the_owner_approves_another_members_revision(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    owner = caller.user
    member = review_support.new_account(engine, tenants[0])
    caller.user = member
    saved = _save(client, REQUEST)
    assert saved["is_author"] is True
    # The member may not approve anyway; a reviewer who authored it may not either.
    reviewer = review_support.new_account(engine, tenants[0], role=Role.REVIEWER)
    caller.user = reviewer
    own = _save(client, REQUEST)
    refused = approve(client, own["id"], 1)
    assert refused.status_code == 403
    assert error(refused)["code"] == "approval_by_author"
    caller.user = owner
    opened = client.get(f"/design/projects/{saved['id']}").json()
    assert opened["is_author"] is False
    approved = approve(client, saved["id"], 1)
    assert approved.status_code == 200, approved.text[:600]
    assert approved.json()["author_unknown"] is False
    # Owners included: the owner's own revision is refused.
    mine = _save(client, REQUEST)
    assert error(approve(client, mine["id"], 1))["code"] == "approval_by_author"
    # No recorded author: approved, said, and none invented.
    update_row(engine, mine["id"], 1, author="")
    unknown = approve(client, mine["id"], 1)
    assert unknown.status_code == 200, unknown.text[:600]
    assert unknown.json()["author_unknown"] is True
    assert revision_row(engine, mine["id"], 1)["author"] == ""


def test_a_typed_feeder_in_a_stored_revision_opens_but_is_not_re_designed(
    client: TestClient, engine: Engine
) -> None:
    saved = _save(client, GUIDED)
    legacy = copy.deepcopy(GUIDED)
    legacy["boards"][0]["loads"].append(
        {"description": "Feeder", "load": "sub_board", "power_kw": "5", "phases": 3}
    )
    update_row(engine, saved["id"], 1, request=legacy)
    opened = client.get(f"/design/projects/{saved['id']}").json()
    assert opened["validated"] is True
    revised = client.post(
        f"/design/projects/{saved['id']}/revisions", json={"request": legacy, "note": "again"}
    )
    assert revised.status_code == 422
    assert error(revised)["code"] == "sub_board_load_server_owned"
    assert error(revised)["params"] == {"board": "MDB", "load_index": "2"}
