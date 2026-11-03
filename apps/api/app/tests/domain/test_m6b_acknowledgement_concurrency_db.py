"""M6b against Postgres: acknowledgements, answers and approvals that race each other.

Each race is forced, not left to timing: an outside connection holds the
project row (``SELECT ... FOR UPDATE``), the competing requests are started
one by one and each is seen waiting on that lock in ``pg_stat_activity``
before the next starts, then the lock is released. Postgres grants a row
lock to its waiters in the order they queued, so the order is the test's.

Synthetic data only. Uses the export-integrity fixtures.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.design import snapshot
from app.models.schemas.auth import CurrentUser, Role
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
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
from app.tests.domain.test_m6a_requirements_db import GUIDED, INPUT

#: How long a started request may take to reach the lock it queues on.
_QUEUE_DEADLINE_S = 20.0


def _save(client: TestClient) -> dict[str, Any]:
    saved = client.post(
        "/design/projects",
        json={"name": "M6b", "request": GUIDED, "note": "r1", "requirements_input": INPUT},
    )
    assert saved.status_code == 201, saved.text[:600]
    body: dict[str, Any] = saved.json()
    return body


def _qid(record: dict[str, Any], field: str) -> str:
    return str(next(q for q in record["questions"] if q["field"] == field)["id"])


def _ask(client: TestClient, project_id: str, number: int, qid: str, body: Any) -> Any:
    return client.post(
        f"/design/projects/{project_id}/revisions/{number}/questions/{qid}", json=body
    )


def _ack(client: TestClient, project_id: str, number: int, qid: str) -> Any:
    return _ask(client, project_id, number, qid, {"action": "acknowledge"})


def _stored(engine: Engine, project_id: str, number: int) -> dict[str, Any]:
    row = revision_row(engine, project_id, number)
    # What is stored hashes to the hash stored with it, always.
    assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])
    record: dict[str, Any] = row["requirements"]
    return record


def _waiting(engine: Engine) -> int:
    """How many backends of this database wait on a lock now."""
    with engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar_one()
    return int(found)


@contextmanager
def _project_held(engine: Engine, project_id: str) -> Iterator[Connection]:
    """The project row locked by an outside transaction until the block ends."""
    conn = engine.connect()
    held = conn.begin()
    conn.execute(
        text("SELECT id FROM design_projects WHERE id = :p FOR UPDATE"),
        {"p": uuid.UUID(project_id)},
    )
    try:
        yield conn
    finally:
        held.rollback()
        conn.close()


class _Race:
    """Requests started one at a time, each queued on the held lock before the next."""

    def __init__(self, engine: Engine, app: FastAPI) -> None:
        self.engine = engine
        self.app = app
        self.threads: list[threading.Thread] = []
        self.results: dict[str, Any] = {}

    def queue(self, name: str, call: Callable[[TestClient], Any]) -> None:
        before = _waiting(self.engine)

        def run() -> None:
            with TestClient(self.app, raise_server_exceptions=False) as client:
                self.results[name] = call(client)

        thread = threading.Thread(target=run, name=name)
        thread.start()
        self.threads.append(thread)
        deadline = time.monotonic() + _QUEUE_DEADLINE_S
        while _waiting(self.engine) <= before:
            assert thread.is_alive(), f"{name} finished without waiting on the lock"
            assert time.monotonic() < deadline, f"{name} never queued on the lock"
            time.sleep(0.02)

    def finish(self) -> dict[str, Any]:
        for thread in self.threads:
            thread.join()
        return self.results


@pytest.fixture(name="race")
def _race(engine: Engine, app: FastAPI) -> _Race:
    return _Race(engine, app)


# --------------------------------------------------------------------------- #
# 1. Two acknowledgements of different questions on one revision
# --------------------------------------------------------------------------- #


def test_two_concurrent_acknowledgements_both_stay_on_the_record(
    client: TestClient, engine: Engine, race: _Race
) -> None:
    saved = _save(client)
    pid, record = saved["id"], saved["requirements"]
    fault, ze = _qid(record, "fault_level_ka"), _qid(record, "ze_ohm")
    with _project_held(engine, pid):
        race.queue("fault", lambda c: _ack(c, pid, 1, fault))
        race.queue("ze", lambda c: _ack(c, pid, 1, ze))
    results = race.finish()
    assert results["fault"].status_code == 200, results["fault"].text[:600]
    assert results["ze"].status_code == 200, results["ze"].text[:600]
    stored = _stored(engine, pid, 1)
    assert sorted(a["question_id"] for a in stored["acknowledgements"]) == sorted([fault, ze])
    states = {q["id"]: q["state"] for q in stored["questions"]}
    assert states[fault] == states[ze] == "acknowledged"
    # The second response is the record as stored, with both.
    second = results["ze"].json()["requirements"]
    assert sorted(a["question_id"] for a in second["acknowledgements"]) == sorted([fault, ze])


# --------------------------------------------------------------------------- #
# 2. An acknowledgement racing an approval
# --------------------------------------------------------------------------- #


def test_an_acknowledgement_queued_behind_an_approval_is_refused(
    client: TestClient, engine: Engine, race: _Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)
    saved = _save(client)
    pid = saved["id"]
    fault = _qid(saved["requirements"], "fault_level_ka")
    before = _stored(engine, pid, 1)
    with _project_held(engine, pid):
        race.queue("approve", lambda c: approve(c, pid, 1))
        race.queue("ack", lambda c: _ack(c, pid, 1, fault))
    results = race.finish()
    assert results["approve"].status_code == 200, results["approve"].text[:600]
    assert results["ack"].status_code == 409
    assert error(results["ack"])["code"] == "revision_already_approved"
    # What was approved is what is stored: unchanged since.
    assert _stored(engine, pid, 1) == before


def test_an_approval_queued_behind_an_acknowledgement_approves_the_acknowledged_record(
    client: TestClient, engine: Engine, race: _Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)
    saved = _save(client)
    pid = saved["id"]
    fault = _qid(saved["requirements"], "fault_level_ka")
    with _project_held(engine, pid):
        race.queue("ack", lambda c: _ack(c, pid, 1, fault))
        race.queue("approve", lambda c: approve(c, pid, 1))
    results = race.finish()
    assert results["ack"].status_code == 200, results["ack"].text[:600]
    assert results["approve"].status_code == 200, results["approve"].text[:600]
    stored = _stored(engine, pid, 1)
    assert [a["question_id"] for a in stored["acknowledgements"]] == [fault]
    # The approval's response and the approved export show that record.
    assert results["approve"].json()["requirements"]["acknowledgements"] == stored[
        "acknowledgements"
    ]
    words = " ".join(" ".join(pdf_pages(revision_export(client, pid, 1, "pdf").content)).split())
    assert "Assumption acknowledged by" in words
    assert "not engineer-validated" in words


# --------------------------------------------------------------------------- #
# 3. The record and its hash
# --------------------------------------------------------------------------- #


def test_an_approval_refuses_a_record_that_does_not_match_its_hash(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)
    saved = _save(client)
    pid = saved["id"]
    altered = copy.deepcopy(revision_row(engine, pid, 1)["requirements"])
    altered["acknowledgements"] = [
        {"question_id": "q-forged", "by_user_id": str(uuid.uuid4()), "by_name": "X", "at": "2001"}
    ]
    update_row(engine, pid, 1, requirements=altered)
    refused = approve(client, pid, 1)
    assert refused.status_code == 409
    assert error(refused)["code"] == "snapshot_integrity_failed"
    assert error(refused)["params"]["reason"] == "requirements_mismatch"
    assert revision_row(engine, pid, 1)["approved_at"] is None
    # Nor is it acknowledged on, nor exported for review as if intact.
    fault = _qid(saved["requirements"], "fault_level_ka")
    acked = _ack(client, pid, 1, fault)
    assert acked.status_code == 409
    assert error(acked)["params"]["reason"] == "requirements_mismatch"
    opened = client.get(f"/design/projects/{pid}").json()
    assert opened["requirements"] is None
    assert any(f["values"].get("reason") == "requirements_mismatch" for f in opened["findings"])


def test_an_acknowledgement_stores_the_hash_of_what_it_stores(
    client: TestClient, engine: Engine
) -> None:
    saved = _save(client)
    pid = saved["id"]
    sha = revision_row(engine, pid, 1)["snapshot_sha256"]
    done = _ack(client, pid, 1, _qid(saved["requirements"], "fault_level_ka"))
    assert done.status_code == 200, done.text[:600]
    stored = _stored(engine, pid, 1)
    assert done.json()["requirements"]["acknowledgements"] == stored["acknowledgements"]
    assert revision_row(engine, pid, 1)["snapshot_sha256"] == sha
    opened = client.get(f"/design/projects/{pid}").json()
    assert opened["requirements"]["acknowledgements"] == stored["acknowledgements"]
    assert not any(
        f["values"].get("reason") == "requirements_mismatch" for f in opened["findings"]
    )


# --------------------------------------------------------------------------- #
# 4. Answers racing answers and acknowledgements
# --------------------------------------------------------------------------- #


def test_two_concurrent_answers_make_one_revision_and_carry_acknowledgements(
    client: TestClient, engine: Engine, race: _Race, caller: _Caller
) -> None:
    saved = _save(client)
    pid, record = saved["id"], saved["requirements"]
    pf = _qid(record, "power_factor")
    done = _ack(client, pid, 1, pf)
    assert done.status_code == 200, done.text[:600]
    first_ack = done.json()["requirements"]["acknowledgements"][0]
    fault, ze = _qid(record, "fault_level_ka"), _qid(record, "ze_ohm")
    with _project_held(engine, pid):
        race.queue("fault", lambda c: _ask(c, pid, 1, fault, {"action": "answer", "value": "12"}))
        race.queue("ze", lambda c: _ask(c, pid, 1, ze, {"action": "answer", "value": "0.2"}))
    results = race.finish()
    assert results["fault"].status_code == 201, results["fault"].text[:600]
    assert results["fault"].json()["revision"] == 2
    assert results["ze"].status_code == 409
    assert error(results["ze"])["code"] == "revision_not_latest"
    with engine.connect() as conn:
        numbers = conn.execute(
            text("SELECT number FROM design_project_revisions WHERE project_id = :p"),
            {"p": uuid.UUID(pid)},
        ).scalars()
        assert sorted(numbers) == [1, 2]
    carried = _stored(engine, pid, 2)["acknowledgements"]
    # Carried with who and when as first recorded, not re-stamped.
    assert carried == [first_ack]
    assert first_ack["by_user_id"] == caller.user.id


def test_an_acknowledgement_queued_behind_an_answer_is_refused_not_lost(
    client: TestClient, engine: Engine, race: _Race
) -> None:
    saved = _save(client)
    pid, record = saved["id"], saved["requirements"]
    fault, ze = _qid(record, "fault_level_ka"), _qid(record, "ze_ohm")
    with _project_held(engine, pid):
        race.queue("answer", lambda c: _ask(c, pid, 1, fault, {"action": "answer", "value": "12"}))
        race.queue("ack", lambda c: _ack(c, pid, 1, ze))
    results = race.finish()
    assert results["answer"].status_code == 201, results["answer"].text[:600]
    # Acknowledged on revision 1 after revision 2 was made without it, the
    # acknowledgement would be on no revision anyone works on: refused.
    assert results["ack"].status_code == 409, results["ack"].text[:600]
    assert error(results["ack"])["code"] == "revision_not_latest"
    assert _stored(engine, pid, 1)["acknowledgements"] == []
    assert _stored(engine, pid, 2)["acknowledgements"] == []


def test_an_acknowledgement_queued_before_an_answer_is_carried_forward(
    client: TestClient, engine: Engine, race: _Race
) -> None:
    saved = _save(client)
    pid, record = saved["id"], saved["requirements"]
    fault, ze = _qid(record, "fault_level_ka"), _qid(record, "ze_ohm")
    with _project_held(engine, pid):
        race.queue("ack", lambda c: _ack(c, pid, 1, ze))
        race.queue("answer", lambda c: _ask(c, pid, 1, fault, {"action": "answer", "value": "12"}))
    results = race.finish()
    assert results["ack"].status_code == 200, results["ack"].text[:600]
    assert results["answer"].status_code == 201, results["answer"].text[:600]
    first = _stored(engine, pid, 1)["acknowledgements"]
    assert [a["question_id"] for a in first] == [ze]
    assert _stored(engine, pid, 2)["acknowledgements"] == first


# --------------------------------------------------------------------------- #
# 6. Audit: append-only, server-set, a second acknowledgement changes nothing
# --------------------------------------------------------------------------- #


def test_acknowledging_twice_changes_nothing_even_by_another_member(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = _save(client)
    pid = saved["id"]
    fault, ze = _qid(saved["requirements"], "fault_level_ka"), _qid(saved["requirements"], "ze_ohm")
    assert _ack(client, pid, 1, fault).status_code == 200
    first = revision_row(engine, pid, 1)
    again = _ack(client, pid, 1, fault)
    assert again.status_code == 200
    assert revision_row(engine, pid, 1)["requirements"] == first["requirements"]
    assert revision_row(engine, pid, 1)["requirements_sha256"] == first["requirements_sha256"]
    owner = caller.user
    caller.user = review_support.new_account(engine, tenants[0])
    try:
        other = _ack(client, pid, 1, fault)
        assert other.status_code == 200
        assert revision_row(engine, pid, 1)["requirements"] == first["requirements"]
        # Another question: appended after the first, which stays as it was.
        assert _ack(client, pid, 1, ze).status_code == 200
    finally:
        caller.user = owner
    acks = _stored(engine, pid, 1)["acknowledgements"]
    assert acks[0] == first["requirements"]["acknowledgements"][0]
    assert acks[0]["by_user_id"] == owner.id
    assert [a["question_id"] for a in acks] == [fault, ze]
    assert acks[1]["by_user_id"] != owner.id


# --------------------------------------------------------------------------- #
# 7. Who may acknowledge what
# --------------------------------------------------------------------------- #


def test_another_tenants_revision_is_not_found_like_a_missing_one(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = _save(client)
    pid = saved["id"]
    fault = _qid(saved["requirements"], "fault_level_ka")
    before = revision_row(engine, pid, 1)
    owner = caller.user
    caller.user = review_support.new_account(engine, tenants[1], owner=True)
    try:
        theirs = _ack(client, pid, 1, fault)
        missing = _ack(client, str(uuid.uuid4()), 1, fault)
        answered = _ask(client, pid, 1, fault, {"action": "answer", "value": "12"})
    finally:
        caller.user = owner
    for refused in (theirs, missing, answered):
        assert refused.status_code == 404
        assert error(refused)["code"] == "revision_not_found"
    assert theirs.json() == missing.json()
    assert revision_row(engine, pid, 1)["requirements"] == before["requirements"]
    assert _ack(client, pid, 9, fault).status_code == 404


def test_a_trial_may_not_acknowledge(
    client: TestClient, engine: Engine, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = _save(client)
    pid = saved["id"]
    owner = caller.user
    caller.user = CurrentUser(
        id="trial", email="", tenant_id=tenants[0], roles=frozenset({Role.ENGINEER})
    )
    try:
        refused = _ack(client, pid, 1, _qid(saved["requirements"], "fault_level_ka"))
    finally:
        caller.user = owner
    assert refused.status_code == 403
    assert error(refused)["code"] == "acknowledgement_requires_account"
    assert _stored(engine, pid, 1)["acknowledgements"] == []


def test_derived_approved_and_superseded_revisions_refuse_acknowledgement(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)
    saved = _save(client)
    pid, record = saved["id"], saved["requirements"]
    fault, ze = _qid(record, "fault_level_ka"), _qid(record, "ze_ohm")
    answered = _ask(client, pid, 1, fault, {"action": "answer", "value": "12"})
    assert answered.status_code == 201, answered.text[:600]
    # Superseded: revision 1 is not the latest.
    stale = _ack(client, pid, 1, ze)
    assert stale.status_code == 409
    assert error(stale)["code"] == "revision_not_latest"
    assert error(stale)["params"]["latest"] == 2
    # Approved: what it was approved with does not change.
    assert approve(client, pid, 2).status_code == 200
    approved = _ack(client, pid, 2, ze)
    assert approved.status_code == 409
    assert error(approved)["code"] == "revision_already_approved"
    # Derived: a revision saved before records were kept.
    other = _save(client)
    update_row(engine, other["id"], 1, requirements=None, requirements_sha256=None)
    derived = _ack(client, other["id"], 1, _qid(other["requirements"], "ze_ohm"))
    assert derived.status_code == 409
    assert error(derived)["code"] == "requirements_derived"
    assert revision_row(engine, other["id"], 1)["requirements"] is None
