"""Shared pytest fixtures.

Fixtures here are available to the whole tree. Anything specific to one package
belongs in a ``conftest.py`` next to those tests, not in this file.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Return settings suitable for tests, with no real credentials."""
    return Settings(
        environment=Environment.DEV,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        opensearch_url="http://localhost:9200",
        anthropic_api_key="test-key",
        jwt_secret="test-secret",
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def app_client(settings: Settings) -> Iterator[TestClient]:
    """Yield a TestClient bound to an app built from test settings.

    Generator-shaped deliberately: the client is closed on teardown, and
    changing the fixture's shape later would churn every test using it.

    Raises ``NotImplementedError`` until ``core.logging`` and ``core.errors``
    are implemented — the fixture itself needs no change when they are.
    """
    with TestClient(create_app(settings)) as client:
        yield client
