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
        "jwt_secret": _STRONG_SECRET,
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _no_ambient_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """Measure the arguments, not the developer's shell or .env."""
    for name in ("ENVIRONMENT", "DEBUG", "CORS_ALLOWED_ORIGINS", "JWT_ALGORITHM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]


# --- ENVIRONMENT is required ---------------------------------------------------


def test_environment_has_no_default() -> None:
    """A prod deploy that forgot ENVIRONMENT used to run as dev, silently."""
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://u:p@h:5432/d",
        "opensearch_url": "http://localhost:9200",
        "anthropic_api_key": "k",
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
