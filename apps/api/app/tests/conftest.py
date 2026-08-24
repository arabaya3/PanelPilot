"""Shared pytest fixtures.

Fixtures here are available to the whole tree. Anything specific to one package
belongs in a ``conftest.py`` next to those tests, not in this file.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import Environment, Settings


@pytest.fixture
def settings() -> Settings:
    """Return settings suitable for tests, with no real credentials."""
    return Settings(
        environment=Environment.LOCAL,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        opensearch_url="http://localhost:9200",
        anthropic_api_key="test-key",  # type: ignore[arg-type]
        jwt_secret="test-secret",  # type: ignore[arg-type]
    )


@pytest.fixture
def app_client(settings: Settings) -> Iterator[object]:
    """Yield a TestClient bound to an app built from test settings.

    Marked ``object`` until the app boots; tighten the annotation once
    ``create_app`` no longer raises.
    """
    pytest.skip("wire up once core.logging and core.errors are implemented")
    yield None
