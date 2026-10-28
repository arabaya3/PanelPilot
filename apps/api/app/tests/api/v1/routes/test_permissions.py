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
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
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
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.core.tenancy import bind_tenant
from app.domain import roles
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
        orm.execute(text("DELETE FROM users WHERE tenant_id = :t"), {"t": found})
        orm.execute(text("DELETE FROM company_settings WHERE tenant_id = :t"), {"t": found})
        orm.commit()
        return uuid.UUID(str(found))


def _account(
    engine: Engine, tenant: uuid.UUID, email: str, *, age_days: int, grant: Role | None = None
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
        owner=_account(engine, a, "perm-owner@example.com", age_days=40),
        admin=_account(engine, a, "perm-admin@example.com", age_days=30, grant=Role.ADMIN),
        reviewer=_account(engine, a, "perm-reviewer@example.com", age_days=20, grant=Role.REVIEWER),
        member=_account(engine, a, "perm-member@example.com", age_days=10),
        other_owner=_account(engine, b, "perm-b-owner@example.com", age_days=40),
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


def test_a_legacy_tenant_keeps_its_first_account_as_owner(
    client: TestClient, caller: _Caller, engine: Engine
) -> None:
    """Accounts from before the checks, with no role rows at all."""
    legacy = _tenant(engine, "perm-route-legacy")
    first = _account(engine, legacy, "legacy-first@example.com", age_days=900)
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


@pytest.mark.parametrize("round_", range(6))
def test_approve_racing_delete_ends_consistent(
    round_: int, engine: Engine, team: _Team, client: TestClient
) -> None:
    """Reviewer approves while the owner deletes: one wins, the other is told why."""
    del round_
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
    if _project_exists(engine, pid):
        # The approval won: the delete waited for it, then refused an approved project.
        assert approved.status_code == 200, approved.text
        assert deleted.status_code == 422, deleted.text
        assert deleted.json()["code"] == "project_approved"
        row = revision_row(engine, pid, 1)
        assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    else:
        # The delete won: the approval waited for it, then found no revision.
        assert deleted.status_code == 204, deleted.text
        assert approved.status_code == 404, approved.text
        assert approved.json()["code"] == "revision_not_found"


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
