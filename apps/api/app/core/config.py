"""Application configuration.

All runtime configuration enters the process here and nowhere else. Modules
import ``get_settings()`` rather than reading ``os.environ`` directly, so every
knob is discoverable in one place and overridable in tests.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# RFC 7518 §3.2: an HMAC key shorter than the hash output weakens the
# signature. PyJWT only warns, so a one-character secret would sign real tokens
# in production. A module constant rather than a setting: an environment
# variable that can lower the floor is a floor anyone with deploy access can
# remove without noticing they did.
JWT_SECRET_MIN_BYTES = 32


class Environment(StrEnum):
    """Deployment environment the process is running in.

    Exactly three values, resolved once from the ``ENVIRONMENT`` variable at
    startup. Code branches on this enum rather than re-reading the environment,
    so there is one place to look when behaviour differs between deployments.
    """

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class RateLimitBackend(StrEnum):
    """Where the trial rate limiter keeps its request history.

    ``redis`` is the default because it is the only one that is correct with
    more than one worker. ``memory`` exists for a single process with no Redis
    to hand; it enforces a per-worker limit, not a per-deployment one.
    """

    MEMORY = "memory"
    REDIS = "redis"


class Settings(BaseSettings):
    """Typed view over the process environment.

    Every field maps 1:1 to a variable documented in ``.env.example``. Adding a
    field here without documenting it there is a review error.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="forbid",
    )

    # --- Runtime -----------------------------------------------------------
    # Required, with no default. A default of dev meant a production deploy
    # that forgot ENVIRONMENT silently ran with dev's relaxations — a one-byte
    # JWT_SECRET, debug tracebacks, wildcard CORS — and nothing said so.
    environment: Environment
    debug: bool = False
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # --- Postgres ----------------------------------------------------------
    database_url: SecretStr = Field(
        ..., description="SQLAlchemy DSN for the primary Postgres database."
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- OpenSearch --------------------------------------------------------
    opensearch_url: str = Field(..., description="Base URL of the OpenSearch cluster.")
    opensearch_username: str | None = None
    opensearch_password: SecretStr | None = None
    # Staging and production are two distinct indices, never one index with a
    # flag column. See docs/adr/0001-staging-vs-production-index.md.
    opensearch_staging_index: str = "panelpilot-staging"
    opensearch_production_index: str = "panelpilot-production"

    # --- LLM provider ------------------------------------------------------
    anthropic_api_key: SecretStr = Field(..., description="API key for the Claude API.")
    llm_model: str = "claude-sonnet-5"
    llm_max_output_tokens: int = 4096

    # --- Embedding provider ------------------------------------------------
    # Separate from the LLM provider on purpose: Anthropic publishes no
    # embeddings API, so the key above cannot serve retrieval's vector leg.
    # Unset by default and refused at use rather than defaulted — a default
    # provider would let a misconfigured deployment return vectors from a
    # model the index was never built against.
    embedding_provider: str = "voyage"
    # Read from VOYAGE_API_KEY rather than a generic EMBEDDING_API_KEY: the key
    # is vendor-specific, and naming it after the vendor means a second
    # provider added later gets its own variable instead of overloading one
    # that could silently hold the wrong account's credential.
    embedding_api_key: SecretStr | None = Field(default=None, alias="VOYAGE_API_KEY")
    # voyage-3.5 outputs 1024 dimensions, which is what
    # `mappings.EMBEDDING_DIMENSIONS` pins — verified against the live API,
    # not assumed. Changing to a model of a different width is a re-index,
    # not a config edit.
    embedding_model: str = "voyage-3.5"

    # --- Retrieval / guardrails --------------------------------------------
    # These seed RetrievalConfig, which is what the query path actually reads.
    # They stay here because they are documented environment variables an
    # operator may already have set; `retrieval_config_from_settings` is the
    # one place that turns them into a config, so there is still a single
    # value in play rather than two that can drift apart.
    retrieval_top_k: int = 12
    # Hybrid scores are normalised to [0, 1] by the search pipeline, so this
    # is a fraction of the top hit rather than a raw BM25 value.
    retrieval_min_score: float = 0.05
    guardrail_min_confidence: float = 0.6

    # --- Object storage ----------------------------------------------------
    # Where uploaded equipment photos are written. A local directory today;
    # the domain talks to an ObjectStore port, so pointing this at S3 is an
    # adapter swap at the composition root rather than a domain change.
    image_storage_root: str = "./var/images"

    # --- Security ----------------------------------------------------------
    jwt_secret: SecretStr = Field(..., description="Signing key for issued access tokens.")
    # A closed set. Anything else is either asymmetric (needs a key pair this
    # service does not hold) or "none", and both are refused at startup rather
    # than discovered when the first token fails to verify.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_seconds: int = 3600
    # NoDecode stops pydantic-settings JSON-decoding this before validation,
    # so the comma-separated form documented in .env.example actually works.
    # Without it, CORS_ALLOWED_ORIGINS=http://localhost:3000 is a startup error.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Request limits ----------------------------------------------------
    # Enforced by middleware before anything parses the body. FastAPI reads,
    # parses and spools a body (multipart files to temp files) BEFORE any
    # dependency runs, so without a ceiling an unauthenticated caller could
    # make the server buffer an arbitrary amount ahead of auth and rate
    # limiting. Bytes, on the raw request body.
    max_request_body_bytes: int = 64 * 1024
    # Image upload. Must exceed app.domain.images.MAX_IMAGE_BYTES (8 MiB) by
    # the multipart framing, so the domain's precise "image too large" message
    # stays reachable; a test pins the relationship.
    max_image_request_body_bytes: int = 9 * 1024 * 1024
    # PLC review accepts up to 100,000 characters of source, which a 64 KiB
    # ceiling would reject for a perfectly valid request.
    max_plc_request_body_bytes: int = 512 * 1024

    # --- Redis -------------------------------------------------------------
    redis_url: str = Field(..., description="Redis URL used for rate limiting and cached lookups.")
    rate_limit_backend: RateLimitBackend = RateLimitBackend.REDIS

    # --- Ingestion ---------------------------------------------------------
    ingestion_user_agent: str = "PanelPilotBot/0.1"
    ingestion_max_concurrency: int = 4

    @model_validator(mode="after")
    def _reject_a_weak_signing_key(self) -> Settings:
        """Refuse to start outside dev with a forgeable JWT secret.

        Returns:
            The validated settings.

        Raises:
            ValueError: If the secret is too short for the signing algorithm.
        """
        secret = self.jwt_secret.get_secret_value()
        # Dev keeps short throwaway secrets usable; staging and prod do not.
        if self.environment is not Environment.DEV and len(secret.encode()) < JWT_SECRET_MIN_BYTES:
            raise ValueError(
                f"JWT_SECRET is {len(secret.encode())} bytes; {self.environment.value} "
                f"requires at least {JWT_SECRET_MIN_BYTES}. Generate one with "
                '`python -c "import secrets; print(secrets.token_urlsafe(32))"`.'
            )
        return self

    @model_validator(mode="after")
    def _reject_dev_only_switches_outside_dev(self) -> Settings:
        """Refuse settings that are only safe on a developer's machine.

        Returns:
            The validated settings.

        Raises:
            ValueError: If debug is on, or CORS allows any origin, outside dev.
        """
        if self.environment is Environment.DEV:
            return self
        # FastAPI(debug=True) renders the traceback — exception text, file
        # paths, local source — into the body of every 500. Verified, not
        # assumed. In staging or prod that is an information leak per error.
        if self.debug:
            raise ValueError(
                f"DEBUG=true is refused in {self.environment.value}: it returns "
                "tracebacks in error responses. Set DEBUG=false."
            )
        # A wildcard lets any website script this API from a visitor's
        # browser. There is no deployment where that is the intent.
        if "*" in self.cors_allowed_origins:
            raise ValueError(
                f"CORS_ALLOWED_ORIGINS='*' is refused in {self.environment.value}. "
                "List the frontend's origins explicitly."
            )
        return self

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept a comma-separated string for the origins list.

        Args:
            value: The raw value from the environment or an explicit argument.

        Returns:
            A list of origins when given a string, otherwise the value unchanged.
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        """Return ``True`` when running against production infrastructure."""
        return self.environment is Environment.PROD


# Distinct from 1 so an orchestrator can tell "bad config" from "crashed".
EXIT_CONFIG_ERROR = 78  # EX_CONFIG, sysexits.h


class ConfigurationError(RuntimeError):
    """Required configuration is missing or invalid.

    Deliberately not a ``PanelPilotError``: this is raised before the
    application exists, so there is no handler to translate it and no request
    to fail. It ends the process instead.
    """


def _format_validation_error(error: PydanticValidationError) -> str:
    """Turn a pydantic error into something readable at 3am.

    Args:
        error: The validation error raised while building ``Settings``.

    Returns:
        A multi-line message naming each offending variable and why.
    """
    lines = ["Invalid or missing configuration:", ""]
    for item in error.errors():
        # loc is the field name; the environment variable is its upper-case form.
        field = ".".join(str(part) for part in item["loc"]) or "<root>"
        lines.append(f"  {field.upper()}: {item['msg']}")
    lines += [
        "",
        "Every variable is documented in .env.example. Copy it to .env and fill "
        "in the values, or set them in the environment.",
    ]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so validation happens once at startup. Tests patch the environment
    and then call ``get_settings.cache_clear()``.

    Returns:
        The validated settings object for this process.

    Raises:
        ConfigurationError: If any required variable is missing or invalid. The
            process must not start half-configured, so this is fatal rather
            than something a caller can fall back from.
    """
    try:
        return Settings()
    except PydanticValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc)) from exc


def load_settings_or_exit() -> Settings:
    """Return settings, or print the reason and end the process.

    Shared by both composition roots — ``app.main`` and ``app.worker.main``.
    Living here rather than in either entrypoint is what stops one of them
    drifting into a raw traceback while the other exits cleanly.

    Returns:
        The validated settings.

    Raises:
        SystemExit: Always, with ``EXIT_CONFIG_ERROR``, when configuration
            is missing or invalid. Starting half-configured is worse than
            not starting: the process would pass a liveness probe and then
            fail every request for a reason nothing in the logs explains.
    """
    try:
        return get_settings()
    except ConfigurationError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG_ERROR) from exc
