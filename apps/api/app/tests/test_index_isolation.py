"""The test suite never runs against the real indices.

``conftest`` renames the configured indices before anything reads the
settings. This pins that it did, and that the rename keeps names CI already
made safe. See ``_keep_tests_off_real_indices`` for the deletion it prevents.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import Settings
from app.tests import conftest


def test_every_index_the_suite_can_reach_is_a_throwaway(settings: Settings) -> None:
    """Read through test settings, so it holds where no environment is set (CI's unit job)."""
    for name in (settings.opensearch_staging_index, settings.opensearch_production_index):
        assert any(
            marker in name for marker in conftest._THROWAWAY_MARKERS
        ), f"the suite would create and delete {name!r}"


def test_a_real_name_is_renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_STAGING_INDEX", "panelpilot-staging")
    monkeypatch.setenv("OPENSEARCH_PRODUCTION_INDEX", "panelpilot-production")
    conftest._keep_tests_off_real_indices()
    assert os.environ["OPENSEARCH_STAGING_INDEX"] == "panelpilot-test-staging"
    assert os.environ["OPENSEARCH_PRODUCTION_INDEX"] == "panelpilot-test-production"


def test_a_name_ci_made_safe_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEARCH_STAGING_INDEX", "panelpilot-ci-staging")
    monkeypatch.setenv("OPENSEARCH_PRODUCTION_INDEX", "panelpilot-ci-production")
    conftest._keep_tests_off_real_indices()
    assert os.environ["OPENSEARCH_STAGING_INDEX"] == "panelpilot-ci-staging"
    assert os.environ["OPENSEARCH_PRODUCTION_INDEX"] == "panelpilot-ci-production"
