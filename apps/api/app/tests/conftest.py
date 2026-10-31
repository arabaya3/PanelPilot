"""Shared pytest fixtures.

Fixtures here are available to the whole tree. Anything specific to one package
belongs in a ``conftest.py`` next to those tests, not in this file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

#: What makes an index name safe for a test to create and delete.
_THROWAWAY_MARKERS = ("-ci-", "test")


def _keep_tests_off_real_indices() -> None:
    """Point the suite at throwaway indices unless it already is.

    The OpenSearch tests build their corpus with ``ensure_index(...,
    recreate=True)`` and drop it afterwards, on whatever names are configured.
    CI configures ``panelpilot-ci-*``. Inside the compose ``api`` container the
    configured names are the real ones, so ``docker compose exec api pytest``
    deleted the published library and staging -- 6,376 and 6,610 passages,
    restored from a backup on 2026-10-20. Renaming here, before anything reads
    the settings, makes the safe names the default rather than a flag to
    remember.
    """
    for variable, target in (
        ("OPENSEARCH_STAGING_INDEX", "staging"),
        ("OPENSEARCH_PRODUCTION_INDEX", "production"),
    ):
        name = os.environ.get(variable, "")
        if not any(marker in name for marker in _THROWAWAY_MARKERS):
            os.environ[variable] = f"panelpilot-test-{target}"


# Before the app imports below: settings are read once and cached.
_keep_tests_off_real_indices()


def _refuse_unsafe_database() -> None:
    """Stop the run before any test can create or drop tables in the wrong place.

    Only an explicitly named, disposable database is accepted
    (``app.tests.db_target``). Without ``TEST_DATABASE_URL`` the database
    tests skip or fail on their own; nothing falls back to ``DATABASE_URL``.
    """
    from app.tests import db_target

    url = os.environ.get("TEST_DATABASE_URL", "")
    if url:
        try:
            db_target.require_disposable(url)
        except db_target.UnsafeDatabaseTargetError as exc:
            raise SystemExit(f"pytest: {exc}") from None


_refuse_unsafe_database()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Environment, Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _billing_not_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold every test to an unenforced plan, as a deployment starts.

    Reading ``BILLING_ENFORCED`` needs the whole configuration, which most
    tests do not set; a test of the plans' limits switches them on itself.
    """
    from app.domain import billing

    monkeypatch.setattr(billing, "enforced", lambda: False)


@pytest.fixture(autouse=True)
def _no_brand_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the diagnosis path off the index unless a test asks for the hint.

    Reading the brand vocabulary queries OpenSearch, which the unit job does
    not run, and a test that stubs ``search`` means the passages it supplies.
    A test of the hint itself sets it back, or calls ``equipment_hint`` directly.
    """
    from app.domain import diagnostics

    monkeypatch.setattr(diagnostics, "infer_manufacturer", lambda _question: None)


@pytest.fixture
def settings() -> Settings:
    """Return settings suitable for tests, with no real credentials."""
    return Settings(
        environment=Environment.DEV,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        opensearch_url="http://localhost:9200",
        anthropic_api_key="test-key",
        openai_api_key="test-key",
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
