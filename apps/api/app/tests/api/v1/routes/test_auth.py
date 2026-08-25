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

from app.core.config import Environment, Settings
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
    with TestClient(create_app(settings)) as test_client:
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
