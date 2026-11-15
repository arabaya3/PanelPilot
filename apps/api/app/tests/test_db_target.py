"""The refusal of unexpected database targets, without touching any database.

``app.tests.db_target`` and ``scripts/recreate-test-db.sh`` must refuse the
dev database and anything that is not a disposable test database before a
single statement is sent. The script's refusals are checked here only where
they happen before Docker is consulted; its container checks were run by hand
against disposable containers (see the Phase 1 record).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.tests import db_target

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recreate-test-db.sh"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql+psycopg://panelpilot:x@localhost:5432/panelpilot",
        "postgresql+psycopg://panelpilot:x@localhost:5432/postgres",
        "postgresql+psycopg://panelpilot:x@localhost:5432/template1",
        "postgresql+psycopg://panelpilot:x@localhost:5432/",
        "postgresql+psycopg://panelpilot:x@localhost:5432/panelpilot_dev",
        "postgresql+psycopg://panelpilot:x@localhost:5432/panelpilot_phase1_",
        "postgresql+psycopg://panelpilot:x@localhost:5432/Panelpilot_phase1_x",
        "postgresql+psycopg://panelpilot:x@localhost:5432/panelpilot_phase1_x;drop",
        "postgresql+psycopg://panelpilot:x@localhost:5432/other_phase1_x",
        "sqlite:///panelpilot_phase1_x",
        "not a url",
    ],
)
def test_refuses_anything_but_a_disposable_database(url: str) -> None:
    with pytest.raises(db_target.UnsafeDatabaseTargetError):
        db_target.require_disposable(url)


@pytest.mark.parametrize(
    "name", ["panelpilot_phase1_test", "panelpilot_ci_test", "panelpilot_test_x1"]
)
def test_accepts_a_disposable_database(name: str) -> None:
    url = f"postgresql+psycopg://panelpilot:x@localhost:5432/{name}"
    assert db_target.require_disposable(url).database == name


def test_scratch_names_are_checked_too() -> None:
    base = "postgresql+psycopg://panelpilot:x@localhost:5432/panelpilot_ci_test"
    assert db_target.scratch_url(base, "owner_mig").database == "panelpilot_ci_test_owner_mig"
    with pytest.raises(db_target.UnsafeDatabaseTargetError):
        db_target.scratch_url(base, "x" * 60)
    with pytest.raises(db_target.UnsafeDatabaseTargetError):
        db_target.scratch_url("postgresql+psycopg://p:x@h/panelpilot", "owner_mig")


def _script(*args: str) -> subprocess.CompletedProcess[str]:
    # An empty PATH: if a check were skipped, docker could not be reached.
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/nonexistent"},
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--database", "panelpilot_phase1_x"),
        ("--container", "panelpilot-postgres-1"),
        ("--container", "panelpilot-postgres-1", "--database", "panelpilot"),
        ("--container", "panelpilot-postgres-1", "--database", "postgres"),
        ("--container", "panelpilot-postgres-1", "--database", "panelpilot_other"),
        ("--container", "$(docker ps -q)", "--database", "panelpilot_phase1_x"),
    ],
)
def test_script_refuses_before_contacting_docker(args: tuple[str, ...]) -> None:
    result = _script(*args)
    assert result.returncode == 3, result.stderr
    assert result.stderr.startswith("refused: ")
