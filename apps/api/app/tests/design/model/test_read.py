"""Tests of ``app.design.model.read``: the one strict snapshot reader, and its use on open."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.errors import ConflictError
from app.design import snapshot
from app.design.model import versions
from app.design.model.read import read_snapshot
from app.domain import design_projects
from app.models.schemas.design import DesignProject, ExportStatus
from app.tests.domain.test_design_projects import (
    DATABASE_URL,
    _approve,
    _as_tenant,  # noqa: F401  (a fixture)
    _engine,  # noqa: F401  (a fixture)
    _export,
    _saved,
    _tenants,  # noqa: F401  (a fixture)
    _user,
)


def test_unmarked_snapshot_reads_as_v1(hall_project: DesignProject) -> None:
    read = read_snapshot(hall_project.model_dump(mode="json"))
    assert read.schema == versions.V1
    assert read.project == hall_project


def test_an_unknown_schema_is_refused(hall_project: DesignProject) -> None:
    stored = {"schema": "pp-design/2", "model": hall_project.model_dump(mode="json")}
    with pytest.raises(versions.SnapshotSchemaUnknownError) as refused:
        read_snapshot(stored)
    assert refused.value.params == {"schema": "pp-design/2"}


def test_an_unknown_field_is_refused_not_dropped(hall_project: DesignProject) -> None:
    stored = hall_project.model_dump(mode="json")
    stored["boards"][0]["devices"][0]["terminals"] = []
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot({**hall_project.model_dump(mode="json"), "registry_lock": {}})


def _rewrite_snapshot(engine: Engine, change: Any) -> None:
    """Change the stored design and record its new hash, as a direct database write would."""
    with engine.begin() as conn:
        stored: Any = conn.execute(
            text("SELECT design_snapshot FROM design_project_revisions")
        ).scalar_one()
        stored = change(stored)
        conn.execute(
            text(
                "UPDATE design_project_revisions "
                "SET design_snapshot = CAST(:s AS jsonb), snapshot_sha256 = :h"
            ),
            {"s": json.dumps(stored), "h": snapshot.sha256_of(stored)},
        )


def _with_unknown_field(stored: dict[str, Any]) -> dict[str, Any]:
    stored["boards"][0]["devices"][0]["terminals"] = []
    return stored


def _enveloped(stored: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "pp-design/9", "model": stored}


needs_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"), reason="needs Postgres: revisions are JSONB"
)


@needs_postgres
def test_open_agrees_with_export_on_an_unreadable_rehashed_snapshot(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _rewrite_snapshot(engine, _with_unknown_field)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.export_status is ExportStatus.NOT_VALIDATED
    assert any(
        f.code == "snapshot_integrity_failed" and f.severity.value == "blocking"
        for f in opened.findings
    )
    for refuse in (_export, _approve):
        with pytest.raises(ConflictError) as refused:
            refuse(as_tenant, a, saved)
        assert refused.value.code == "snapshot_integrity_failed"


@needs_postgres
def test_open_reports_an_unknown_schema_and_export_refuses_it(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _rewrite_snapshot(engine, _enveloped)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.export_status is ExportStatus.NOT_VALIDATED
    assert any(f.code == "snapshot_schema_unknown" for f in opened.findings)
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved)
    assert refused.value.code == "snapshot_schema_unknown"
    assert refused.value.params == {"schema": "pp-design/9"}
