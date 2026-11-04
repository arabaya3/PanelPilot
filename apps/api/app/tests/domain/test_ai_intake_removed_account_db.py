"""M6b/M6d against Postgres: an AI intake account removed from its tenant.

Synthetic data only. The removal is the real ``team.remove`` (the owner's
"remove member"), and the caller is resolved the way production resolves it
(``auth.resolve_caller``: a deactivated account's token stops working at its
next request), so this shows what a removed account can still do: nothing.

What the removal does **not** do, by design, is checked too: the account's
saved revisions keep its decisions (they are inside the hashed record), its
used proposals stay, and its unused ones stay until the retention purge.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.deps import SessionDep
from app.api.v1.routes import design as design_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.core.tenancy import bind_tenant, cross_tenant_info
from app.domain import auth as auth_domain
from app.domain import design_ai, team
from app.models.schemas.auth import CurrentUser
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    error,
    pytestmark,
    revision_row,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    AI,
    _As,
    _created,
    _propose,
    _provider_fixture,
    _saved_ai,
    _Who,
    _who_fixture,
)


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["schema-unset", "pp-design-2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


@pytest.fixture(name="app")
def _live_app(engine: Engine, who: _Who) -> FastAPI:
    """The design routes, with the caller checked for liveness as production does."""
    factory = sessionmaker(bind=engine)

    def session() -> Iterator[Session]:
        opened = factory()
        try:
            yield opened
        finally:
            opened.close()

    def current(request: Request, db: SessionDep) -> CurrentUser:
        user = who.users[request.headers.get("x-test-user", "engineer")]
        # Production's liveness step (``get_current_user`` → ``authenticate``):
        # a removed account's still-unexpired token is refused here.
        auth_domain.resolve_caller(session=db, caller=user)
        return user

    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    app.dependency_overrides[deps.get_current_user] = current
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None
    install_exception_handlers(app)
    return app


def _usage(engine: Engine, user: CurrentUser) -> tuple[int, Any, Any]:
    with engine.connect() as conn:
        found: Any = conn.execute(
            text(
                "SELECT calls, lease_id, lease_expires_at FROM ai_intake_usage "
                "WHERE tenant_id=:t AND user_id=:u"
            ),
            {"t": uuid.UUID(user.tenant_id), "u": uuid.UUID(user.id)},
        ).one_or_none()
    return (int(found[0]), found[1], found[2]) if found else (0, None, None)


def _remove(engine: Engine, owner: CurrentUser, member: CurrentUser) -> None:
    with sessionmaker(bind=engine)() as session:
        bind_tenant(session, owner.tenant_id)
        team.remove(session=session, user=owner, member_id=member.id)
        session.commit()


def test_a_removed_account_can_no_longer_call_decide_or_delete(
    engine: Engine, app: FastAPI, provider: Any, who: _Who
) -> None:
    engineer, owner = who.users["engineer"], who.users["owner"]
    c, boss = _As(app, "engineer"), _As(app, "owner")
    revision_id, used = _saved_ai(c, provider)
    unused = _created(c, provider)
    before_record = revision_row(engine, revision_id, 1)["requirements"]
    # A call of the account's is in flight when it is removed (its lease is live).
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_intake_usage SET lease_id=:l, "
                "lease_expires_at=now() + interval '1 minute' WHERE user_id=:u"
            ),
            {"l": uuid.uuid4(), "u": uuid.UUID(engineer.id)},
        )
    calls_before, _, _ = _usage(engine, engineer)
    asked_before = len(provider.calls)

    _remove(engine, owner, engineer)

    # Every AI intake action of the removed account is refused at authentication:
    # nothing is counted, charged, sent, decided or deleted.
    refused = [
        _propose(c),
        c.get(f"{AI}/{unused['id']}"),
        c.post(f"{AI}/{unused['id']}/items/i1/decision", json={"decision": "accept"}),
        c.client.delete(f"{AI}/{unused['id']}", headers=c.headers),
    ]
    assert [r.status_code for r in refused] == [401] * 4
    assert len(provider.calls) == asked_before
    assert _usage(engine, engineer)[0] == calls_before

    # The tenant's owner still sees the removed account's proposals, but cannot
    # delete them (only their author can: an open owner decision, §7.5).
    seen = boss.get(f"{AI}/{unused['id']}")
    assert seen.status_code == 200
    assert seen.json()["created_by_user_id"] == engineer.id
    not_yours = boss.client.delete(f"{AI}/{unused['id']}", headers=boss.headers)
    assert not_yours.status_code == 403
    assert error(not_yours)["code"] == "ai_proposal_not_yours"
    # The removed account's lease holds up nobody else.
    assert _propose(boss).status_code == 201

    # The saved revision keeps the removed account's decisions, unchanged.
    after_record = revision_row(engine, revision_id, 1)["requirements"]
    assert after_record == before_record
    assert after_record["ai"]["decisions"]
    assert all(d["by_user_id"] == engineer.id for d in after_record["ai"]["decisions"])

    # The retention purge removes the unused proposal; the used one stays.
    factory = sessionmaker(bind=engine, info=cross_tenant_info("test: retention purge"))
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_ai_proposals SET created_at = now() - interval '400 days' "
                "WHERE id = ANY(:i)"
            ),
            {"i": [uuid.UUID(unused["id"]), uuid.UUID(used["id"])]},
        )
    with factory() as session:
        assert design_ai.purge_unused_proposals(session=session, older_than_days=30) >= 1
        session.commit()
    with engine.connect() as conn:
        left = {
            str(r[0])
            for r in conn.execute(
                text("SELECT id FROM design_ai_proposals WHERE created_by_user_id=:u"),
                {"u": uuid.UUID(engineer.id)},
            ).all()
        }
    assert left == {used["id"]}


def _set_tenant_active(engine: Engine, tenant: str, active: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tenants SET is_active = :a WHERE id = :t"),
            {"a": active, "t": uuid.UUID(tenant)},
        )


@pytest.mark.parametrize("gone", ["member_removed", "tenant_deactivated"])
def test_an_account_removed_while_the_provider_answers_stores_nothing(
    engine: Engine, app: FastAPI, who: _Who, gone: str
) -> None:
    from app.tests.domain.ai_intake_support import TEXT, StubProvider, complete_payload

    engineer, owner = who.users["engineer"], who.users["owner"]
    removed_mid_call: list[bool] = []

    class RemovedMidCall(StubProvider):
        def propose(self, *, description: str, boards: list[str]) -> Any:
            # While the model is still answering, the owner removes the
            # member, or the whole tenant is deactivated.
            if gone == "member_removed":
                _remove(engine, owner, engineer)
            else:
                _set_tenant_active(engine, engineer.tenant_id, False)
            removed_mid_call.append(True)
            return super().propose(description=description, boards=boards)

    slow = RemovedMidCall(complete_payload())
    app.dependency_overrides[design_ai.get_proposal_provider] = lambda: slow
    c = _As(app, "engineer")
    calls_before = _usage(engine, engineer)[0]

    try:
        late = _propose(c, TEXT)
    finally:
        # The tenants are shared by the module's tests.
        _set_tenant_active(engine, engineer.tenant_id, True)
    assert late.status_code == 401, late.text[:400]
    assert removed_mid_call == [True]
    assert len(slow.calls) == 1
    # Nothing stored; the call stays counted (it reached the provider); the
    # lease is released.
    with engine.connect() as conn:
        stored: int = conn.execute(
            text("SELECT count(*) FROM design_ai_proposals WHERE created_by_user_id=:u"),
            {"u": uuid.UUID(engineer.id)},
        ).scalar_one()
    assert stored == 0
    calls, lease, _ = _usage(engine, engineer)
    assert calls == calls_before + 1
    assert lease is None
    # The description is not echoed in the refusal.
    assert TEXT not in late.text
