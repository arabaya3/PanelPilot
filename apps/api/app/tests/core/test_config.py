"""Tests for `app/core/config.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Every validator here refuses a configuration that would start and then be
unsafe, so each is tested from both sides: refused where it is dangerous, and
still accepted in dev, where the relaxation is the point.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core import config
from app.core.config import JWT_SECRET_MIN_BYTES, Environment, Settings
from app.domain.images import MAX_IMAGE_BYTES

_STRONG_SECRET = "s" * JWT_SECRET_MIN_BYTES


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": Environment.PROD,
        "database_url": "postgresql+psycopg://u:p@h:5432/d",
        "opensearch_url": "http://localhost:9200",
        "anthropic_api_key": "k",
        "openai_api_key": "k",
        "jwt_secret": _STRONG_SECRET,
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _no_ambient_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Measure the arguments, not the developer's shell or .env."""
    for name in (
        "ENVIRONMENT",
        "DEBUG",
        "CORS_ALLOWED_ORIGINS",
        "JWT_ALGORITHM",
        "WEB_ORIGINS",
        "AUTH_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]


# --- ENVIRONMENT is required ---------------------------------------------------


def test_environment_has_no_default() -> None:
    """A prod deploy that forgot ENVIRONMENT used to run as dev, silently."""
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://u:p@h:5432/d",
        "opensearch_url": "http://localhost:9200",
        "anthropic_api_key": "k",
        "openai_api_key": "k",
        "jwt_secret": "x",
        "redis_url": "redis://localhost:6379/0",
    }
    with pytest.raises(PydanticValidationError, match="environment"):
        Settings(**values)


# --- the signing key -------------------------------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_a_short_secret_is_refused_outside_dev(env: Environment) -> None:
    with pytest.raises(PydanticValidationError, match="JWT_SECRET"):
        _settings(environment=env, jwt_secret="x" * (JWT_SECRET_MIN_BYTES - 1))


def test_a_short_secret_is_allowed_in_dev() -> None:
    assert _settings(environment=Environment.DEV, jwt_secret="x").jwt_secret


def test_the_minimum_is_a_constant_not_a_setting() -> None:
    """An environment variable that can lower the floor removes the floor."""
    assert "jwt_secret_min_bytes" not in Settings.model_fields
    assert config.JWT_SECRET_MIN_BYTES == 32


def test_the_minimum_cannot_be_passed_in() -> None:
    with pytest.raises(PydanticValidationError):
        _settings(jwt_secret="x", jwt_secret_min_bytes=1)


# --- the algorithm -------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_hmac_algorithms_are_accepted(algorithm: str) -> None:
    assert _settings(jwt_algorithm=algorithm).jwt_algorithm == algorithm


@pytest.mark.parametrize("algorithm", ["none", "RS256", "ES256", "hs256"])
def test_other_algorithms_are_refused(algorithm: str) -> None:
    with pytest.raises(PydanticValidationError, match="jwt_algorithm"):
        _settings(jwt_algorithm=algorithm)


# --- debug ---------------------------------------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_debug_is_refused_outside_dev(env: Environment) -> None:
    """FastAPI(debug=True) renders tracebacks into 500 bodies."""
    with pytest.raises(PydanticValidationError, match="DEBUG"):
        _settings(environment=env, debug=True)


def test_debug_is_allowed_in_dev() -> None:
    assert _settings(environment=Environment.DEV, debug=True).debug


# --- CORS ----------------------------------------------------------------------


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_a_wildcard_origin_is_refused_outside_dev(env: Environment) -> None:
    with pytest.raises(PydanticValidationError, match="CORS_ALLOWED_ORIGINS"):
        _settings(environment=env, cors_allowed_origins="https://app.example.com,*")


def test_a_wildcard_origin_is_allowed_in_dev() -> None:
    settings = _settings(environment=Environment.DEV, cors_allowed_origins="*")
    assert settings.cors_allowed_origins == ["*"]


def test_explicit_origins_are_accepted_everywhere() -> None:
    settings = _settings(cors_allowed_origins="https://a.example.com, https://b.example.com")
    assert settings.cors_allowed_origins == ["https://a.example.com", "https://b.example.com"]


# --- the refresh-session cookie ------------------------------------------------


def test_the_web_origins_are_trusted_for_the_cookie() -> None:
    settings = _settings(
        web_origins="https://app.example.com/, https://b.example.com",
        cors_allowed_origins="https://other.example.com",
    )
    assert settings.trusted_web_origins == {"https://app.example.com", "https://b.example.com"}


def test_without_web_origins_the_cors_origins_are_trusted() -> None:
    settings = _settings(cors_allowed_origins="https://app.example.com")
    assert settings.trusted_web_origins == {"https://app.example.com"}


def test_dev_trusts_the_local_web_server_when_nothing_is_configured() -> None:
    assert "http://localhost:3000" in _settings(environment=Environment.DEV).trusted_web_origins


def test_dev_always_trusts_both_loopback_names_beside_the_configured_ones() -> None:
    """D-AUTH-1: CORS named only localhost, so a reload at 127.0.0.1 lost the sign-in."""
    settings = _settings(environment=Environment.DEV, cors_allowed_origins="http://localhost:3000")
    assert settings.trusted_web_origins == {"http://localhost:3000", "http://127.0.0.1:3000"}
    configured = _settings(environment=Environment.DEV, web_origins="https://dev.example.com")
    assert "https://dev.example.com" in configured.trusted_web_origins
    assert "http://127.0.0.1:3000" in configured.trusted_web_origins


def test_outside_dev_loopback_is_not_trusted_unless_configured() -> None:
    settings = _settings(web_origins="https://app.example.com")
    assert settings.trusted_web_origins == {"https://app.example.com"}


def test_outside_dev_nothing_configured_trusts_nothing() -> None:
    assert _settings().trusted_web_origins == frozenset()


def test_a_wildcard_is_never_a_trusted_web_origin() -> None:
    assert "*" not in _settings(environment=Environment.DEV, web_origins="*").trusted_web_origins
    with pytest.raises(PydanticValidationError, match="WEB_ORIGINS"):
        _settings(web_origins="*")


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_the_cookie_is_secure_outside_dev_and_cannot_be_made_otherwise(env: Environment) -> None:
    assert _settings(environment=env).refresh_cookie_secure
    with pytest.raises(PydanticValidationError, match="AUTH_COOKIE_SECURE"):
        _settings(environment=env, auth_cookie_secure=False)


def test_the_cookie_is_plain_in_dev_unless_asked() -> None:
    assert not _settings(environment=Environment.DEV).refresh_cookie_secure
    assert _settings(environment=Environment.DEV, auth_cookie_secure=True).refresh_cookie_secure


# --- request body limits -------------------------------------------------------


def test_the_default_body_limits() -> None:
    settings = _settings()
    assert settings.max_request_body_bytes == 64 * 1024
    assert settings.max_image_request_body_bytes == 9 * 1024 * 1024


def test_the_image_body_limit_leaves_room_for_the_largest_image() -> None:
    """The image body limit must exceed the domain's image limit.

    Below the domain's own ceiling plus multipart framing, a legal image would
    get a bare 413 instead of the domain's precise message.
    """
    assert _settings().max_image_request_body_bytes > MAX_IMAGE_BYTES + 64 * 1024


def test_the_plc_body_limit_fits_the_largest_review_request() -> None:
    """PLC review accepts 100,000 characters of source."""
    assert _settings().max_plc_request_body_bytes > 100_000 * 4


# --- the absolute relevance floor ---------------------------------------------


def test_the_similarity_floor_is_unset_by_default() -> None:
    """Measure-only until calibrated: the right value depends on the corpus."""
    assert _settings().retrieval_min_similarity is None


@pytest.mark.parametrize("floor", [0.0, 1.0, -0.2, 1.5])
def test_a_similarity_floor_outside_the_open_unit_interval_is_refused(floor: float) -> None:
    # 0 or below enforces nothing while looking enforced; 1 refuses everything.
    with pytest.raises(PydanticValidationError):
        _settings(retrieval_min_similarity=floor)


def test_a_calibrated_similarity_floor_is_accepted() -> None:
    assert _settings(retrieval_min_similarity=0.42).retrieval_min_similarity == 0.42


def test_the_design_body_limit_fits_the_largest_schedule_upload() -> None:
    """Above the design's own 5 MiB file limit plus multipart framing."""
    from app.design.schedule_import import MAX_SCHEDULE_BYTES

    assert _settings().max_design_request_body_bytes > MAX_SCHEDULE_BYTES + 64 * 1024


# --- DESIGN_SNAPSHOT_SCHEMA ------------------------------------------------


def test_design_snapshot_schema_defaults_to_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DESIGN_SNAPSHOT_SCHEMA", raising=False)
    assert _settings().design_snapshot_schema == "pp-design/1"


@pytest.mark.parametrize("value", ["pp-design/1", "pp-design/2"])
def test_design_snapshot_schema_accepts_the_two_schemas(value: str) -> None:
    assert _settings(design_snapshot_schema=value).design_snapshot_schema == value


@pytest.mark.parametrize(
    "value", ["pp-design/3", "pp-design/999", "PP-DESIGN/2", "2", "v2", "", " pp-design/2"]
)
def test_design_snapshot_schema_refuses_anything_else(value: str) -> None:
    with pytest.raises(PydanticValidationError):
        _settings(design_snapshot_schema=value)


def test_an_unknown_design_snapshot_schema_stops_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "ENVIRONMENT": "dev",
        "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d",
        "OPENSEARCH_URL": "http://localhost:9200",
        "ANTHROPIC_API_KEY": "k",
        "OPENAI_API_KEY": "k",
        "JWT_SECRET": _STRONG_SECRET,
        "REDIS_URL": "redis://localhost:6379/0",
        "DESIGN_SNAPSHOT_SCHEMA": "pp-design/3",
    }.items():
        monkeypatch.setenv(name, value)
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exited:
            config.load_settings_or_exit()
        assert exited.value.code == config.EXIT_CONFIG_ERROR
    finally:
        config.get_settings.cache_clear()
