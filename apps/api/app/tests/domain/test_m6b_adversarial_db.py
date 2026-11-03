"""M6b adversarial tests: approval four-eyes, re-save recovery, acknowledgement races.

Written by the independent reviewer from the integration owner's contract
(PR #213, phase 1), against the HTTP routes and the disposable Postgres only
(``panelpilot_test_*``). They do not read the implementation's internals except
to pause a request at a known point (a monkeypatched module function), so a
second request can be interleaved deterministically against real row locks.

Contract A (approval four-eyes):
* an author is established only by ``author_user_id``; an email-only or
  author-less revision cannot be approved: 409 ``approval_author_not_established``;
* self-approval is 403 ``approval_by_author``, owners included;
* ``author_established`` on ``SavedProject`` and each ``RevisionSummary``;
* ``POST .../revisions/{n}/resave`` re-saves revision n's stored request as a
  new latest revision by the caller, note "Re-saved from revision <n> for
  independent review", requirements ``resaved_from: n``; a stored request
  refused today returns that 422 and saves nothing; old rows never change;
* another tenant: 404 everywhere.

Contract B (acknowledgements): no lost update under concurrency; an ack racing
an approval never changes an approved revision's record; the stored hash
matches the stored record; double ack is a no-op; derived record 409
``requirements_derived``; trial 403; append-only, server-set by/at.

Synthetic data only.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.v1.routes import design as design_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.design import snapshot
from app.models.schemas.auth import CurrentUser, Role
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _Caller,
    _engine,
    _tenants,
    canonical_sha256,
    error,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain import review_support
from app.tests.domain.test_m6a_adversarial_db import FED, GUIDED, MISSING

# --------------------------------------------------------------------------- #
# A client per user: the X-Test-User header picks who the request is from.
# --------------------------------------------------------------------------- #


class _Who:
    def __init__(self, engine: Engine, tenants: tuple[str, str]) -> None:
        a, b = tenants
        owner = _Caller(a, engine)  # tenant A's owner (approves)
        self.users: dict[str, CurrentUser] = {
            "owner": owner.user,
            "reviewer": review_support.new_account(engine, a, role=Role.REVIEWER),
            "reviewer2": review_support.new_account(engine, a, role=Role.REVIEWER),
            "engineer": review_support.new_account(engine, a),
            "other_tenant": review_support.new_account(engine, b, owner=True),
            # A trial: no email; one with no account id, one with an id.
            "trial": CurrentUser(id="trial", email="", tenant_id=a, roles=frozenset()),
            "trial_id": CurrentUser(
                id=str(uuid.uuid4()), email="", tenant_id=a, roles=frozenset({Role.ENGINEER})
            ),
        }


@pytest.fixture(name="who")
def _who_fixture(engine: Engine, tenants: tuple[str, str]) -> _Who:
    return _Who(engine, tenants)


@pytest.fixture(name="app")
def _app_fixture(engine: Engine, who: _Who) -> FastAPI:
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    def current(request: Request) -> CurrentUser:
        return who.users[request.headers.get("x-test-user", "owner")]

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = current
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    return app


class _As:
    """Requests from one named user."""

    def __init__(self, app: FastAPI, name: str) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"x-test-user": name}

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.client.post(url, headers=self.headers, **kw)


@pytest.fixture(name="as_")
def _as_fixture(app: FastAPI) -> Any:
    def make(name: str) -> _As:
        return _As(app, name)

    return make


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _save(c: _As, request: dict[str, Any] | None = None, guided: bool = True) -> str:
    body: dict[str, Any] = {"name": "M6b ADV", "request": request or MISSING, "note": "r1"}
    if guided:
        body["requirements_input"] = GUIDED
    response = c.post("/design/projects", json=body)
    assert response.status_code == 201, response.text[:600]
    return str(response.json()["id"])


def _open(c: _As, pid: str, number: int | None = None) -> dict[str, Any]:
    params = {} if number is None else {"revision": number}
    response = c.get(f"/design/projects/{pid}", params=params)
    assert response.status_code == 200, response.text[:600]
    body: dict[str, Any] = response.json()
    return body


def _approve(c: _As, pid: str, n: int) -> Any:
    return c.post(f"/design/projects/{pid}/revisions/{n}/approval", json={})


def _resave(c: _As, pid: str, n: int) -> Any:
    return c.post(f"/design/projects/{pid}/revisions/{n}/resave", json={})


def _ack(c: _As, pid: str, n: int, qid: str) -> Any:
    return c.post(
        f"/design/projects/{pid}/revisions/{n}/questions/{qid}", json={"action": "acknowledge"}
    )


def _assumptions(engine: Engine, pid: str, n: int = 1) -> list[str]:
    """Ids of the acknowledgeable open questions in the stored record."""
    record = revision_row(engine, pid, n)["requirements"]
    assert record is not None, "no stored requirements record"
    ids = [q["id"] for q in record["questions"] if q["state"] == "open"]
    assert len(ids) >= 2, record["questions"]
    return ids


def _count(engine: Engine, pid: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM design_project_revisions WHERE project_id = :p"),
                {"p": uuid.UUID(pid)},
            ).scalar_one()
        )


#: Columns that must never change on an existing revision after a re-save.
FROZEN = (
    "request",
    "design_snapshot",
    "snapshot_sha256",
    "requirements",
    "requirements_sha256",
    "author",
    "author_user_id",
    "note",
    "approved_at",
    "approved_user_id",
    "approved_account",
    "approved_snapshot_sha256",
    "approved_rules_version",
    "approved_renderer_version",
)


def _frozen(engine: Engine, pid: str, n: int) -> dict[str, Any]:
    row = revision_row(engine, pid, n)
    return {k: row.get(k) for k in FROZEN}


def _email_only(engine: Engine, pid: str, n: int = 1) -> None:
    """Make revision n look saved before author ids: an email, no account id."""
    update_row(engine, pid, n, author_user_id=None)


def _no_author(engine: Engine, pid: str, n: int = 1) -> None:
    update_row(engine, pid, n, author_user_id=None, author="")


def _summary(opened: dict[str, Any], n: int) -> dict[str, Any]:
    found: dict[str, Any] = next(r for r in opened["revisions"] if r["number"] == n)
    return found


# =========================================================================== #
# A. Approval four-eyes
# =========================================================================== #


def test_a_email_only_author_cannot_be_approved(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    _email_only(engine, pid)
    before = _frozen(engine, pid, 1)
    refused = _approve(as_("reviewer"), pid, 1)
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "approval_author_not_established"
    assert _frozen(engine, pid, 1) == before


def test_a_email_only_author_matching_the_approver_is_still_not_established(
    engine: Engine, as_: Any
) -> None:
    """No guessing from the email: neither approved nor treated as the author's own."""
    pid = _save(as_("reviewer"), FED)
    _email_only(engine, pid)
    refused = _approve(as_("reviewer"), pid, 1)
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "approval_author_not_established"
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_no_author_cannot_be_approved_and_none_is_backfilled(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    _no_author(engine, pid)
    for name in ("reviewer", "owner"):
        refused = _approve(as_(name), pid, 1)
        assert refused.status_code == 409, refused.text[:600]
        assert error(refused)["code"] == "approval_author_not_established"
    row = revision_row(engine, pid, 1)
    assert row["author"] == "" and row["author_user_id"] is None
    assert row["approved_at"] is None


@pytest.mark.parametrize("author", ["owner", "reviewer"])
def test_a_self_approval_is_403_owners_included(engine: Engine, as_: Any, author: str) -> None:
    pid = _save(as_(author), FED)
    refused = _approve(as_(author), pid, 1)
    assert refused.status_code == 403, refused.text[:600]
    assert error(refused)["code"] == "approval_by_author"
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_self_approval_by_id_survives_an_email_change(
    engine: Engine, as_: Any, who: _Who
) -> None:
    pid = _save(as_("reviewer"), FED)
    who.users["reviewer"] = who.users["reviewer"].model_copy(
        update={"email": "renamed-" + who.users["reviewer"].email}
    )
    refused = _approve(as_("reviewer"), pid, 1)
    assert refused.status_code == 403, refused.text[:600]
    assert error(refused)["code"] == "approval_by_author"


def test_a_author_established_is_exposed(engine: Engine, as_: Any) -> None:
    c = as_("engineer")
    pid = _save(c, FED)
    assert (
        c.post(f"/design/projects/{pid}/revisions", json={"request": FED, "note": "r2"}).status_code
        == 201
    )
    assert (
        c.post(f"/design/projects/{pid}/revisions", json={"request": FED, "note": "r3"}).status_code
        == 201
    )
    _email_only(engine, pid, 1)
    _no_author(engine, pid, 2)
    opened = _open(as_("reviewer"), pid, 3)
    assert opened["author_established"] is True
    assert _summary(opened, 1)["author_established"] is False
    assert _summary(opened, 2)["author_established"] is False
    assert _summary(opened, 3)["author_established"] is True
    assert _open(as_("reviewer"), pid, 1)["author_established"] is False


def test_a_resave_recovers_an_email_only_revision(engine: Engine, as_: Any, who: _Who) -> None:
    pid = _save(as_("engineer"), FED)
    _email_only(engine, pid)
    before = _frozen(engine, pid, 1)
    done = _resave(as_("reviewer"), pid, 1)
    assert done.status_code in (200, 201), done.text[:600]
    assert _count(engine, pid) == 2
    new = revision_row(engine, pid, 2)
    assert new["author_user_id"] == uuid.UUID(who.users["reviewer"].id)
    assert new["note"] == "Re-saved from revision 1 for independent review"
    assert new["request"] == before["request"]
    assert new["requirements"] is not None
    assert new["requirements"].get("resaved_from") == 1
    assert new["requirements_sha256"] == snapshot.sha256_of(new["requirements"])
    assert _frozen(engine, pid, 1) == before
    # Its re-saver may not approve it; another eligible account may.
    self_ = _approve(as_("reviewer"), pid, 2)
    assert self_.status_code == 403, self_.text[:600]
    assert error(self_)["code"] == "approval_by_author"
    ok = _approve(as_("reviewer2"), pid, 2)
    assert ok.status_code == 200, ok.text[:600]
    assert _frozen(engine, pid, 1) == before
    assert _open(as_("owner"), pid, 2)["author_established"] is True


def test_a_resave_of_an_approved_revision_leaves_its_approval(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    assert _approve(as_("reviewer"), pid, 1).status_code == 200
    before = _frozen(engine, pid, 1)
    assert before["approved_at"] is not None
    done = _resave(as_("reviewer2"), pid, 1)
    assert done.status_code in (200, 201), done.text[:600]
    assert _frozen(engine, pid, 1) == before
    assert revision_row(engine, pid, 2)["approved_at"] is None


def test_a_resave_of_an_older_revision_becomes_latest(engine: Engine, as_: Any) -> None:
    c = as_("engineer")
    pid = _save(c, FED)
    other = copy.deepcopy(FED)
    other["info"]["number"] = "M6B-R2"
    assert (
        c.post(
            f"/design/projects/{pid}/revisions", json={"request": other, "note": "r2"}
        ).status_code
        == 201
    )
    done = _resave(as_("reviewer"), pid, 1)
    assert done.status_code in (200, 201), done.text[:600]
    assert _count(engine, pid) == 3
    assert revision_row(engine, pid, 3)["request"] == revision_row(engine, pid, 1)["request"]
    assert revision_row(engine, pid, 3)["requirements"]["resaved_from"] == 1


def _typed(request: dict[str, Any], feeds: bool) -> dict[str, Any]:
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


@pytest.mark.parametrize(
    ("feeds", "code"), [(True, "feeder_server_owned"), (False, "sub_board_load_server_owned")]
)
def test_a_resave_of_a_request_refused_today_saves_nothing(
    engine: Engine, as_: Any, feeds: bool, code: str
) -> None:
    pid = _save(as_("engineer"), FED)
    update_row(engine, pid, 1, request=_typed(FED, feeds), author_user_id=None)
    before = _frozen(engine, pid, 1)
    refused = _resave(as_("reviewer"), pid, 1)
    assert refused.status_code == 422, refused.text[:600]
    assert error(refused)["code"] == code
    assert _count(engine, pid) == 1
    assert _frozen(engine, pid, 1) == before


@pytest.mark.parametrize("route", ["open", "approve", "resave", "ack"])
def test_a_other_tenant_gets_404(engine: Engine, as_: Any, route: str) -> None:
    pid = _save(as_("engineer"))
    _email_only(engine, pid)
    qid = _assumptions(engine, pid)[0]
    before = _frozen(engine, pid, 1)
    c = as_("other_tenant")
    if route == "open":
        response = c.get(f"/design/projects/{pid}", params={"revision": 1})
    elif route == "approve":
        response = _approve(c, pid, 1)
    elif route == "resave":
        response = _resave(c, pid, 1)
    else:
        response = _ack(c, pid, 1, qid)
    assert response.status_code == 404, response.text[:600]
    assert _count(engine, pid) == 1
    assert _frozen(engine, pid, 1) == before


def test_a_resave_of_a_missing_revision_is_404(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    response = _resave(as_("reviewer"), pid, 9)
    assert response.status_code == 404, response.text[:600]
    assert _count(engine, pid) == 1


# =========================================================================== #
# B. Acknowledgements
# =========================================================================== #


def test_b_stored_hash_matches_stored_record_after_acks(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"))
    for qid in _assumptions(engine, pid)[:2]:
        assert _ack(as_("reviewer"), pid, 1, qid).status_code == 200
        row = revision_row(engine, pid, 1)
        assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])
        assert row["requirements_sha256"] == canonical_sha256(row["requirements"])


def test_b_double_ack_is_a_noop_even_by_another_user(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"))
    qid = _assumptions(engine, pid)[0]
    assert _ack(as_("reviewer"), pid, 1, qid).status_code == 200
    first = revision_row(engine, pid, 1)
    for name in ("reviewer", "engineer"):
        again = _ack(as_(name), pid, 1, qid)
        assert again.status_code == 200, again.text[:600]
    after = revision_row(engine, pid, 1)
    assert after["requirements"] == first["requirements"]
    assert after["requirements_sha256"] == first["requirements_sha256"]
    acks = [a for a in after["requirements"]["acknowledgements"] if a["question_id"] == qid]
    assert len(acks) == 1


def test_b_acks_are_append_only_and_server_set(engine: Engine, as_: Any, who: _Who) -> None:
    pid = _save(as_("engineer"))
    q1, q2 = _assumptions(engine, pid)[:2]
    assert _ack(as_("reviewer"), pid, 1, q1).status_code == 200
    first = list(revision_row(engine, pid, 1)["requirements"]["acknowledgements"])
    assert _ack(as_("engineer"), pid, 1, q2).status_code == 200
    acks = revision_row(engine, pid, 1)["requirements"]["acknowledgements"]
    assert acks[: len(first)] == first
    assert acks[-1]["question_id"] == q2
    assert acks[-1]["by_user_id"] == who.users["engineer"].id
    assert acks[-1]["at"]


def test_b_derived_record_is_409(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"))
    qid = _assumptions(engine, pid)[0]
    update_row(engine, pid, 1, requirements=None, requirements_sha256=None)
    opened = _open(as_("reviewer"), pid, 1)
    assert opened["requirements"]["derived"] is True
    derived_ids = [q["id"] for q in opened["requirements"]["questions"]] or [qid]
    refused = _ack(as_("reviewer"), pid, 1, derived_ids[0])
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "requirements_derived"
    assert revision_row(engine, pid, 1)["requirements"] is None


@pytest.mark.parametrize("trial", ["trial", "trial_id"])
def test_b_trial_is_403(engine: Engine, as_: Any, trial: str) -> None:
    pid = _save(as_("engineer"))
    qid = _assumptions(engine, pid)[0]
    before = revision_row(engine, pid, 1)["requirements"]
    refused = _ack(as_(trial), pid, 1, qid)
    assert refused.status_code == 403, refused.text[:600]
    assert error(refused)["code"] == "acknowledgement_requires_account"
    assert revision_row(engine, pid, 1)["requirements"] == before


def test_b_ack_after_approval_is_409_and_changes_nothing(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    record = revision_row(engine, pid, 1)["requirements"]
    open_ids = [q["id"] for q in record["questions"] if q["state"] == "open"]
    if not open_ids:
        pid = _save(as_("engineer"))
        open_ids = _assumptions(engine, pid)
    if _approve(as_("reviewer"), pid, 1).status_code != 200:
        pytest.skip("this request does not approve (blocking findings); covered by the race tests")
    before = revision_row(engine, pid, 1)
    refused = _ack(as_("engineer"), pid, 1, open_ids[0])
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "revision_already_approved"
    after = revision_row(engine, pid, 1)
    assert after["requirements"] == before["requirements"]
    assert after["requirements_sha256"] == before["requirements_sha256"]


# --------------------------------------------------------------------------- #
# Deterministic interleavings on real Postgres
# --------------------------------------------------------------------------- #


class _Gate:
    """Pause the first call of a module function until released (holding its locks)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._used = False
        self._lock = threading.Lock()
        original = getattr(module, name)

        def paused(*args: Any, **kw: Any) -> Any:
            out = original(*args, **kw)
            with self._lock:
                first = not self._used
                self._used = True
            if first:
                self.entered.set()
                assert self.release.wait(20), "gate never released"
            return out

        monkeypatch.setattr(module, name, paused)


class _Run(threading.Thread):
    def __init__(self, fn: Any) -> None:
        super().__init__(daemon=True)
        self.fn = fn
        self.result: Any = None

    def run(self) -> None:
        self.result = self.fn()


def _waiting_on_lock(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                    "AND datname = current_database()"
                )
            ).scalar_one()
        )


def _interleave(first: Any, second: Any, gate: _Gate, engine: Engine) -> tuple[Any, Any, bool]:
    """Run ``first`` up to the gate, then ``second``; report whether it blocked."""
    a = _Run(first)
    a.start()
    assert gate.entered.wait(30), "first request never reached the gate"
    b = _Run(second)
    b.start()
    blocked = False
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and b.is_alive():
        if _waiting_on_lock(engine) > 0:
            blocked = True
            break
        time.sleep(0.05)
    b_finished_early = not b.is_alive()
    gate.release.set()
    a.join(30)
    b.join(30)
    assert not a.is_alive() and not b.is_alive()
    return a.result, b.result, blocked and not b_finished_early


def test_b_concurrent_acks_lose_no_update(
    engine: Engine, as_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.design import requirements as req_module

    pid = _save(as_("engineer"))
    q1, q2 = _assumptions(engine, pid)[:2]
    gate = _Gate(monkeypatch, req_module, "apply_acknowledgements")
    ra, rb, blocked = _interleave(
        lambda: _ack(as_("reviewer"), pid, 1, q1),
        lambda: _ack(as_("engineer"), pid, 1, q2),
        gate,
        engine,
    )
    assert ra.status_code == 200, ra.text[:600]
    assert rb.status_code == 200, rb.text[:600]
    assert blocked, "the second acknowledgement did not wait for the first's row lock"
    row = revision_row(engine, pid, 1)
    acked = {a["question_id"] for a in row["requirements"]["acknowledgements"]}
    assert {q1, q2} <= acked, "an acknowledgement was lost"
    assert row["requirements_sha256"] == snapshot.sha256_of(row["requirements"])


def test_b_concurrent_double_ack_records_once(
    engine: Engine, as_: Any, who: _Who, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.design import requirements as req_module

    pid = _save(as_("engineer"))
    q1 = _assumptions(engine, pid)[0]
    gate = _Gate(monkeypatch, req_module, "apply_acknowledgements")
    ra, rb, _ = _interleave(
        lambda: _ack(as_("reviewer"), pid, 1, q1),
        lambda: _ack(as_("engineer"), pid, 1, q1),
        gate,
        engine,
    )
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text[:300], rb.text[:300])
    acks = revision_row(engine, pid, 1)["requirements"]["acknowledgements"]
    mine = [a for a in acks if a["question_id"] == q1]
    assert len(mine) == 1, mine
    assert mine[0]["by_user_id"] == who.users["reviewer"].id


def _approvable_missing(engine: Engine, as_: Any) -> tuple[str, list[str]]:
    """A saved guided project with open assumptions that a reviewer can approve."""
    for request in (MISSING, FED):
        pid = _save(as_("engineer"), request)
        rec = revision_row(engine, pid, 1)["requirements"]
        ids = [q["id"] for q in rec["questions"] if q["state"] == "open"]
        if ids:
            return pid, ids
    pytest.fail("no request with open questions")


def test_b_approval_then_racing_ack_never_changes_the_approved_record(
    engine: Engine, as_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval holds its locks (paused); an ack arrives; approval commits; ack must 409."""
    from app.domain import design_projects

    pid, ids = _approvable_missing(engine, as_)
    if _approve_dry_run_blocked(engine, pid):
        pytest.skip("revision has blocking findings; cannot approve")
    before = revision_row(engine, pid, 1)
    gate = _Gate(monkeypatch, design_projects, "_refuse_author")
    rapp, rack, blocked = _interleave(
        lambda: _approve(as_("reviewer"), pid, 1),
        lambda: _ack(as_("engineer"), pid, 1, ids[0]),
        gate,
        engine,
    )
    assert rapp.status_code == 200, rapp.text[:600]
    assert blocked, "the acknowledgement did not wait for the approval's lock"
    assert rack.status_code == 409, rack.text[:600]
    assert error(rack)["code"] == "revision_already_approved"
    after = revision_row(engine, pid, 1)
    assert after["requirements"] == before["requirements"]
    assert after["requirements_sha256"] == before["requirements_sha256"]


def test_b_ack_then_racing_approval_sees_the_ack(
    engine: Engine, as_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ack holds its locks (paused); approval arrives; both succeed; record fixed after."""
    from app.design import requirements as req_module

    pid, ids = _approvable_missing(engine, as_)
    if _approve_dry_run_blocked(engine, pid):
        pytest.skip("revision has blocking findings; cannot approve")
    gate = _Gate(monkeypatch, req_module, "apply_acknowledgements")
    rack, rapp, blocked = _interleave(
        lambda: _ack(as_("engineer"), pid, 1, ids[0]),
        lambda: _approve(as_("reviewer"), pid, 1),
        gate,
        engine,
    )
    assert rack.status_code == 200, rack.text[:600]
    assert rapp.status_code == 200, rapp.text[:600]
    assert blocked, "the approval did not wait for the acknowledgement's lock"
    approved = revision_row(engine, pid, 1)
    assert ids[0] in {a["question_id"] for a in approved["requirements"]["acknowledgements"]}
    # After approval the record never changes again.
    late = _ack(as_("engineer"), pid, 1, ids[1]) if len(ids) > 1 else None
    if late is not None:
        assert late.status_code == 409 and error(late)["code"] == "revision_already_approved"
    final = revision_row(engine, pid, 1)
    assert final["requirements"] == approved["requirements"]
    assert final["requirements_sha256"] == approved["requirements_sha256"]


def test_b_external_row_lock_blocks_an_ack(engine: Engine, as_: Any) -> None:
    """A second real connection holding the revision row: the ack must wait, then apply."""
    pid = _save(as_("engineer"))
    qid = _assumptions(engine, pid)[0]
    holder = engine.connect()
    tx = holder.begin()
    holder.execute(
        text(
            "SELECT 1 FROM design_project_revisions WHERE project_id = :p AND number = 1 "
            "FOR UPDATE"
        ),
        {"p": uuid.UUID(pid)},
    )
    run = _Run(lambda: _ack(as_("reviewer"), pid, 1, qid))
    run.start()
    time.sleep(1.5)
    still_waiting = run.is_alive()
    tx.rollback()
    holder.close()
    run.join(30)
    assert still_waiting, "the ack did not wait for the revision row lock"
    assert run.result.status_code == 200, run.result.text[:600]
    row = revision_row(engine, pid, 1)
    assert qid in {a["question_id"] for a in row["requirements"]["acknowledgements"]}


def test_b_ack_racing_a_new_revision_is_carried_or_ordered(
    engine: Engine, as_: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ack on the latest revision holds locks; a new revision is saved meanwhile.

    Acknowledgements whose question still stands carry to the new revision: the
    save must read the record after the ack commits, never lose it.
    """
    from app.design import requirements as req_module

    pid = _save(as_("engineer"))
    qid = _assumptions(engine, pid)[0]
    gate = _Gate(monkeypatch, req_module, "apply_acknowledgements")
    rack, rrev, blocked = _interleave(
        lambda: _ack(as_("reviewer"), pid, 1, qid),
        lambda: as_("engineer").post(
            f"/design/projects/{pid}/revisions",
            json={"request": MISSING, "note": "r2", "requirements_input": GUIDED},
        ),
        gate,
        engine,
    )
    assert rack.status_code == 200, rack.text[:600]
    assert rrev.status_code == 201, rrev.text[:600]
    assert blocked, "the save did not wait for the acknowledgement"
    new = revision_row(engine, pid, 2)["requirements"]
    assert qid in {
        a["question_id"] for a in new.get("acknowledgements", [])
    }, "the acknowledgement was lost from the new revision"


def _approve_dry_run_blocked(engine: Engine, pid: str) -> bool:
    findings = revision_row(engine, pid, 1).get("findings") or []
    return any(isinstance(f, dict) and f.get("severity") == "error" for f in findings)
