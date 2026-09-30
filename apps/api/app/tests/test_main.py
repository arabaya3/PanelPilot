"""Tests for `app/main.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This is BE-001's smoke test: the app boots from a test config and answers on
/health. It is deliberately the only test that exercises the real composition
root, so a broken wiring change fails here rather than in every other suite at
once.
"""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    correlation_id_middleware,
)
from app.core.config import (
    EXIT_CONFIG_ERROR,
    ConfigurationError,
    Environment,
    Settings,
    get_settings,
)
from app.core.observability import CORRELATION_HEADER
from app.main import create_app

REQUIRED_ENV = (
    # Required with no default: a prod deploy that forgot it used to run as dev.
    "ENVIRONMENT",
    "DATABASE_URL",
    "OPENSEARCH_URL",
    "OPENAI_API_KEY",
    "JWT_SECRET",
    "REDIS_URL",
)


def test_app_boots_from_test_config_and_serves_liveness(app_client: TestClient) -> None:
    response = app_client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "up"


def test_readiness_reports_each_dependency_separately(app_client: TestClient) -> None:
    """Readiness must name each dependency, and its status must follow them.

    Asserted as a relationship rather than a fixed outcome: this suite runs
    both with and without live services, so pinning 503 made the test pass for
    an environmental reason rather than a behavioural one. What must hold
    either way is that every dependency is reported by name and that the
    overall status is up only when all of them are — otherwise a container
    healthcheck built on this would gate on nothing.
    """
    response = app_client.get("/api/v1/health/ready")
    body = response.json()
    assert set(body["dependencies"]) == {"database", "opensearch"}

    all_up = all(state == "up" for state in body["dependencies"].values())
    assert body["status"] == ("up" if all_up else "down")
    assert response.status_code == (200 if all_up else 503)


def test_routes_come_only_from_the_v1_router(app_client: TestClient) -> None:
    """main.py wires routers; it must not define routes itself.

    Every non-internal path has to sit under the configured prefix, which is
    what keeps route definitions in app/api/v1/ where the layering tests can
    see them.
    """
    prefix = "/api/v1"
    builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    # Not every entry in .routes is a path-bearing route (mounts and included
    # routers are not), so read the attribute defensively rather than assuming.
    paths = {
        path
        for route in app_client.app.routes  # type: ignore[attr-defined]
        if (path := getattr(route, "path", None)) is not None
    }
    assert paths, "no routes registered at all"
    stray = {p for p in paths if not p.startswith(prefix) and p not in builtin}
    assert not stray, f"routes defined outside {prefix}: {stray}"


@pytest.mark.parametrize("missing", REQUIRED_ENV)
def test_startup_fails_loudly_when_a_required_variable_is_absent(
    missing: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Missing config must stop the process, not start it half-configured.

    Each required variable is removed in turn so that adding a new one without
    making it required is visible here.
    """
    values = {
        "ENVIRONMENT": "prod",
        "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d",
        "OPENSEARCH_URL": "http://localhost:9200",
        "OPENAI_API_KEY": "k",
        # 32+ bytes: staging and prod refuse to start with a forgeable
        # signing key, which is the point of this boot test.
        "JWT_SECRET": "x" * 48,
        "REDIS_URL": "redis://localhost:6379/0",
    }
    del values[missing]

    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    # Settings reads .env when present; chdir somewhere without one so the test
    # measures the environment it just set, not the developer's local file.
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    get_settings.cache_clear()

    try:
        with pytest.raises(ConfigurationError) as caught:
            get_settings()
        # The message has to name the offending variable to be worth anything.
        assert missing in str(caught.value)
    finally:
        get_settings.cache_clear()


def test_create_app_exits_with_config_code_rather_than_raising_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """A misconfigured start exits 78 (EX_CONFIG), not an unhandled traceback."""
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    get_settings.cache_clear()

    try:
        with pytest.raises(SystemExit) as caught:
            create_app()
        assert caught.value.code == EXIT_CONFIG_ERROR
    finally:
        get_settings.cache_clear()


def test_environment_enum_matches_the_three_documented_values() -> None:
    """Dev | staging | prod — the single ENV var the whole service branches on."""
    assert {e.value for e in Environment} == {"dev", "staging", "prod"}


@pytest.mark.parametrize("env", list(Environment))
def test_service_boots_in_every_environment_from_env_vars_only(
    env: Environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The acceptance criterion, asserted rather than assumed.

    Boots the real composition root once per environment with nothing but
    environment variables set — no .env file, no constructed Settings object —
    and checks it serves liveness. Anything that hardcodes config, or branches
    on the environment in a way that breaks one of the three, fails here.
    """
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for key, value in {
        "ENVIRONMENT": env.value,
        "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d",
        "OPENSEARCH_URL": "http://localhost:9200",
        "OPENAI_API_KEY": "k",
        # 32+ bytes: staging and prod refuse to start with a forgeable
        # signing key, which is the point of this boot test.
        "JWT_SECRET": "x" * 48,
        "REDIS_URL": "redis://localhost:6379/0",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    try:
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/api/v1/health/live").status_code == 200
        assert get_settings().environment is env
        # Only prod counts as production; a typo'd branch here would be silent.
        assert get_settings().is_production is (env is Environment.PROD)
    finally:
        get_settings.cache_clear()


def test_explicit_settings_bypass_the_environment(settings: Settings) -> None:
    """Tests construct settings directly; that path must not read the process env."""
    app = create_app(settings)
    assert app.title == "PanelPilot API"


# --- API docs ------------------------------------------------------------------


@pytest.mark.parametrize("env", list(Environment))
def test_docs_are_served_everywhere_but_prod(settings: Settings, env: Environment) -> None:
    """Docs are off in prod only.

    A map of every endpoint helps a developer in dev and staging, and only
    someone probing the API in prod.
    """
    configured = settings.model_copy(update={"environment": env, "jwt_secret": SecretStr("x" * 48)})
    with TestClient(create_app(configured)) as client:
        expected = 404 if env is Environment.PROD else 200
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == expected, path


def test_the_schema_can_still_be_generated_in_prod(settings: Settings) -> None:
    """The shared-types generator calls app.openapi(), not the route."""
    configured = settings.model_copy(
        update={"environment": Environment.PROD, "jwt_secret": SecretStr("x" * 48)}
    )
    schema = create_app(configured).openapi()
    assert "/api/v1/auth/trial/resume" in schema["paths"]


# --- middleware order ------------------------------------------------------------


def test_the_correlation_id_middleware_is_outermost(settings: Settings) -> None:
    """The correlation middleware runs first on every request.

    Starlette runs the middleware added LAST first. The comment in main.py said
    "outermost" while CORS, added after it, actually was.
    """
    app = create_app(settings)
    stack: list[object] = [m.cls for m in app.user_middleware]
    assert stack == [
        BaseHTTPMiddleware,
        SecurityHeadersMiddleware,
        CORSMiddleware,
        BodySizeLimitMiddleware,
    ]
    assert app.user_middleware[0].kwargs["dispatch"] is correlation_id_middleware


def test_a_rejected_body_still_carries_every_outer_header(settings: Settings) -> None:
    """A 413 passes through every outer middleware.

    It is produced innermost, so it passes through CORS (a browser can read
    it), the security headers, and the correlation id on its way out.
    """
    configured = settings.model_copy(update={"cors_allowed_origins": ["http://app.test"]})
    with TestClient(create_app(configured)) as client:
        response = client.post(
            "/api/v1/auth/login",
            content=b"x" * (configured.max_request_body_bytes + 1),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://app.test",
                CORRELATION_HEADER: "trace-413",
            },
        )
    assert response.status_code == 413
    assert response.headers[CORRELATION_HEADER] == "trace-413"
    assert response.headers["access-control-allow-origin"] == "http://app.test"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_image_uploads_get_the_larger_body_limit(settings: Settings) -> None:
    """A 1 MiB photo is refused by the default limit and must not be here.

    Unauthenticated, so the route answers 401 — the point is that it answers
    at all rather than the body limit answering 413 first.
    """
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/images", files={"file": ("panel.jpg", b"\xff" * (1024 * 1024))}
        )
    assert response.status_code != 413


# --- CORS ----------------------------------------------------------------------


def test_cors_allows_only_what_the_frontend_uses(settings: Settings) -> None:
    configured = settings.model_copy(update={"cors_allowed_origins": ["http://app.test"]})
    with TestClient(create_app(configured)) as client:
        preflight = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        # Bearer auth, never cookies: no ambient credential to carry.
        assert "access-control-allow-credentials" not in preflight.headers

        refused = client.options(
            "/api/v1/auth/login",
            headers={"Origin": "http://app.test", "Access-Control-Request-Method": "DELETE"},
        )
        assert refused.status_code == 400

        foreign = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://app.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-something-else",
            },
        )
        assert foreign.status_code == 400


# --- unhandled exceptions --------------------------------------------------------


def test_an_unhandled_exception_is_a_generic_500(settings: Settings) -> None:
    """Never the exception text: it can name tables, paths, or echo SQL."""
    app = create_app(settings)

    @app.get("/api/v1/boom-for-test")
    def boom() -> None:
        raise RuntimeError("password_hash column leaked")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/boom-for-test", headers={CORRELATION_HEADER: "trace-x"})

    assert response.status_code == 500
    assert response.json() == {
        "error": "InternalServerError",
        "detail": "Internal server error",
        "correlation_id": "trace-x",
    }
    assert "leaked" not in response.text
    assert response.headers[CORRELATION_HEADER] == "trace-x"
    assert response.headers["X-Frame-Options"] == "DENY"
