"""Tests for `app/main.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This is BE-001's smoke test: the app boots from a test config and answers on
/health. It is deliberately the only test that exercises the real composition
root, so a broken wiring change fails here rather than in every other suite at
once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    EXIT_CONFIG_ERROR,
    ConfigurationError,
    Environment,
    Settings,
    get_settings,
)
from app.main import create_app

REQUIRED_ENV = (
    "DATABASE_URL",
    "OPENSEARCH_URL",
    "ANTHROPIC_API_KEY",
    "JWT_SECRET",
    "REDIS_URL",
)


def test_app_boots_from_test_config_and_serves_liveness(app_client: TestClient) -> None:
    response = app_client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "up"


def test_readiness_reports_each_dependency_separately(app_client: TestClient) -> None:
    """Readiness must name what is down, not just fail.

    Nothing is running in the unit-test environment, so both dependencies are
    expected down — and the endpoint must say so with a 503 rather than a 200,
    or a container healthcheck built on it would gate on nothing.
    """
    response = app_client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert set(body["dependencies"]) == {"database", "opensearch"}


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
        "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d",
        "OPENSEARCH_URL": "http://localhost:9200",
        "ANTHROPIC_API_KEY": "k",
        "JWT_SECRET": "s",
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
        "ANTHROPIC_API_KEY": "k",
        "JWT_SECRET": "s",
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
