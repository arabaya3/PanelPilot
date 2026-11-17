"""The read-only operator script scripts/cable_core_audit.py against the disposable database.

SYNTHETIC data. The script spans every tenant and runs in a READ ONLY transaction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_cable_core_identity_db import _cabled
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _count,
    _new_project,
    _revise,
    _switch,
)
from app.tests.domain.test_m9b1_binding_preview_db import _project


def _script() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "cable_core_audit.py"
    spec = importlib.util.spec_from_file_location("cable_core_audit_script", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cable_core_audit = _script()


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def test_the_command_names_a_revision_written_with_the_gate_on_and_changes_nothing(
    client: TestClient,
    switch: Switch,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_id = _project(client, switch)
    monkeypatch.setenv("DESIGN_CABLE_CORE_CROSS_BOARD", "true")
    config = _cabled("c1-cable", "c1-cable")
    assert (
        _revise(client, project_id, m7a_support.request(), configuration=config).status_code == 201
    )
    before = revision_row(engine, project_id, 3)
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    assert cable_core_audit.main() == 1
    printed = capsys.readouterr().out
    assert f"{project_id} r3: c1-cable#1 on MB-1, SB-1" in printed
    # Read-only: the revision and the project's revisions are as they were.
    after = revision_row(engine, project_id, 3)
    assert after["snapshot_sha256"] == before["snapshot_sha256"]
    assert _count(client, project_id) == 3


def test_without_a_database_url_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cable_core_audit.main() == 2
