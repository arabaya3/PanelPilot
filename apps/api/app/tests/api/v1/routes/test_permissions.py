"""Who may approve, delete and change company settings, through the real routes.

Driven with FastAPI's TestClient against the disposable Postgres, as a caller
would call the API directly, so the page hiding a button is never what keeps
an action from happening. The policy is ``app.domain.permissions``.

Like the export-integrity tests, these refuse any local database not named
``panelpilot_phase1_*``, and skip with a reason naming Postgres where none is
configured (CI's integration job fails on such a skip).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.v1.routes import design as design_route
from app.api.v1.routes import team as team_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.core.tenancy import bind_tenant, cross_tenant
from app.domain import permissions, roles
from app.domain import team as team_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    approve,
    pytestmark,
    revision_row,
    save,
)

EVERYTHING = ["approve_revision", "delete_project", "edit_company_settings"]


@dataclass
class _Caller:
    """Who the next request is from; a test switches it."""

    user: CurrentUser


def _tenant(engine: Engine, slug: str) -> uuid.UUID:
    """A tenant with no accounts and no company settings."""
    with Session(bind=engine) as orm:
        found = orm.execute(
            text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug}
        ).scalar_one_or_none()
        if found is None:
            row = TenantRow(slug=slug, name=slug)
            orm.add(row)
            orm.flush()
            found = row.id
        orm.execute(text("UPDATE tenants SET owner_user_id = NULL WHERE id = :t"), {"t": found})
        orm.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": found})
        orm.execute(text("DELETE FROM company_settings WHERE tenant_id = :t"), {"t": found})
        orm.commit()
        return uuid.UUID(str(found))


def _account(
    engine: Engine,
    tenant: uuid.UUID,
    email: str,
    *,
    age_days: int,
    grant: Role | None = None,
    owner: bool = False,
) -> CurrentUser:
    """An account, created ``age_days`` ago; the caller it resolves to, roles from the DB."""
    created = datetime.now(UTC) - timedelta(days=age_days)
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, tenant)
        row = User(
            tenant_id=tenant,
            email=email,
            full_name=email.split("@")[0],
            is_active=True,
            created_at=created,
            updated_at=created,
        )
        session.add(row)
        session.flush()
        if grant is not None:
            roles.grant_role(session=session, email=email, role=grant)
        if owner:
            session.execute(
                text("UPDATE tenants SET owner_user_id = :u WHERE id = :t"),
                {"u": row.id, "t": tenant},
            )
        held = roles.roles_of(row)
        user_id = str(row.id)
        session.commit()
    return CurrentUser(id=user_id, email=email, tenant_id=str(tenant), roles=held)


@dataclass
class _Team:
    owner: CurrentUser
    admin: CurrentUser
    reviewer: CurrentUser
    member: CurrentUser
    other_owner: CurrentUser
    other_member: CurrentUser
    trial: CurrentUser


@pytest.fixture(name="team")
def _team(engine: Engine) -> _Team:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE design_project_revisions, design_projects CASCADE"))
    a = _tenant(engine, "perm-route-a")
    b = _tenant(engine, "perm-route-b")
    return _Team(
        owner=_account(engine, a, "perm-owner@example.com", age_days=40, owner=True),
        admin=_account(engine, a, "perm-admin@example.com", age_days=30, grant=Role.ADMIN),
        reviewer=_account(engine, a, "perm-reviewer@example.com", age_days=20, grant=Role.REVIEWER),
        member=_account(engine, a, "perm-member@example.com", age_days=10),
        other_owner=_account(engine, b, "perm-b-owner@example.com", age_days=40, owner=True),
        other_member=_account(engine, b, "perm-b-member@example.com", age_days=5),
        # A trial token resolves to an engineer with no email and no account.
        trial=CurrentUser(
            id=str(uuid.uuid4()), email="", tenant_id=str(a), roles=frozenset({Role.ENGINEER})
        ),
    )


@pytest.fixture(name="caller")
def _caller(team: _Team) -> _Caller:
    return _Caller(team.owner)


@pytest.fixture(name="app")
def _app(engine: Engine, caller: _Caller) -> FastAPI:
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.include_router(team_route.router, prefix="/team")
    app.dependency_overrides[deps.get_current_user] = lambda: caller.user
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    return app


@pytest.fixture(name="client")
def _client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _as(caller: _Caller, user: CurrentUser) -> None:
    caller.user = user


def _delete(client: TestClient, project_id: str) -> Any:
    return client.delete(f"/design/projects/{project_id}")


def _put_settings(client: TestClient, key: str = "acme") -> Any:
    return client.put("/design/company-settings", json={"settings": {"key": key}})


def _project_exists(engine: Engine, project_id: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT count(*) FROM design_projects WHERE id = :p"),
                {"p": uuid.UUID(project_id)},
            ).scalar()
            == 1
        )


# --------------------------------------------------------------------------- #
# The matrix, through the API
# --------------------------------------------------------------------------- #

ALLOWED = {
    "owner": {"approve": True, "delete": True, "settings": True},
    "admin": {"approve": True, "delete": True, "settings": True},
    "reviewer": {"approve": True, "delete": False, "settings": False},
    "member": {"approve": False, "delete": False, "settings": False},
}


@pytest.mark.parametrize("who", list(ALLOWED))
def test_approve_follows_the_matrix(
    who: str, client: TestClient, caller: _Caller, team: _Team, engine: Engine
) -> None:
    pid = save(client)["id"]
    _as(caller, getattr(team, who))
    response = approve(client, pid, 1)
    if ALLOWED[who]["approve"]:
        assert response.status_code == 200, response.text
        assert revision_row(engine, pid, 1)["approved_user_id"] == uuid.UUID(caller.user.id)
    else:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "approval_not_permitted"
        assert revision_row(engine, pid, 1)["approved_at"] is None


@pytest.mark.parametrize("who", list(ALLOWED))
def test_delete_follows_the_matrix(
    who: str, client: TestClient, caller: _Caller, team: _Team, engine: Engine
) -> None:
    pid = save(client)["id"]
    _as(caller, getattr(team, who))
    response = _delete(client, pid)
    if ALLOWED[who]["delete"]:
        assert response.status_code == 204, response.text
        assert not _project_exists(engine, pid)
    else:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "project_delete_not_permitted"
        assert _project_exists(engine, pid)


@pytest.mark.parametrize("who", list(ALLOWED))
def test_settings_follow_the_matrix(
    who: str, client: TestClient, caller: _Caller, team: _Team
) -> None:
    assert _put_settings(client, "before").status_code == 200
    _as(caller, getattr(team, who))
    read = client.get("/design/company-settings")
    assert read.status_code == 200, read.text
    assert read.json()["settings"] == {"key": "before"}
    response = _put_settings(client, "after")
    if ALLOWED[who]["settings"]:
        assert response.status_code == 200, response.text
        assert read.json()["permissions"] == ["edit_company_settings"]
    else:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "settings_not_permitted"
        assert read.json()["permissions"] == []
        assert client.get("/design/company-settings").json()["settings"] == {"key": "before"}


@pytest.mark.parametrize("who", list(ALLOWED))
def test_responses_tell_the_client_what_it_may_do(
    who: str, client: TestClient, caller: _Caller, team: _Team
) -> None:
    pid = save(client)["id"]
    _as(caller, getattr(team, who))
    expected = [
        name
        for name, key in zip(EVERYTHING, ("approve", "delete", "settings"), strict=True)
        if ALLOWED[who][key]
    ]
    assert client.get("/design/projects").json()["permissions"] == expected
    assert client.get(f"/design/projects/{pid}").json()["permissions"] == expected


def test_a_trial_may_do_none_of_the_three(client: TestClient, caller: _Caller, team: _Team) -> None:
    pid = save(client)["id"]
    _as(caller, team.trial)
    refused = approve(client, pid, 1)
    assert refused.status_code == 403
    assert refused.json()["code"] == "approval_requires_account"
    refused = _delete(client, pid)
    assert refused.status_code == 403
    assert refused.json()["code"] == "project_delete_not_permitted"
    refused = _put_settings(client)
    assert refused.status_code == 403
    assert refused.json()["code"] == "settings_not_permitted"


@pytest.mark.parametrize("who", ["other_owner", "other_member"])
def test_another_tenants_project_is_not_found_for_every_action(
    who: str, client: TestClient, caller: _Caller, team: _Team, engine: Engine
) -> None:
    """The same 404 for an owner and a member of another tenant: nothing leaks."""
    pid = save(client)["id"]
    _as(caller, getattr(team, who))
    refused = approve(client, pid, 1)
    assert refused.status_code == 404, refused.text
    assert refused.json()["code"] == "revision_not_found"
    refused = _delete(client, pid)
    assert refused.status_code == 404, refused.text
    assert refused.json()["code"] == "project_not_found"
    refused = client.get(f"/design/projects/{pid}")
    assert refused.status_code == 404
    # A project that exists nowhere answers exactly the same.
    missing = _delete(client, str(uuid.uuid4()))
    assert (missing.status_code, missing.json()["code"]) == (404, "project_not_found")
    assert _project_exists(engine, pid)
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_legacy_tenant_keeps_its_backfilled_owner(
    client: TestClient, caller: _Caller, engine: Engine
) -> None:
    """Accounts from before the checks, no role rows; the migration recorded the first."""
    legacy = _tenant(engine, "perm-route-legacy")
    first = _account(engine, legacy, "legacy-first@example.com", age_days=900, owner=True)
    others = [_account(engine, legacy, f"legacy-{i}@example.com", age_days=900 - i) for i in (1, 2)]
    _as(caller, first)
    pid = save(client)["id"]
    second = save(client)["id"]
    for other in others:
        assert other.roles == frozenset({Role.ENGINEER})
        _as(caller, other)
        assert approve(client, pid, 1).json()["code"] == "approval_not_permitted"
        assert _delete(client, pid).json()["code"] == "project_delete_not_permitted"
        assert _put_settings(client).json()["code"] == "settings_not_permitted"
    _as(caller, first)
    assert approve(client, pid, 1).status_code == 200
    assert _delete(client, second).status_code == 204
    assert _put_settings(client).status_code == 200


# --------------------------------------------------------------------------- #
# Approve racing delete on the same revision
# --------------------------------------------------------------------------- #


def _race(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run each call on its own thread, released together."""
    barrier = threading.Barrier(len(calls))

    def run(call: Callable[[], Any]) -> Any:
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def _assert_approve_won(engine: Engine, pid: str, approved: Any, deleted: Any) -> None:
    assert approved.status_code == 200, approved.text
    assert deleted.status_code == 422, deleted.text
    assert deleted.json()["code"] == "project_approved"
    assert _project_exists(engine, pid)
    row = revision_row(engine, pid, 1)
    assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]


def _assert_delete_won(engine: Engine, pid: str, approved: Any, deleted: Any) -> None:
    assert deleted.status_code == 204, deleted.text
    assert approved.status_code == 404, approved.text
    assert approved.json()["code"] == "revision_not_found"
    assert not _project_exists(engine, pid)


def test_approve_committed_then_delete_is_refused(
    engine: Engine, team: _Team, client: TestClient, caller: _Caller
) -> None:
    """One ordering of the race, made deterministic: the approval commits first."""
    pid = save(client)["id"]
    _as(caller, team.reviewer)
    approved = approve(client, pid, 1)
    _as(caller, team.owner)
    deleted = _delete(client, pid)
    _assert_approve_won(engine, pid, approved, deleted)


def test_delete_committed_then_approve_is_not_found(
    engine: Engine, team: _Team, client: TestClient, caller: _Caller
) -> None:
    """The other ordering, made deterministic: the delete commits first."""
    pid = save(client)["id"]
    deleted = _delete(client, pid)
    _as(caller, team.reviewer)
    approved = approve(client, pid, 1)
    _assert_delete_won(engine, pid, approved, deleted)


@pytest.mark.parametrize("round_", range(6))
def test_approve_racing_delete_ends_consistent(
    round_: int,
    engine: Engine,
    team: _Team,
    client: TestClient,
    request: pytest.FixtureRequest,
) -> None:
    """Reviewer approves while the owner deletes: one wins, the other is told why.

    Which one wins is up to the scheduler; each round records its outcome
    (``race_outcome`` in the test's user properties, so in a JUnit report),
    and both orderings are also tested deterministically above.
    """
    pid = save(client)["id"]
    users = {"approve": team.reviewer, "delete": team.owner}
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    # One app and client per racer, each with its own session, so each
    # request is made as its own account.
    def racer(kind: str) -> Callable[[], Any]:
        app = FastAPI()
        app.include_router(design_route.router, prefix="/design")
        app.dependency_overrides[deps.get_current_user] = lambda: users[kind]
        app.dependency_overrides[get_session] = session
        app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
        install_exception_handlers(app)

        own = TestClient(app, raise_server_exceptions=False)

        def call() -> Any:
            return approve(own, pid, 1) if kind == "approve" else _delete(own, pid)

        return call

    approved, deleted = _race([racer("approve"), racer("delete")])
    assert 500 not in (approved.status_code, deleted.status_code), (approved.text, deleted.text)
    outcome = "approve_won" if _project_exists(engine, pid) else "delete_won"
    request.node.user_properties.append(("race_outcome", f"round {round_}: {outcome}"))
    if outcome == "approve_won":
        # The delete waited for the approval, then refused an approved project.
        _assert_approve_won(engine, pid, approved, deleted)
    else:
        # The approval waited for the delete, then found no revision.
        _assert_delete_won(engine, pid, approved, deleted)


def test_a_refused_approval_holds_no_lock(
    engine: Engine, team: _Team, client: TestClient, caller: _Caller
) -> None:
    """A member's refused approval leaves the owner's delete free to finish."""
    pid = save(client)["id"]
    _as(caller, team.member)
    refused = approve(client, pid, 1)
    assert refused.status_code == 403
    _as(caller, team.owner)
    assert _delete(client, pid).status_code == 204
    assert not _project_exists(engine, pid)


# --------------------------------------------------------------------------- #
# Ownership through the team routes
# --------------------------------------------------------------------------- #


def _remove(client: TestClient, member: CurrentUser) -> Any:
    return client.delete(f"/team/members/{member.id}")


def _transfer(client: TestClient, member: CurrentUser | str) -> Any:
    member_id = member if isinstance(member, str) else member.id
    return client.post("/team/owner", json={"member_id": member_id})


def _owner_of(engine: Engine, tenant_id: str) -> uuid.UUID | None:
    with engine.connect() as conn:
        found: object = conn.execute(
            text("SELECT owner_user_id FROM tenants WHERE id = :t"), {"t": uuid.UUID(tenant_id)}
        ).scalar_one()
    return None if found is None else uuid.UUID(str(found))


def test_an_admin_cannot_remove_the_owner(team: _Team, client: TestClient, caller: _Caller) -> None:
    _as(caller, team.admin)
    refused = _remove(client, team.owner)
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "team_owner_not_removable"
    _as(caller, team.owner)
    assert _put_settings(client).status_code == 200
    # Removing a plain member is still the admin's to do.
    _as(caller, team.admin)
    assert _remove(client, team.member).status_code == 204


def test_a_deactivated_owner_hands_ownership_to_nobody(
    engine: Engine, team: _Team, client: TestClient, caller: _Caller
) -> None:
    """Deactivated outside the API: nobody inherits by seniority."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET is_active = false WHERE id IN (:a, :b)"),
            {"a": uuid.UUID(team.owner.id), "b": uuid.UUID(team.admin.id)},
        )
    # The reviewer is now the oldest active account: still only a reviewer.
    _as(caller, team.reviewer)
    assert _put_settings(client).json()["code"] == "settings_not_permitted"
    assert _remove(client, team.member).json()["code"] == "team_owner_only"
    shown = client.get("/team").json()
    assert (shown["owner"], shown["owns"]) == (False, False)
    assert [m for m in shown["members"] if m["owner"]] == []


def test_the_owner_transfers_through_the_api(
    engine: Engine, team: _Team, client: TestClient, caller: _Caller
) -> None:
    _as(caller, team.owner)
    shown = client.get("/team").json()
    assert (shown["owner"], shown["owns"]) == (True, True)
    moved = _transfer(client, team.member)
    assert moved.status_code == 200, moved.text
    assert (moved.json()["owner"], moved.json()["owns"]) == (False, False)
    assert moved.json()["members"][0]["id"] == team.member.id
    assert _owner_of(engine, team.member.tenant_id) == uuid.UUID(team.member.id)
    # The former owner holds nothing now; the new owner everything.
    assert _put_settings(client).json()["code"] == "settings_not_permitted"
    _as(caller, team.member)
    assert _put_settings(client).status_code == 200
    assert client.get("/team").json()["owns"] is True
    assert _remove(client, team.owner).status_code == 204


@pytest.mark.parametrize(
    ("who", "target", "status", "code"),
    [
        ("admin", "member", 403, "team_transfer_owner_only"),
        ("reviewer", "member", 403, "team_transfer_owner_only"),
        ("member", "member", 403, "team_transfer_owner_only"),
        ("owner", "owner", 422, "team_transfer_self"),
        ("owner", "other_member", 404, "team_member_not_found"),
        ("owner", "missing", 404, "team_member_not_found"),
        ("other_owner", "member", 404, "team_member_not_found"),
    ],
)
def test_a_transfer_is_refused(
    who: str,
    target: str,
    status: int,
    code: str,
    engine: Engine,
    team: _Team,
    client: TestClient,
    caller: _Caller,
) -> None:
    _as(caller, getattr(team, who))
    member = str(uuid.uuid4()) if target == "missing" else getattr(team, target)
    refused = _transfer(client, member)
    assert refused.status_code == status, refused.text
    assert refused.json()["code"] == code
    assert _owner_of(engine, team.owner.tenant_id) == uuid.UUID(team.owner.id)


def test_a_transfer_body_is_validated(team: _Team, client: TestClient, caller: _Caller) -> None:
    _as(caller, team.owner)
    assert client.post("/team/owner", json={}).status_code == 422
    assert client.post("/team/owner", json={"member_id": "x" * 65}).status_code == 422


# --------------------------------------------------------------------------- #
# The check and the action are atomic: revocation, removal, transfer
# --------------------------------------------------------------------------- #
#
# Each ordering is made deterministic with a held lock rather than left to
# the scheduler: one transaction is kept open on the main thread, and the
# other request is started on a thread and seen blocked on a lock in
# Postgres before the first commits.


def _client_as(engine: Engine, user: CurrentUser) -> TestClient:
    """A client of its own, so a request on a thread is made as one account."""
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.include_router(team_route.router, prefix="/team")
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def _blocked(engine: Engine, count: int = 1, timeout: float = 15.0) -> None:
    """Wait until ``count`` backends are waiting on a lock; fail if they never do."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            waiting: int = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if waiting >= count:
            return
        time.sleep(0.05)
    pytest.fail(f"expected {count} request(s) blocked on a lock; none was")


def _in_thread(call: Callable[[], Any]) -> Future[Any]:
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call)
    pool.shutdown(wait=False)
    return future


@pytest.fixture(name="paused")
def _paused(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[threading.Event, threading.Event]]:
    """Hold an action's transaction open just after its permission check passes."""
    checked, release = threading.Event(), threading.Event()
    real = permissions.require

    def require(**kwargs: Any) -> None:
        real(**kwargs)
        checked.set()
        assert release.wait(30), "the test never released the paused action"

    monkeypatch.setattr(permissions, "require", require)
    yield checked, release
    release.set()


def test_a_revocation_committed_first_refuses_the_waiting_approval(
    engine: Engine, team: _Team, client: TestClient
) -> None:
    pid = save(client)["id"]
    with (
        sessionmaker(bind=engine)() as revoking,
        cross_tenant(revoking, reason="an operator revokes"),
    ):
        assert roles.revoke_role(session=revoking, email=team.reviewer.email, role=Role.REVIEWER)
        # The reviewer's token still says reviewer; the request starts now.
        approving = _in_thread(lambda: approve(_client_as(engine, team.reviewer), pid, 1))
        _blocked(engine)
        assert not approving.done()
        revoking.commit()
    response = approving.result(timeout=30)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "approval_not_permitted"
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_an_approval_checked_first_finishes_before_the_revocation(
    engine: Engine,
    team: _Team,
    client: TestClient,
    paused: tuple[threading.Event, threading.Event],
) -> None:
    checked, release = paused
    pid = save(client)["id"]
    approving = _in_thread(lambda: approve(_client_as(engine, team.reviewer), pid, 1))
    assert checked.wait(15)

    def revoke() -> bool:
        with (
            sessionmaker(bind=engine)() as session,
            cross_tenant(session, reason="an operator revokes"),
        ):
            changed = roles.revoke_role(
                session=session, email=team.reviewer.email, role=Role.REVIEWER
            )
            session.commit()
            return changed

    revoking = _in_thread(revoke)
    _blocked(engine)
    assert not revoking.done()
    release.set()
    assert approving.result(timeout=30).status_code == 200
    assert revoking.result(timeout=30) is True
    assert revision_row(engine, pid, 1)["approved_user_id"] == uuid.UUID(team.reviewer.id)
    # The revocation applies from the next request.
    second = save(client)["id"]
    refused = approve(_client_as(engine, team.reviewer), second, 1)
    assert refused.json()["code"] == "approval_not_permitted"


def test_a_removal_committed_first_refuses_the_waiting_approval(
    engine: Engine, team: _Team, client: TestClient
) -> None:
    pid = save(client)["id"]
    with sessionmaker(bind=engine)() as removing:
        team_domain.remove(session=removing, user=team.admin, member_id=team.reviewer.id)
        approving = _in_thread(lambda: approve(_client_as(engine, team.reviewer), pid, 1))
        _blocked(engine)
        removing.commit()
    response = approving.result(timeout=30)
    assert response.status_code == 403, response.text
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_transfer_committed_first_refuses_the_former_owners_delete(
    engine: Engine, team: _Team, client: TestClient
) -> None:
    pid = save(client)["id"]
    with sessionmaker(bind=engine)() as transferring:
        team_domain.transfer_ownership(
            session=transferring, user=team.owner, member_id=team.member.id
        )
        deleting = _in_thread(lambda: _delete(_client_as(engine, team.owner), pid))
        approving = _in_thread(lambda: approve(_client_as(engine, team.owner), pid, 1))
        _blocked(engine, count=2)
        transferring.commit()
    deleted, approved = deleting.result(timeout=30), approving.result(timeout=30)
    assert deleted.status_code == 403, deleted.text
    assert deleted.json()["code"] == "project_delete_not_permitted"
    assert approved.status_code == 403, approved.text
    assert approved.json()["code"] == "approval_not_permitted"
    assert _project_exists(engine, pid)
    assert revision_row(engine, pid, 1)["approved_at"] is None


def test_a_delete_checked_first_finishes_before_the_transfer(
    engine: Engine,
    team: _Team,
    client: TestClient,
    paused: tuple[threading.Event, threading.Event],
) -> None:
    checked, release = paused
    pid = save(client)["id"]
    deleting = _in_thread(lambda: _delete(_client_as(engine, team.owner), pid))
    assert checked.wait(15)
    transferring = _in_thread(lambda: _transfer(_client_as(engine, team.owner), team.member))
    _blocked(engine)
    assert not transferring.done()
    release.set()
    assert deleting.result(timeout=30).status_code == 204
    assert transferring.result(timeout=30).status_code == 200
    assert not _project_exists(engine, pid)
    assert _owner_of(engine, team.owner.tenant_id) == uuid.UUID(team.member.id)


def test_two_transfers_queue_and_the_second_is_refused(engine: Engine, team: _Team) -> None:
    with sessionmaker(bind=engine)() as first:
        team_domain.transfer_ownership(session=first, user=team.owner, member_id=team.member.id)
        second = _in_thread(lambda: _transfer(_client_as(engine, team.owner), team.reviewer))
        _blocked(engine)
        first.commit()
    refused = second.result(timeout=30)
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "team_transfer_owner_only"
    assert _owner_of(engine, team.owner.tenant_id) == uuid.UUID(team.member.id)


@pytest.mark.parametrize("round_", range(4))
def test_two_transfers_racing_leave_one_owner(round_: int, engine: Engine, team: _Team) -> None:
    """Released together: exactly one succeeds, whichever the scheduler picks."""
    barrier = threading.Barrier(2)

    def to(member: CurrentUser) -> Callable[[], Any]:
        own = _client_as(engine, team.owner)

        def call() -> Any:
            barrier.wait()
            return _transfer(own, member)

        return call

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda call: call(), [to(team.member), to(team.reviewer)]))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 403], [r.text for r in results]
    winner = team.member if results[0].status_code == 200 else team.reviewer
    assert _owner_of(engine, team.owner.tenant_id) == uuid.UUID(winner.id)
