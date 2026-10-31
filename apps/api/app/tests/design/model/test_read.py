"""Tests of ``app.design.model.read``: the one strict snapshot reader, and its use on open."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.errors import ConflictError
from app.design import snapshot
from app.design.model import versions
from app.design.model.read import VersionedModel, read_snapshot
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
    stored = {"schema": "pp-design/3", "model": hall_project.model_dump(mode="json")}
    with pytest.raises(versions.SnapshotSchemaUnknownError) as refused:
        read_snapshot(stored)
    assert refused.value.params == {"schema": "pp-design/3"}


def test_bare_v1_content_in_a_v2_envelope_is_unreadable(hall_project: DesignProject) -> None:
    stored = {"schema": "pp-design/2", "model": hall_project.model_dump(mode="json")}
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)


def test_an_unknown_field_is_refused_not_dropped(hall_project: DesignProject) -> None:
    stored = hall_project.model_dump(mode="json")
    stored["boards"][0]["devices"][0]["terminals"] = []
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot({**hall_project.model_dump(mode="json"), "registry_lock": {}})


def test_the_domain_model_is_built_only_when_asked_for(
    hall_project: DesignProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.design.model.convert_v1 import from_v1 as real

    calls: list[object] = []

    def counted(project: DesignProject) -> Any:
        calls.append(project)
        return real(project)

    monkeypatch.setattr("app.design.model.read.from_v1", counted)
    read = read_snapshot(hall_project.model_dump(mode="json"))
    assert calls == []
    assert read.model is read.model
    assert len(calls) == 1


def _deep(depth: int = 5_000) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for _ in range(depth):
        nested = {"x": nested}
    return nested


def test_deep_nesting_is_unreadable_not_a_crash(hall_project: DesignProject) -> None:
    stored = hall_project.model_dump(mode="json")
    stored["info"]["name"] = _deep()
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(stored)
    with pytest.raises(versions.SnapshotUnreadableError):
        read_snapshot(_deep())


def test_deep_nesting_does_not_match_its_hash() -> None:
    row = SimpleNamespace(design_snapshot=_deep(), snapshot_sha256="0" * 64)
    assert design_projects._snapshot_matches(row) is False  # type: ignore[arg-type]


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


def _set_hash(engine: Engine, digest: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE design_project_revisions SET snapshot_sha256 = :h"), {"h": digest}
        )


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []
    real = read_snapshot

    def counted(stored: object) -> VersionedModel:
        calls.append(stored)
        return real(stored)

    monkeypatch.setattr("app.domain.design_projects.read_snapshot", counted)
    return calls


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


@needs_postgres
def test_an_unknown_marker_with_a_bad_hash_is_altered_on_open_as_on_export(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _rewrite_snapshot(engine, _enveloped)
    _set_hash(engine, "0" * 64)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.export_status is ExportStatus.NOT_VALIDATED
    codes = {f.code for f in opened.findings}
    assert "snapshot_integrity_failed" in codes
    assert "snapshot_schema_unknown" not in codes
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved)
    assert refused.value.code == "snapshot_integrity_failed"


@needs_postgres
def test_open_and_export_read_the_snapshot_once(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, monkeypatch
) -> None:
    a, _ = tenants
    saved = _saved(as_tenant, a)
    calls = _count_reads(monkeypatch)
    design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert len(calls) == 1
    calls.clear()
    _export(as_tenant, a, saved)
    assert len(calls) == 1
