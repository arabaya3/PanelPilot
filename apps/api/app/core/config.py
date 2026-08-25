"""Application configuration.

All runtime configuration enters the process here and nowhere else. Modules
import ``get_settings()`` rather than reading ``os.environ`` directly, so every
knob is discoverable in one place and overridable in tests.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment the process is running in."""

    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


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
    environment: Environment = Environment.LOCAL
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

    # --- Retrieval / guardrails --------------------------------------------
    retrieval_top_k: int = 12
    retrieval_min_score: float = 0.35
    guardrail_min_confidence: float = 0.6

    # --- Security ----------------------------------------------------------
    jwt_secret: SecretStr = Field(..., description="Signing key for issued access tokens.")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    # NoDecode stops pydantic-settings JSON-decoding this before validation,
    # so the comma-separated form documented in .env.example actually works.
    # Without it, CORS_ALLOWED_ORIGINS=http://localhost:3000 is a startup error.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Redis -------------------------------------------------------------
    redis_url: str = Field(..., description="Redis URL used for rate limiting and cached lookups.")

    # --- Ingestion ---------------------------------------------------------
    ingestion_user_agent: str = "PanelPilotBot/0.1"
    ingestion_max_concurrency: int = 4

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
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so validation happens once at startup. Tests patch the environment
    and then call ``get_settings.cache_clear()``.

    Returns:
        The validated settings object for this process.
    """
    return Settings()
