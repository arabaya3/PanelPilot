"""M6b S2/S3 against Postgres: per-account AI intake limits, consent, deletion, retention.

Synthetic data only. The per-account cap and the one-call-in-flight lease are
exercised through the API and, for races, with real concurrent sessions on
their own connections. What reaches the provider is asserted on the request
the real ``LlmProposalProvider`` builds, with a recording client.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai import intake_proposer
from app.api import deps
from app.core.config import get_settings
from app.core.errors import ConflictError, TooManyRequestsError
from app.core.tenancy import cross_tenant_info
from app.domain import ai_intake_usage, design_ai, model_budget
from app.models.schemas.auth import CurrentUser, Role
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _account_row,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    error,
    pytestmark,
)
from app.tests.domain.ai_intake_support import TEXT, StubProvider, complete_payload

BASE = "/design/projects/requirements/ai-proposals"


@pytest.fixture(autouse=True, params=[None, "pp-design/2"], ids=["schema-unset", "pp-design-2"])
def _schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param is None:
        monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("DESIGN_SNAPSHOT_SCHEMA", request.param)


@pytest.fixture(name="stub")
def _stub(app: FastAPI, engine: Engine) -> StubProvider:
    provider = StubProvider(complete_payload())
    app.dependency_overrides[design_ai.get_proposal_provider] = lambda: provider
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE design_ai_proposals CASCADE"))
        conn.execute(text("TRUNCATE ai_intake_usage"))
        conn.execute(text("DELETE FROM model_usage"))
    return provider


@pytest.fixture(name="cap")
def _cap(monkeypatch: pytest.MonkeyPatch) -> Any:
    def set_cap(value: int | None) -> None:
        monkeypatch.setattr(get_settings(), "ai_intake_calls_per_user_per_month", value)

    return set_cap


def _propose(client: TestClient, **extra: Any) -> Any:
    return client.post(BASE, json={"description": TEXT, "ai_consent": True, **extra})


def _usage(engine: Engine, user: CurrentUser) -> tuple[int, Any]:
    with engine.connect() as conn:
        found: Any = conn.execute(
            text("SELECT calls, lease_id FROM ai_intake_usage WHERE tenant_id=:t AND user_id=:u"),
            {"t": uuid.UUID(user.tenant_id), "u": uuid.UUID(user.id)},
        ).one_or_none()
    return (int(found[0]), found[1]) if found else (0, None)


def _tenant_calls(engine: Engine, tenant: str) -> int:
    with engine.connect() as conn:
        found: Any = conn.execute(
            text("SELECT coalesce(sum(calls), 0) FROM model_usage WHERE tenant_id=:t"),
            {"t": uuid.UUID(tenant)},
        ).scalar_one()
    return int(found)


def _count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM design_ai_proposals")).scalar_one())


def _member(engine: Engine, tenant: str, email: str) -> CurrentUser:
    user = CurrentUser(
        id=str(uuid.uuid4()), email=email, tenant_id=tenant, roles=frozenset({Role.ENGINEER})
    )
    _account_row(engine, user, None)
    return user


# --------------------------------------------------------------------------- #
# Consent (S3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("body", [{}, {"ai_consent": False}])
def test_without_consent_nothing_is_sent_counted_or_charged(
    client: TestClient,
    engine: Engine,
    stub: StubProvider,
    caller: _Caller,
    body: dict[str, Any],
) -> None:
    refused = client.post(BASE, json={"description": TEXT, **body})
    assert refused.status_code == 422
    assert error(refused)["code"] == "ai_consent_required"
    assert stub.calls == []
    assert _usage(engine, caller.user) == (0, None)
    assert _tenant_calls(engine, caller.user.tenant_id) == 0
    assert _count(engine) == 0


def test_consent_is_recorded_on_the_proposal(
    client: TestClient, engine: Engine, stub: StubProvider, caller: _Caller
) -> None:
    made = _propose(client)
    assert made.status_code == 201, made.text[:400]
    body = made.json()
    assert body["ai_consent_by_user_id"] == caller.user.id
    assert body["ai_consent_at"] == body["created_at"]
    # Read back without the create-only allowance.
    again = client.get(f"{BASE}/{body['id']}").json()
    assert again["ai_consent_at"] == body["ai_consent_at"]
    assert again["calls_remaining"] is None


# --------------------------------------------------------------------------- #
# Per-account cap and the call in flight (S2)
# --------------------------------------------------------------------------- #


def test_the_monthly_per_account_cap(
    client: TestClient, engine: Engine, stub: StubProvider, caller: _Caller, cap: Any
) -> None:
    cap(2)
    first = _propose(client)
    assert first.status_code == 201
    assert first.json()["calls_remaining"] == 1
    second = _propose(client)
    assert second.json()["calls_remaining"] == 0
    third = _propose(client)
    assert third.status_code == 429
    body = error(third)
    assert body["code"] == "ai_user_limit_exceeded"
    assert body["params"]["limit"] == "2"
    assert body["params"]["period"] == model_budget.current_period()
    assert int(third.headers["Retry-After"]) > 0
    # The refusal reached nobody and charged nothing.
    assert len(stub.calls) == 2
    assert _usage(engine, caller.user)[0] == 2
    assert _tenant_calls(engine, caller.user.tenant_id) == 2
    # Unset: no per-account cap.
    cap(None)
    assert _propose(client).json()["calls_remaining"] is None


def test_the_tenant_ceiling_refusal_counts_nothing_for_the_account(
    client: TestClient, engine: Engine, stub: StubProvider, caller: _Caller, monkeypatch: Any
) -> None:
    monkeypatch.setattr(get_settings(), "model_calls_per_month", 1)
    assert _propose(client).status_code == 201
    refused = _propose(client)
    assert refused.status_code == 429
    assert error(refused)["code"] == "model_budget_exceeded"
    calls, lease = _usage(engine, caller.user)
    assert (calls, lease) == (1, None)


@pytest.mark.parametrize(
    "raised",
    [
        intake_proposer.ProviderUnavailable("down"),
        intake_proposer.ProviderTimeout("slow"),
        intake_proposer.ProviderMalformed("no tool"),
        RuntimeError("a bug"),
    ],
    ids=["unavailable", "timeout", "malformed", "crash"],
)
def test_a_failed_call_counts_once_and_releases_its_lease(
    client: TestClient,
    engine: Engine,
    stub: StubProvider,
    caller: _Caller,
    raised: Exception,
) -> None:
    stub.raises = raised
    failed = _propose(client)
    assert failed.status_code >= 500
    # Counted and charged like the tenant's call (the provider was asked);
    # not retried; the lease is gone, so the next call is not "in flight".
    assert len(stub.calls) == 1
    assert _usage(engine, caller.user) == (1, None)
    assert _tenant_calls(engine, caller.user.tenant_id) == 1
    stub.raises = None
    assert _propose(client).status_code == 201


def test_a_call_past_the_deadline_releases_its_lease(
    client: TestClient, engine: Engine, app: FastAPI, caller: _Caller, monkeypatch: Any
) -> None:
    stub = StubProvider(complete_payload())
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE ai_intake_usage"))
    gate = threading.Event()

    class Slow(StubProvider):
        def propose(self, *, description: str, boards: list[str]) -> Any:
            gate.wait(5)
            return stub.propose(description=description, boards=boards)

    app.dependency_overrides[design_ai.get_proposal_provider] = lambda: Slow()
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None
    monkeypatch.setattr(design_ai, "PROVIDER_DEADLINE_S", 0.2)
    late = _propose(client)
    gate.set()
    assert late.status_code == 504
    assert _usage(engine, caller.user) == (1, None)


def test_a_live_lease_refuses_and_an_expired_one_does_not(
    client: TestClient, engine: Engine, stub: StubProvider, caller: _Caller
) -> None:
    assert _propose(client).status_code == 201
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_intake_usage SET lease_id=:l, lease_expires_at=now() + interval '1 minute'"
                " WHERE user_id=:u"
            ),
            {"l": uuid.uuid4(), "u": uuid.UUID(caller.user.id)},
        )
    busy = _propose(client)
    assert busy.status_code == 409
    assert error(busy)["code"] == "ai_request_in_flight"
    assert len(stub.calls) == 1
    assert _usage(engine, caller.user)[0] == 1
    # A worker that died mid-call: its lease lapses and the next call goes ahead.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_intake_usage SET lease_expires_at=now() - interval '1 second'"
                " WHERE user_id=:u"
            ),
            {"u": uuid.UUID(caller.user.id)},
        )
    assert _propose(client).status_code == 201
    assert _usage(engine, caller.user) == (2, None)


def test_limits_are_per_tenant_and_account(
    client: TestClient,
    engine: Engine,
    stub: StubProvider,
    caller: _Caller,
    tenants: tuple[str, str],
    cap: Any,
) -> None:
    cap(1)
    owner = caller.user
    assert _propose(client).status_code == 201
    assert _propose(client).status_code == 429
    # Another account of the same tenant has its own month.
    caller.user = _member(engine, tenants[0], "eng-a2@example.com")
    assert _propose(client).status_code == 201
    # The same account id in another tenant is another (tenant, account).
    caller.user = CurrentUser(
        id=owner.id, email="eng-b@example.com", tenant_id=tenants[1], roles=owner.roles
    )
    assert _propose(client).status_code == 201
    # A live lease of one account does not hold up another.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_intake_usage SET lease_id=:l, lease_expires_at=now() + interval '1 minute'"
                " WHERE user_id=:u AND tenant_id=:t"
            ),
            {"l": uuid.uuid4(), "u": uuid.UUID(owner.id), "t": uuid.UUID(tenants[0])},
        )
    cap(None)
    assert _propose(client).status_code == 201


# --------------------------------------------------------------------------- #
# Races, with real concurrent sessions
# --------------------------------------------------------------------------- #


def _race(
    engine: Engine, tenant: str, user: str, runners: int, *, release: bool, limit: int | None
) -> list[str]:
    factory = sessionmaker(bind=engine)
    start = threading.Barrier(runners)
    outcomes: list[str] = []
    lock = threading.Lock()

    def one() -> None:
        session: Session = factory()
        try:
            start.wait(10)
            lease = ai_intake_usage.acquire(
                session=session,
                tenant_id=uuid.UUID(tenant),
                user_id=uuid.UUID(user),
                limit=limit,
            )
            model_budget.charge_model_call(session=session, tenant_id=tenant)
            session.commit()
            if release:
                ai_intake_usage.release(session=session, lease=lease)
            outcome = "ok"
        except ConflictError as exc:
            session.rollback()
            outcome = str(exc.code)
        except TooManyRequestsError as exc:
            session.rollback()
            outcome = str(exc.code)
        finally:
            session.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=one) for _ in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return outcomes


@pytest.fixture(name="fresh")
def _fresh(engine: Engine, tenants: tuple[str, str]) -> tuple[str, str]:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE ai_intake_usage"))
        conn.execute(text("DELETE FROM model_usage"))
    return tenants[0], str(uuid.uuid4())


def test_parallel_calls_of_one_account_get_one_lease(
    engine: Engine, fresh: tuple[str, str]
) -> None:
    tenant, user = fresh
    outcomes = _race(engine, tenant, user, 12, release=False, limit=None)
    assert sorted(outcomes) == ["ai_request_in_flight"] * 11 + ["ok"]
    with engine.connect() as conn:
        calls: int = conn.execute(
            text("SELECT calls FROM ai_intake_usage WHERE user_id=:u"), {"u": uuid.UUID(user)}
        ).scalar_one()
    assert calls == 1
    assert _tenant_calls(engine, tenant) == 1


def test_parallel_calls_never_pass_the_cap(engine: Engine, fresh: tuple[str, str]) -> None:
    tenant, user = fresh
    for _ in range(3):
        assert _race(engine, tenant, user, 1, release=True, limit=4) == ["ok"]
    outcomes = _race(engine, tenant, user, 12, release=True, limit=4)
    assert outcomes.count("ok") <= 1
    assert set(outcomes) <= {"ok", "ai_request_in_flight", "ai_user_limit_exceeded"}
    with engine.connect() as conn:
        calls: int = conn.execute(
            text("SELECT calls FROM ai_intake_usage WHERE user_id=:u"), {"u": uuid.UUID(user)}
        ).scalar_one()
    assert calls == 3 + outcomes.count("ok") <= 4
    assert _tenant_calls(engine, tenant) == calls


def test_a_new_month_starts_the_count_again(engine: Engine, fresh: tuple[str, str]) -> None:
    tenant, user = fresh
    factory = sessionmaker(bind=engine)
    may = datetime(2026, 5, 31, 23, 59, tzinfo=UTC)
    with factory() as session:
        args: dict[str, Any] = {
            "session": session,
            "tenant_id": uuid.UUID(tenant),
            "user_id": uuid.UUID(user),
        }
        lease = ai_intake_usage.acquire(**args, limit=1, now=may)
        session.commit()
        ai_intake_usage.release(session=session, lease=lease)
        with pytest.raises(ai_intake_usage.AiUserLimitExceededError) as refused:
            ai_intake_usage.acquire(**args, limit=1, now=may + timedelta(seconds=30))
        session.rollback()
        assert refused.value.retry_after_seconds == 31
        assert ai_intake_usage.acquire(**args, limit=1, now=may + timedelta(minutes=2)).used == 1


# --------------------------------------------------------------------------- #
# What the provider receives
# --------------------------------------------------------------------------- #


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Block:
            type = "tool_use"
            name = intake_proposer.TOOL_NAME
            input = complete_payload()

        class _Message:
            stop_reason = "tool_use"

            def __init__(self) -> None:
                self.content = [_Block()]

        return _Message()


def test_the_provider_receives_only_the_prompt_the_description_and_board_names(
    client: TestClient, app: FastAPI, engine: Engine, stub: StubProvider
) -> None:
    recording = _RecordingClient()
    app.dependency_overrides[design_ai.get_proposal_provider] = (
        lambda: intake_proposer.LlmProposalProvider(recording)
    )
    draft = {
        "info": {"name": "SECRET-PROJECT", "number": "SECRET-42", "client": "SECRET-CLIENT"},
        "boards": [
            {
                "name": "MDB",
                "supply": {"voltage_v": 400},
                "loads": [{"description": "SECRET-LOAD", "power_kw": "9"}],
            },
            {"name": "DB-1"},
        ],
    }
    made = _propose(client, request=draft)
    assert made.status_code == 201, made.text[:400]
    assert len(recording.calls) == 1
    sent = recording.calls[0]
    assert sent["system"] == intake_proposer.SYSTEM_PROMPT
    assert sent["messages"] == [
        {
            "role": "user",
            "content": (
                "<existing_boards>\nMDB\nDB-1\n</existing_boards>\n\n"
                f"<description>\n{TEXT}\n</description>\n\n"
                "Offsets count from the first character after the line break that follows "
                "<description>."
            ),
        }
    ]
    assert set(sent) == {"model", "max_tokens", "system", "messages", "tools", "tool_choice"}
    assert sent["tools"] == [intake_proposer.tool_definition()]
    flat = repr(sent)
    for leaked in ("SECRET", "eng-a@example.com", "adv-export", "voltage", "supply"):
        assert leaked not in flat


# --------------------------------------------------------------------------- #
# Deleting one's own unused proposal, and the retention purge
# --------------------------------------------------------------------------- #


def test_an_account_deletes_its_own_unused_proposal_only(
    client: TestClient,
    engine: Engine,
    stub: StubProvider,
    caller: _Caller,
    tenants: tuple[str, str],
) -> None:
    owner = caller.user
    mine = _propose(client).json()["id"]
    used = _propose(client).json()["id"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE design_ai_proposals SET used_at = now() WHERE id = :i"),
            {"i": uuid.UUID(used)},
        )
    refused = client.delete(f"{BASE}/{used}")
    assert refused.status_code == 409
    assert error(refused)["code"] == "ai_proposal_used"
    caller.user = _member(engine, tenants[0], "eng-a3@example.com")
    assert error(client.delete(f"{BASE}/{mine}"))["code"] == "ai_proposal_not_yours"
    caller.user = CurrentUser(
        id=owner.id, email=owner.email, tenant_id=tenants[1], roles=owner.roles
    )
    assert error(client.delete(f"{BASE}/{mine}"))["code"] == "ai_proposal_not_found"
    caller.user = owner
    assert client.delete(f"{BASE}/{mine}").status_code == 204
    assert client.get(f"{BASE}/{mine}").status_code == 404
    assert client.delete(f"{BASE}/{mine}").status_code == 404
    assert _count(engine) == 1


def test_the_purge_removes_only_old_unused_proposals(
    client: TestClient, engine: Engine, stub: StubProvider, monkeypatch: Any
) -> None:
    ids = [_propose(client).json()["id"] for _ in range(4)]
    old_unused, old_used, new_unused, old_revision_gone = ids
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_ai_proposals SET created_at = now() - interval '31 days' "
                "WHERE id = ANY(:i)"
            ),
            {"i": [uuid.UUID(i) for i in (old_unused, old_used, old_revision_gone)]},
        )
        # Applied, and applied by a revision since deleted (SET NULL): both kept.
        conn.execute(
            text("UPDATE design_ai_proposals SET used_at = now() WHERE id = ANY(:i)"),
            {"i": [uuid.UUID(old_used), uuid.UUID(old_revision_gone)]},
        )
    factory = sessionmaker(bind=engine, info=cross_tenant_info("test: retention purge"))
    monkeypatch.setattr(get_settings(), "ai_intake_unused_retention_days", 30)
    with factory() as session:
        assert design_ai.purge_unused_proposals(session=session) == 1
        session.commit()
    with engine.connect() as conn:
        left = {str(r[0]) for r in conn.execute(text("SELECT id FROM design_ai_proposals")).all()}
    assert left == {old_used, new_unused, old_revision_gone}
    # The worker job does the same through its own session.
    from app.worker import jobs

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_ai_proposals SET created_at = now() - interval '3 days' "
                "WHERE id = :i"
            ),
            {"i": uuid.UUID(new_unused)},
        )
    assert jobs.get_job("purge-ai-proposals").handler(["--days", "2"]) == 0
    assert _count(engine) == 2
    assert jobs.get_job("purge-ai-proposals").handler(["--days", "0"]) == 2
