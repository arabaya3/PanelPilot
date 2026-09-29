"""Tests for `app/api/v1/routes/auth.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

These go through the real HTTP surface. The domain tests prove the rules; these
prove a client can actually reach them, which is the half BE-002's acceptance
criterion is written in terms of ("a new user can sign up and get a scoped,
working session").
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import deps
from app.core.config import Environment, Settings
from app.domain.rate_limit import (
    SIGNUP_POLICY,
    TRIAL_RESUME_POLICY,
    InMemoryRateLimitStore,
    check_trial_start_rate_limit,
)
from app.main import create_app

_SLUG_PREFIX = "routetest-"


def _database_available() -> bool:
    try:
        engine = create_engine(os.environ.get("DATABASE_URL", "").replace("+psycopg", "+psycopg"))
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient against the real app, wired to the live database."""
    settings = Settings(
        environment=Environment.DEV,
        database_url=os.environ["DATABASE_URL"],
        opensearch_url=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"),
        anthropic_api_key="test-key",
        jwt_secret="x" * 48,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
    app = create_app(settings)
    # A fresh in-memory limiter per test. The real one is process-wide (and
    # Redis-backed across runs), so this module's signups would otherwise
    # spend the per-address signup allowance of every later test.
    store = InMemoryRateLimitStore()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM refresh_tokens WHERE tenant_id IN "
                "(SELECT id FROM tenants WHERE slug LIKE :p)"
            ),
            {"p": f"{_SLUG_PREFIX}%"},
        )
        conn.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": f"{_SLUG_PREFIX}%"})
        conn.execute(text("DELETE FROM tenants WHERE slug LIKE :p"), {"p": f"{_SLUG_PREFIX}%"})


def _email() -> str:
    return f"{_SLUG_PREFIX}{uuid.uuid4().hex[:8]}@example.com"


PASSWORD = "correct horse battery"


@requires_db
def test_signup_returns_a_usable_token_pair(client: TestClient) -> None:
    """The acceptance criterion: sign up and get a scoped, working session."""
    response = client.post("/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@requires_db
def test_the_issued_token_authenticates_a_protected_route(client: TestClient) -> None:
    """A working session means the token actually opens a door.

    get_current_user was NotImplementedError before this task, so every
    protected route returned 500 no matter how good the token was.
    """
    signup = client.post(
        "/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD}
    ).json()
    response = client.get(
        "/api/v1/auth/quota",
        headers={"Authorization": f"Bearer {signup['access_token']}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["questions_used"] == 0


@requires_db
def test_a_protected_route_refuses_a_missing_or_bad_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/quota").status_code == 401
    assert (
        client.get(
            "/api/v1/auth/quota", headers={"Authorization": "Bearer not-a-token"}
        ).status_code
        == 401
    )


@requires_db
def test_login_round_trips(client: TestClient) -> None:
    email = _email()
    client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


@requires_db
def test_login_with_a_wrong_password_is_unauthorised(client: TestClient) -> None:
    email = _email()
    client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert response.status_code == 401


@requires_db
def test_refresh_rotates_over_http(client: TestClient) -> None:
    signup = client.post(
        "/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD}
    ).json()
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": signup["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["refresh_token"] != signup["refresh_token"]

    # Replay of the spent token is refused.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": signup["refresh_token"]})
    assert replay.status_code == 401


@requires_db
def test_duplicate_signup_is_a_client_error_not_a_crash(client: TestClient) -> None:
    email = _email()
    client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    response = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 422, response.text


@requires_db
def test_a_short_password_is_rejected_before_reaching_the_domain(
    client: TestClient,
) -> None:
    """Schema validation, so a weak password never reaches the hasher."""
    response = client.post("/api/v1/auth/signup", json={"email": _email(), "password": "short"})
    assert response.status_code == 422


@requires_db
def test_an_oversized_full_name_is_a_422_not_a_database_error(client: TestClient) -> None:
    """users.full_name is String(200); longer used to reach the INSERT as a 500."""
    response = client.post(
        "/api/v1/auth/signup",
        json={"email": _email(), "password": PASSWORD, "full_name": "x" * 201},
    )
    assert response.status_code == 422


@requires_db
def test_an_oversized_refresh_token_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "x" * 257})
    assert response.status_code == 422


@requires_db
def test_signup_is_rate_limited_per_address(client: TestClient) -> None:
    """Each signup is a bcrypt hash and a new tenant; unlimited, both are free."""
    for _ in range(SIGNUP_POLICY.limit):
        client.post("/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD})
    refused = client.post("/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


@requires_db
def test_auth_responses_are_never_cached(client: TestClient) -> None:
    """They carry tokens and claim secrets."""
    response = client.post("/api/v1/auth/signup", json={"email": _email(), "password": PASSWORD})
    assert response.headers["Cache-Control"] == "no-store"


# --- trial start and resume ----------------------------------------------------


def _cleanup_trial(trial: dict[str, object]) -> None:
    """Delete a trial this module started; its slug is not ours to match."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        tenant_id: object | None = conn.execute(
            text("SELECT tenant_id FROM anonymous_sessions WHERE id = :i"),
            {"i": trial["session_id"]},
        ).scalar_one_or_none()
        if tenant_id is None:
            return
        for table in ("anonymous_sessions", "diagnostic_sessions"):
            conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})
        conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})


@requires_db
def test_a_trial_can_be_resumed_with_its_secret(client: TestClient) -> None:
    started = client.post("/api/v1/auth/trial").json()
    try:
        response = client.post(
            "/api/v1/auth/trial/resume",
            json={"session_id": started["session_id"], "claim_secret": started["claim_secret"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["session_id"] == started["session_id"]
        assert body["claim_secret"] == started["claim_secret"]
        assert body["access_token"]
        assert body["questions_remaining"] == started["questions_remaining"]

        # The resumed token actually opens a door, scoped to the trial.
        quota = client.get(
            "/api/v1/auth/quota", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert quota.status_code == 200, quota.text
    finally:
        _cleanup_trial(started)


@requires_db
def test_a_trial_cannot_be_resumed_with_the_wrong_secret(client: TestClient) -> None:
    started = client.post("/api/v1/auth/trial").json()
    try:
        response = client.post(
            "/api/v1/auth/trial/resume",
            json={"session_id": started["session_id"], "claim_secret": "not-the-secret"},
        )
        assert response.status_code == 401
    finally:
        _cleanup_trial(started)


@requires_db
def test_an_unknown_trial_cannot_be_resumed(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/trial/resume",
        json={"session_id": str(uuid.uuid4()), "claim_secret": "anything"},
    )
    assert response.status_code == 401


class _NoRowsSession:
    """A session in which no row exists, for paths that only look things up."""

    def get(self, *_args: object) -> None:
        """Find nothing."""
        return None

    def commit(self) -> None:
        """Nothing to commit."""


def test_resume_has_its_own_budget() -> None:
    """Resuming mints nothing, and the web app resumes on every page load.

    Sharing the ten-an-hour start budget let a few engineers on one site
    address, reloading, lock each other out of trials they already held. It is
    still bounded, on its own counter.

    Needs no database: the limit is decided before the domain is reached, and
    an unknown session is refused with a 401 without writing anything.
    """
    from app.core.db import get_session

    settings = Settings(
        environment=Environment.DEV,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        opensearch_url="http://localhost:9200",
        anthropic_api_key="test-key",
        jwt_secret="x" * 48,
        redis_url="redis://localhost:6379/0",
    )
    app = create_app(settings)
    store = InMemoryRateLimitStore()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store
    app.dependency_overrides[get_session] = _NoRowsSession
    body = {"session_id": str(uuid.uuid4()), "claim_secret": "anything"}
    with TestClient(app) as test_client:
        for _ in range(TRIAL_RESUME_POLICY.limit):
            assert test_client.post("/api/v1/auth/trial/resume", json=body).status_code == 401
        refused = test_client.post("/api/v1/auth/trial/resume", json=body)
        assert refused.status_code == 429
    # The start budget is untouched by all that resuming.
    assert check_trial_start_rate_limit(store=store, client_ip="testclient") == 1
