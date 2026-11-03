"""M6b against Postgres: approval needs an established author; re-save recovers.

An author is established only by the saving account's id
(``author_user_id``). A row without it (saved before the column, or by a
trial) is refused ``approval_author_not_established`` whatever email it
records; it is recovered by ``POST …/revisions/{n}/resave``, saved as the
caller's new revision for another member to approve. No stored row is
ever rewritten.

``pre_m6a_revisions.json`` holds ``design_project_revisions`` rows exactly
as the pre-M6a code (a829707) wrote them, from synthetic data; the ids, the
tenant, the project, the author's email and the approver are substituted
when they are inserted.

Synthetic data only.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.models.schemas.auth import CurrentUser, Role
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
    pytestmark,
    revision_export,
    revision_row,
    update_row,
)
from app.tests.domain import review_support
from app.tests.domain.test_m6a_requirements_db import GUIDED, INPUT

LEGACY: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "pre_m6a_revisions.json").read_text()
)["rows"]

#: Every column a pre-M6a row holds: none of them may change.
OLD_COLUMNS = sorted(LEGACY["approved"])


def _reviewer(engine: Engine, tenant: str) -> CurrentUser:
    return review_support.new_account(engine, tenant, role=Role.REVIEWER)


def _insert_legacy(
    engine: Engine, tenant: str, shape: str, *, author: str, approver: CurrentUser | None = None
) -> str:
    """A project holding one revision exactly as the pre-M6a code saved it."""
    row = dict(LEGACY[shape])
    if row["author"] == "{author}":
        row["author"] = author
    if row["approved_user_id"] == "{approver_id}":
        assert approver is not None
        row["approved_user_id"] = uuid.UUID(approver.id)
        row["approved_account"] = approver.email
    project = uuid.uuid4()
    for key in ("design_snapshot", "request", "findings"):
        row[key] = json.dumps(row[key]) if row[key] is not None else None
    columns = ", ".join(["id", "tenant_id", "project_id", *row])
    values = ", ".join(
        [":id", ":tenant_id", ":project_id"]
        + [
            f"CAST(:{k} AS JSONB)" if k in ("design_snapshot", "request", "findings") else f":{k}"
            for k in row
        ]
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO design_projects (id, tenant_id, name, revision_count, created_at, "
                "updated_at) VALUES (:i, :t, :n, 1, now(), now())"
            ),
            {"i": project, "t": uuid.UUID(tenant), "n": f"Historic {shape}"},
        )
        conn.execute(
            text(f"INSERT INTO design_project_revisions ({columns}) VALUES ({values})"),
            {"id": uuid.uuid4(), "tenant_id": uuid.UUID(tenant), "project_id": project, **row},
        )
    return str(project)


def _old(engine: Engine, project_id: str) -> dict[str, Any]:
    row = revision_row(engine, project_id, 1)
    return {k: row[k] for k in [*OLD_COLUMNS, "author_user_id", "requirements"]}


def _count(engine: Engine, project_id: str) -> tuple[int, int]:
    with engine.connect() as conn:
        held: int = conn.execute(
            text("SELECT revision_count FROM design_projects WHERE id = :p"),
            {"p": uuid.UUID(project_id)},
        ).scalar_one()
        rows: int = conn.execute(
            text("SELECT count(*) FROM design_project_revisions WHERE project_id = :p"),
            {"p": uuid.UUID(project_id)},
        ).scalar_one()
    return held, rows


def _resave(client: TestClient, project_id: str, number: int) -> Any:
    return client.post(f"/design/projects/{project_id}/revisions/{number}/resave")


def _saved(client: TestClient, request: dict[str, Any], **extra: Any) -> dict[str, Any]:
    saved = client.post(
        "/design/projects", json={"name": "M6b", "request": request, "note": "r1", **extra}
    )
    assert saved.status_code == 201, saved.text[:600]
    body: dict[str, Any] = saved.json()
    return body


# --------------------------------------------------------------------------- #
# Historical rows, as the old code wrote them
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", ["approved", "feeds", "sub_board", "blank_author"])
def test_a_pre_m6a_row_opens_stale_unestablished_and_refuses_approval_and_export(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str], shape: str
) -> None:
    author = _reviewer(engine, tenants[0])
    project_id = _insert_legacy(
        engine, tenants[0], shape, author=author.email, approver=caller.user
    )
    before = _old(engine, project_id)
    assert before["author_user_id"] is None
    assert snapshot.sha256_of(before["design_snapshot"]) == before["snapshot_sha256"]
    for viewer in (caller.user, author):
        caller.user = viewer
        opened = client.get(f"/design/projects/{project_id}").json()
        assert opened["export_status"] == "stale"
        assert opened["validated"] is True
        assert opened["author_established"] is False
        assert opened["is_author"] is False
        assert opened["author_unknown"] is (shape == "blank_author")
        assert opened["revisions"][0]["author_established"] is False
        assert opened["requirements"]["derived"] is True
        if shape == "approved":
            assert opened["approval_current"] is False
            assert opened["approval_changed"] == ["rules_version", "renderer_version"]
    exported = revision_export(client, project_id, 1, "pdf")
    assert exported.status_code == 409
    assert error(exported)["code"] == "revision_stale"
    caller.user = _reviewer(engine, tenants[0])
    refused = approve(client, project_id, 1)
    assert refused.status_code == 409
    expected = "revision_already_approved" if shape == "approved" else "revision_stale"
    assert error(refused)["code"] == expected
    assert _old(engine, project_id) == before


def test_a_pre_m6a_row_not_stale_is_refused_for_its_author_not_being_established(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    """With only the author missing (a current design), the author is the refusal."""
    author = _reviewer(engine, tenants[0])
    caller.user = author
    saved = _saved(client, REQUEST)
    for recorded in ("", author.email, "someone-else@synthetic.example"):
        update_row(engine, saved["id"], 1, author=recorded, author_user_id=None)
        for approver in (author, _reviewer(engine, tenants[0])):
            caller.user = approver
            refused = approve(client, saved["id"], 1)
            assert refused.status_code == 409, (recorded, refused.text[:300])
            assert error(refused)["code"] == "approval_author_not_established"
            assert error(refused)["params"] == {"revision": "1"}
    assert revision_row(engine, saved["id"], 1)["approved_at"] is None


def test_the_resave_recovery_end_to_end_leaves_the_old_row_untouched(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    owner = caller.user
    author = _reviewer(engine, tenants[0])
    project_id = _insert_legacy(engine, tenants[0], "approved", author=author.email, approver=owner)
    before = _old(engine, project_id)
    caller.user = author
    resaved = _resave(client, project_id, 1)
    assert resaved.status_code == 201, resaved.text[:600]
    body = resaved.json()
    assert body["revision"] == 2
    assert body["revisions"][-1]["note"] == "Re-saved from revision 1 for independent review"
    assert body["author_established"] is True
    assert body["is_author"] is True
    assert body["export_status"] == "draft"
    assert body["requirements"]["resaved_from"] == 1
    new = revision_row(engine, project_id, 2)
    assert str(new["author_user_id"]) == author.id
    assert new["request"] == before["request"]
    assert new["requirements"]["resaved_from"] == 1
    assert new["requirements_sha256"] == snapshot.sha256_of(new["requirements"])
    # Exports as a draft until approved; its author may not approve it.
    assert revision_export(client, project_id, 2, "pdf").status_code == 200
    refused = approve(client, project_id, 2)
    assert refused.status_code == 403
    assert error(refused)["code"] == "approval_by_author"
    caller.user = owner
    approved = approve(client, project_id, 2, note="independent review")
    assert approved.status_code == 200, approved.text[:600]
    assert approved.json()["export_status"] == "review_approved"
    assert revision_export(client, project_id, 2, "pdf").status_code == 200
    # Revision 1: its snapshot, hashes and approval exactly as recorded.
    assert _old(engine, project_id) == before
    still = client.get(f"/design/projects/{project_id}", params={"revision": 1}).json()
    assert still["export_status"] == "stale"


@pytest.mark.parametrize(
    ("shape", "code"),
    [("feeds", "feeder_server_owned"), ("sub_board", "sub_board_load_server_owned")],
)
def test_resaving_a_typed_feeder_is_refused_unchanged_and_saves_nothing(
    client: TestClient,
    engine: Engine,
    caller: _Caller,
    tenants: tuple[str, str],
    shape: str,
    code: str,
) -> None:
    author = _reviewer(engine, tenants[0])
    project_id = _insert_legacy(engine, tenants[0], shape, author=author.email)
    before = _old(engine, project_id)
    caller.user = author
    refused = _resave(client, project_id, 1)
    assert refused.status_code == 422, refused.text[:300]
    assert error(refused)["code"] == code
    assert error(refused)["params"] == {"board": "MDB", "load_index": "1"}
    assert _count(engine, project_id) == (1, 1)
    assert _old(engine, project_id) == before


def test_another_tenant_gets_not_found_on_resave_and_approve(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _insert_legacy(engine, tenants[0], "blank_author", author="")
    before = _old(engine, project_id)
    caller.user = review_support.new_account(engine, tenants[1], owner=True)
    for refused in (_resave(client, project_id, 1), approve(client, project_id, 1)):
        assert refused.status_code == 404
        assert error(refused)["code"] == "revision_not_found"
    caller.user = review_support.new_account(engine, tenants[0])
    missing = _resave(client, project_id, 9)
    assert missing.status_code == 404
    assert error(missing)["code"] == "revision_not_found"
    assert _count(engine, project_id) == (1, 1)
    assert _old(engine, project_id) == before


def test_a_trial_cannot_resave_or_establish_an_author(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _insert_legacy(engine, tenants[0], "blank_author", author="")
    trial = CurrentUser(id=str(uuid.uuid4()), email="", tenant_id=tenants[0], roles=frozenset())
    caller.user = trial
    refused = _resave(client, project_id, 1)
    assert refused.status_code == 403
    assert error(refused)["code"] == "resave_requires_account"
    # A trial's own save records no account id: its subject is an anonymous
    # session, and the account that claims the tenant is another id.
    saved = _saved(client, REQUEST)
    assert revision_row(engine, saved["id"], 1)["author_user_id"] is None
    assert saved["author_established"] is False
    caller.user = review_support.new_account(engine, tenants[0], owner=True)
    refused = approve(client, saved["id"], 1)
    assert error(refused)["code"] == "approval_author_not_established"


# --------------------------------------------------------------------------- #
# Who the author is: by account id alone
# --------------------------------------------------------------------------- #


def test_self_approval_is_refused_owners_included(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = _saved(client, REQUEST)
    refused = approve(client, saved["id"], 1)
    assert refused.status_code == 403
    assert error(refused) == {**error(refused), "code": "approval_by_author"}
    assert error(refused)["params"] == {"revision": "1"}


def test_a_renamed_author_is_still_the_author_and_a_reused_email_is_not(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    author = _reviewer(engine, tenants[0])
    caller.user = author
    saved = _saved(client, REQUEST)
    old_email = author.email
    renamed = "renamed-" + old_email
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET email = :e WHERE id = :i"),
            {"e": renamed, "i": uuid.UUID(author.id)},
        )
    caller.user = author.model_copy(update={"email": renamed})
    refused = approve(client, saved["id"], 1)
    assert error(refused)["code"] == "approval_by_author"
    taker = _reviewer(engine, tenants[0])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET email = :e WHERE id = :i"),
            {"e": old_email, "i": uuid.UUID(taker.id)},
        )
    caller.user = taker.model_copy(update={"email": old_email})
    assert client.get(f"/design/projects/{saved['id']}").json()["is_author"] is False
    assert approve(client, saved["id"], 1).status_code == 200


@pytest.mark.parametrize("fate", ["deleted", "anonymised"])
def test_a_deleted_or_anonymised_author_stays_established(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str], fate: str
) -> None:
    """``author_user_id`` has no foreign key: the id stays, so does the refusal of it."""
    author = review_support.new_account(engine, tenants[0])
    caller.user = author
    saved = _saved(client, REQUEST)
    with engine.begin() as conn:
        if fate == "deleted":
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": uuid.UUID(author.id)})
        else:
            conn.execute(
                text("UPDATE users SET email = :e, full_name = NULL WHERE id = :i"),
                {"e": f"anon-{uuid.uuid4().hex}@synthetic.example", "i": uuid.UUID(author.id)},
            )
    row = revision_row(engine, saved["id"], 1)
    assert str(row["author_user_id"]) == author.id
    caller.user = _reviewer(engine, tenants[0])
    opened = client.get(f"/design/projects/{saved['id']}").json()
    assert (opened["author_established"], opened["is_author"]) == (True, False)
    approved = approve(client, saved["id"], 1)
    assert approved.status_code == 200, approved.text[:300]


def test_resave_by_the_author_then_approval_by_them_is_refused(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    author = _reviewer(engine, tenants[0])
    project_id = _insert_legacy(engine, tenants[0], "blank_author", author="")
    caller.user = author
    assert _resave(client, project_id, 1).status_code == 201
    refused = approve(client, project_id, 2)
    assert refused.status_code == 403
    assert error(refused)["code"] == "approval_by_author"
    # Another reviewer re-saving makes them the author: the first may approve now.
    caller.user = _reviewer(engine, tenants[0])
    assert _resave(client, project_id, 2).status_code == 201
    caller.user = author
    assert approve(client, project_id, 3).status_code == 200


def test_resave_carries_control_needs_and_acknowledgements_as_recorded(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    owner = caller.user
    caller.user = review_support.new_account(engine, tenants[0])
    saved = _saved(client, GUIDED, requirements_input=INPUT)
    question = next(q for q in saved["requirements"]["questions"] if not q["answerable"])
    caller.user = owner
    acked = client.post(
        f"/design/projects/{saved['id']}/revisions/1/questions/{question['id']}",
        json={"action": "acknowledge"},
    )
    assert acked.status_code == 200, acked.text[:300]
    recorded = acked.json()["requirements"]["acknowledgements"]
    # As a row saved before authors were recorded by id.
    update_row(engine, saved["id"], 1, author_user_id=None)
    before = revision_row(engine, saved["id"], 1)
    caller.user = _reviewer(engine, tenants[0])
    resaved = _resave(client, saved["id"], 1)
    assert resaved.status_code == 201, resaved.text[:600]
    record = resaved.json()["requirements"]
    assert record["acknowledgements"] == recorded
    carried = next(q for q in record["questions"] if q["id"] == question["id"])
    assert carried["state"] == "acknowledged"
    assert (carried["by_user_id"], carried["at"]) == (owner.id, recorded[0]["at"])
    assert record["control"][0]["needs"] == ["start_stop"]
    assert record["intake"] == "guided"
    assert record["resaved_from"] == 1
    assert revision_row(engine, saved["id"], 1) == before


def test_a_configured_revision_is_not_resaved_without_its_configuration(
    client: TestClient, engine: Engine, caller: _Caller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configuration lives only in the snapshot: a re-save of the request would drop it."""
    from app.tests.domain.test_design_projects_configured_db import _configuration, _request

    monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", "pp-design/2")
    saved = _saved(client, _request())
    revised = client.post(
        f"/design/projects/{saved['id']}/revisions",
        json={"request": _request(), "note": "configured", "configuration": _configuration()},
    )
    assert revised.status_code == 201, revised.text[:500]
    update_row(engine, saved["id"], 2, author_user_id=None)
    before = revision_row(engine, saved["id"], 2)
    refused = _resave(client, saved["id"], 2)
    assert refused.status_code == 409, refused.text[:300]
    assert error(refused)["code"] == "revision_not_validated"
    assert error(refused)["params"]["reason"] == "configuration_not_carried"
    assert _count(engine, saved["id"]) == (2, 2)
    assert revision_row(engine, saved["id"], 2) == before
    # Revision 1 (no configuration) re-saves.
    update_row(engine, saved["id"], 1, author_user_id=None)
    assert _resave(client, saved["id"], 1).status_code == 201
