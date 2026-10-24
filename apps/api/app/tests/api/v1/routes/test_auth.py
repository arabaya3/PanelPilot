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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import deps
from app.api.csrf import CSRF_HEADER
from app.api.v1.routes.auth import (
    SECURE_SESSION_COOKIE,
    SESSION_COOKIE,
    _clear_session_cookie,
    _set_session_cookie,
)
from app.core.config import Environment, Settings
from app.core.security import create_access_token, decode_access_token, hash_refresh_token
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
        openai_api_key="test-key",
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
    _delete_rows()


def _delete_rows() -> None:
    """Delete what this module created; signed-in sessions go with their users."""
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
    # The refresh token travels only in the HttpOnly cookie, never the body.
    assert "refresh_token" not in body
    assert body["token_type"] == "bearer"
    assert client.cookies.get(SESSION_COOKIE)


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

    def __init__(self) -> None:
        # Where the domain records a tenant binding or a cross-tenant reason.
        self.info: dict[str, object] = {}

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
        openai_api_key="test-key",
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


# --- the refresh session cookie ------------------------------------------------

_SESSION = "/api/v1/auth/session"
_REFRESH = f"{_SESSION}/refresh"
# What the web app sends with every cookie request: its own origin and the header.
SAME_SITE = {"Origin": "http://localhost:3000", CSRF_HEADER: "1"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign_up(client: TestClient, email: str | None = None) -> str:
    """Sign up in this client's cookie jar; return the access token."""
    response = client.post(
        "/api/v1/auth/signup", json={"email": email or _email(), "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["access_token"]
    return token


def _cookie_attributes(header: str) -> dict[str, str]:
    """Parse one Set-Cookie header's attributes, lower-cased by name."""
    attributes = {}
    for part in [piece.strip() for piece in header.split(";")][1:]:
        name, _, value = part.partition("=")
        attributes[name.lower()] = value
    return attributes


def _hold_cookie(browser: TestClient, value: str) -> None:
    """Put a session cookie in a browser's jar, as the server would have."""
    # The jar files a dotless host under ".local" (RFC 2965), so a cookie the
    # server set for `testserver` lives at `testserver.local`.
    browser.cookies.set(
        SESSION_COOKIE, value, domain="testserver.local", path="/api/v1/auth/session"
    )


def _second_browser(client: TestClient) -> TestClient:
    """Another browser on the same app: its own cookie jar."""
    return TestClient(client.app)


def _settings(environment: Environment, *, secure: bool | None = None) -> Settings:
    return Settings(
        environment=environment,
        database_url="postgresql+psycopg://u:p@localhost:5432/x",
        opensearch_url="http://localhost:9200",
        anthropic_api_key="test-key",
        openai_api_key="test-key",
        jwt_secret="x" * 48,
        redis_url="redis://localhost:6379/0",
        auth_cookie_secure=secure,
    )


def test_the_session_cookie_attributes_outside_dev() -> None:
    """HttpOnly, Secure, SameSite=Strict, `__Host-` (Path=/, no Domain), for 30 days.

    The prefix stops a sibling subdomain tossing a session cookie of its choosing.
    """
    response = Response()
    _set_session_cookie(response, token="t", settings=_settings(Environment.STAGING))
    header = response.headers["set-cookie"]
    attributes = _cookie_attributes(header)
    assert header.startswith(f"{SECURE_SESSION_COOKIE}=t;")
    assert "httponly" in attributes
    assert "secure" in attributes
    assert attributes["samesite"] == "strict"
    assert attributes["path"] == "/"
    assert attributes["max-age"] == str(30 * 24 * 3600)
    assert "domain" not in attributes


def test_the_session_cookie_is_not_secure_in_dev_unless_asked() -> None:
    plain = Response()
    _set_session_cookie(plain, token="t", settings=_settings(Environment.DEV))
    assert "secure" not in _cookie_attributes(plain.headers["set-cookie"])
    forced = Response()
    _set_session_cookie(forced, token="t", settings=_settings(Environment.DEV, secure=True))
    assert "secure" in _cookie_attributes(forced.headers["set-cookie"])


@requires_db
def test_signup_and_login_set_the_session_cookie(client: TestClient) -> None:
    email = _email()
    signup = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    for response in (signup, login):
        attributes = _cookie_attributes(response.headers["set-cookie"])
        assert "httponly" in attributes
        assert attributes["samesite"] == "strict"
        assert attributes["path"] == "/api/v1/auth/session"
        assert "refresh_token" not in response.json()


@requires_db
def test_refresh_by_cookie_rotates_it_and_restores_the_session(client: TestClient) -> None:
    """A reload: no token in memory, only the cookie, and the account is back."""
    first = _sign_up(client)
    before = client.cookies.get(SESSION_COOKIE)

    restored = client.post(_REFRESH, headers=SAME_SITE)
    assert restored.status_code == 200, restored.text
    assert "refresh_token" not in restored.json()
    after = client.cookies.get(SESSION_COOKIE)
    assert after
    assert after != before
    token = restored.json()["access_token"]
    assert decode_access_token(token).id == decode_access_token(first).id
    assert client.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 200


@requires_db
def test_refresh_without_a_cookie_is_a_401(client: TestClient) -> None:
    response = client.post(_REFRESH, headers=SAME_SITE)
    assert response.status_code == 401
    assert response.json()["code"] == "session_ended"


@requires_db
def test_an_oversized_cookie_is_a_401_not_a_crash(client: TestClient) -> None:
    _hold_cookie(client, "x" * 300)
    assert client.post(_REFRESH, headers=SAME_SITE).status_code == 401


@requires_db
def test_a_refused_refresh_clears_the_cookie(client: TestClient) -> None:
    _hold_cookie(client, "never-issued")
    response = client.post(_REFRESH, headers=SAME_SITE)
    assert response.status_code == 401
    assert _cookie_attributes(response.headers["set-cookie"])["max-age"] == "0"


# --- cross-site refusal


@requires_db
@pytest.mark.parametrize(
    "headers",
    [
        {CSRF_HEADER: "1"},
        {"Origin": "https://evil.example", CSRF_HEADER: "1"},
        {"Origin": "null", CSRF_HEADER: "1"},
        {"Referer": "https://evil.example/page", CSRF_HEADER: "1"},
    ],
    ids=["no-origin", "foreign-origin", "opaque-origin", "foreign-referer"],
)
def test_a_cross_site_refresh_is_refused(client: TestClient, headers: dict[str, str]) -> None:
    _sign_up(client)
    before = client.cookies.get(SESSION_COOKIE)
    response = client.post(_REFRESH, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_origin_refused"
    # Refused before the token was touched: it still refreshes from the web app.
    assert client.cookies.get(SESSION_COOKIE) == before
    assert client.post(_REFRESH, headers=SAME_SITE).status_code == 200


@requires_db
def test_a_refresh_without_the_custom_header_is_refused(client: TestClient) -> None:
    _sign_up(client)
    response = client.post(_REFRESH, headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_header_missing"


@requires_db
def test_the_referer_stands_in_for_a_missing_origin(client: TestClient) -> None:
    _sign_up(client)
    response = client.post(
        _REFRESH, headers={"Referer": "http://localhost:3000/design", CSRF_HEADER: "1"}
    )
    assert response.status_code == 200, response.text


@requires_db
@pytest.mark.parametrize("endpoint", ["logout", "logout-everywhere"])
def test_a_cross_site_logout_is_refused(client: TestClient, endpoint: str) -> None:
    """A forged logout is a nuisance, not a theft, but it is refused all the same."""
    token = _sign_up(client)
    response = client.post(
        f"{_SESSION}/{endpoint}",
        headers={"Origin": "https://evil.example", CSRF_HEADER: "1", **_bearer(token)},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_origin_refused"
    assert client.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 200


# --- logout


@requires_db
def test_after_logout_the_access_and_refresh_tokens_are_both_refused(
    client: TestClient,
) -> None:
    """Replay of either credential, copied before sign-out, fails at once."""
    token = _sign_up(client)
    copied_cookie = client.cookies.get(SESSION_COOKIE)
    assert copied_cookie

    response = client.post(f"{_SESSION}/logout", headers={**SAME_SITE, **_bearer(token)})
    assert response.status_code == 204
    assert _cookie_attributes(response.headers["set-cookie"])["max-age"] == "0"

    assert client.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 401
    thief = _second_browser(client)
    _hold_cookie(thief, copied_cookie)
    assert thief.post(_REFRESH, headers=SAME_SITE).status_code == 401


@requires_db
def test_logout_works_with_the_cookie_alone(client: TestClient) -> None:
    """The access token may have expired by the time the engineer signs out."""
    token = _sign_up(client)
    assert client.post(f"{_SESSION}/logout", headers=SAME_SITE).status_code == 204
    assert client.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 401


@requires_db
def test_logout_is_idempotent(client: TestClient) -> None:
    token = _sign_up(client)
    for _ in range(2):
        response = client.post(f"{_SESSION}/logout", headers={**SAME_SITE, **_bearer(token)})
        assert response.status_code == 204
    # Nothing at all: still 204, still says nothing.
    nobody = _second_browser(client)
    assert nobody.post(f"{_SESSION}/logout", headers=SAME_SITE).status_code == 204


@requires_db
def test_logout_leaves_the_accounts_other_sessions_alone(client: TestClient) -> None:
    email = _email()
    laptop_token = _sign_up(client, email)
    phone = _second_browser(client)
    phone_token = phone.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    ).json()["access_token"]

    client.post(f"{_SESSION}/logout", headers={**SAME_SITE, **_bearer(laptop_token)})

    assert client.get("/api/v1/auth/quota", headers=_bearer(laptop_token)).status_code == 401
    assert phone.get("/api/v1/auth/quota", headers=_bearer(phone_token)).status_code == 200
    assert phone.post(_REFRESH, headers=SAME_SITE).status_code == 200


@requires_db
def test_logout_cannot_end_another_accounts_session(client: TestClient) -> None:
    """The bearer names the caller's own session; another's is out of reach."""
    victim = _second_browser(client)
    victim_token = _sign_up(victim)
    attacker_token = _sign_up(client)
    client.post(f"{_SESSION}/logout", headers={**SAME_SITE, **_bearer(attacker_token)})
    assert victim.get("/api/v1/auth/quota", headers=_bearer(victim_token)).status_code == 200
    assert victim.post(_REFRESH, headers=SAME_SITE).status_code == 200


@requires_db
def test_logout_everywhere_ends_every_session_of_the_account(client: TestClient) -> None:
    email = _email()
    laptop_token = _sign_up(client, email)
    phone = _second_browser(client)
    phone_token = phone.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    ).json()["access_token"]
    stranger = _second_browser(client)
    stranger_token = _sign_up(stranger)

    response = client.post(
        f"{_SESSION}/logout-everywhere", headers={**SAME_SITE, **_bearer(laptop_token)}
    )
    assert response.status_code == 204

    for browser, token in ((client, laptop_token), (phone, phone_token)):
        assert browser.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 401
    assert phone.post(_REFRESH, headers=SAME_SITE).status_code == 401
    assert stranger.get("/api/v1/auth/quota", headers=_bearer(stranger_token)).status_code == 200


@requires_db
def test_logout_everywhere_needs_an_access_token(client: TestClient) -> None:
    _sign_up(client)
    assert client.post(f"{_SESSION}/logout-everywhere", headers=SAME_SITE).status_code == 401
    assert client.post(_REFRESH, headers=SAME_SITE).status_code == 200


# --- concurrency, expiry, switching accounts


@requires_db
def test_two_tabs_refreshing_at_once_both_stay_signed_in(client: TestClient) -> None:
    """Tabs share the browser's cookie; a simultaneous refresh is not a replay."""
    _sign_up(client)
    shared = client.cookies.get(SESSION_COOKIE)
    assert shared
    tabs = [_second_browser(client) for _ in range(2)]
    for tab in tabs:
        _hold_cookie(tab, shared)

    with ThreadPoolExecutor(max_workers=len(tabs)) as pool:
        answers = list(pool.map(lambda tab: tab.post(_REFRESH, headers=SAME_SITE), tabs))

    assert [answer.status_code for answer in answers] == [200] * len(tabs)
    for tab, answer in zip(tabs, answers, strict=True):
        token = answer.json()["access_token"]
        assert tab.get("/api/v1/auth/quota", headers=_bearer(token)).status_code == 200
        # And each tab can go on refreshing with the cookie it was given.
        assert tab.post(_REFRESH, headers=SAME_SITE).status_code == 200


@requires_db
def test_an_expired_access_token_is_refused_and_the_cookie_renews_it(
    client: TestClient,
) -> None:
    caller = decode_access_token(_sign_up(client))
    expired = create_access_token(
        subject=caller.id,
        tenant_id=caller.tenant_id,
        session_id=caller.session_id,
        ttl_seconds=-1,
    )
    assert client.get("/api/v1/auth/quota", headers=_bearer(expired)).status_code == 401
    assert client.post(_REFRESH, headers=SAME_SITE).status_code == 200


@requires_db
def test_an_expired_refresh_cookie_is_refused(client: TestClient) -> None:
    _sign_up(client)
    cookie = client.cookies.get(SESSION_COOKIE)
    assert cookie
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE refresh_tokens SET expires_at = :e WHERE token_hash = :h"),
            {"e": datetime.now(UTC) - timedelta(minutes=1), "h": hash_refresh_token(cookie)},
        )
    response = client.post(_REFRESH, headers=SAME_SITE)
    assert response.status_code == 401
    assert response.json()["code"] == "session_ended"


@requires_db
def test_signing_in_as_another_account_switches_what_the_cookie_restores(
    client: TestClient,
) -> None:
    """One browser, two accounts in turn: a reload restores the latest one only."""
    first = decode_access_token(_sign_up(client))
    second = decode_access_token(_sign_up(client))
    assert first.id != second.id

    restored = decode_access_token(client.post(_REFRESH, headers=SAME_SITE).json()["access_token"])
    assert restored.id == second.id
    assert restored.tenant_id == second.tenant_id
    assert restored.session_id == second.session_id


@requires_db
def test_an_account_token_from_before_sessions_is_refused(client: TestClient) -> None:
    caller = decode_access_token(_sign_up(client))
    legacy = create_access_token(subject=caller.id, tenant_id=caller.tenant_id)
    response = client.get("/api/v1/auth/quota", headers=_bearer(legacy))
    assert response.status_code == 401
    assert response.json()["code"] == "session_ended"


def test_clearing_the_secure_cookie_names_the_same_host_cookie() -> None:
    response = Response()
    _clear_session_cookie(response, settings=_settings(Environment.STAGING))
    header = response.headers["set-cookie"]
    attributes = _cookie_attributes(header)
    assert header.startswith(f"{SECURE_SESSION_COOKIE}=")
    assert attributes["path"] == "/"
    assert attributes["max-age"] == "0"
    assert "secure" in attributes
    assert "domain" not in attributes


def test_clearing_the_dev_cookie_names_the_session_path() -> None:
    response = Response()
    _clear_session_cookie(response, settings=_settings(Environment.DEV))
    header = response.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=")
    assert _cookie_attributes(header)["path"] == "/api/v1/auth/session"


@pytest.fixture
def secure_client(client: TestClient) -> Iterator[TestClient]:
    """The same app built with a Secure cookie, over https (the `client` fixture cleans up)."""
    settings = Settings(
        environment=Environment.DEV,
        database_url=os.environ["DATABASE_URL"],
        opensearch_url=os.environ.get("OPENSEARCH_URL", "http://localhost:9200"),
        anthropic_api_key="test-key",
        openai_api_key="test-key",
        jwt_secret="x" * 48,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        auth_cookie_secure=True,
    )
    app = create_app(settings)
    store = InMemoryRateLimitStore()
    app.dependency_overrides[deps.get_rate_limit_store] = lambda: store
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@requires_db
def test_the_secure_cookie_round_trips_and_a_tossed_plain_one_is_ignored(
    secure_client: TestClient,
) -> None:
    token = _sign_up(secure_client)
    assert secure_client.cookies.get(SECURE_SESSION_COOKIE)
    assert secure_client.cookies.get(SESSION_COOKIE) is None
    assert secure_client.post(_REFRESH, headers=SAME_SITE).status_code == 200

    # A cookie under the plain name, as a sibling subdomain could set, is not read.
    other = TestClient(secure_client.app, base_url="https://testserver")
    other.cookies.set(SESSION_COOKIE, "tossed", domain="testserver.local", path="/")
    assert other.post(_REFRESH, headers=SAME_SITE).status_code == 401

    response = secure_client.post(f"{_SESSION}/logout", headers={**SAME_SITE, **_bearer(token)})
    assert response.status_code == 204
    header = response.headers["set-cookie"]
    assert header.startswith(f"{SECURE_SESSION_COOKIE}=")
    assert _cookie_attributes(header)["path"] == "/"
    assert secure_client.cookies.get(SECURE_SESSION_COOKIE) is None
