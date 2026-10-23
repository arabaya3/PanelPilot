"""Tests for `app/domain/design_projects.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.design import RULES_VERSION, snapshot
from app.domain import design_projects
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    ApproveRevisionRequest,
    DistributionBoardRequest,
    ExportStatus,
    LoadInput,
    LoadKind,
    ProjectDesignRequest,
    ProjectInfo,
    ReviseProjectRequest,
    RevisionExportRequest,
    SaveProjectRequest,
)
from app.models.tables.base import Base
from app.models.tables.tenant import TenantRow

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: revisions are JSONB and tenant isolation is the database's",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenants")
def _tenants(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two tenants, the project tables empty."""
    with sessionmaker(bind=engine)() as session:
        session.execute(text("TRUNCATE design_project_revisions, design_projects CASCADE"))
        ids = []
        for slug in ("projects-a", "projects-b"):
            found = session.execute(
                text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": slug}
            ).scalar_one_or_none()
            if found is None:
                row = TenantRow(slug=slug, name=slug)
                session.add(row)
                session.flush()
                found = row.id
            ids.append(found)
        session.commit()
        return ids[0], ids[1]


@pytest.fixture(name="as_tenant")
def _as_tenant(engine: Engine) -> Iterator[object]:
    """A fresh session per call, the way each request gets one."""
    opened: list[Session] = []

    def make() -> Session:
        session = sessionmaker(bind=engine)()
        opened.append(session)
        return session

    yield make
    for session in opened:
        session.close()


def _user(tenant: uuid.UUID, email: str = "eng@example.com") -> CurrentUser:
    return CurrentUser(id=str(uuid.uuid4()), email=email, tenant_id=str(tenant), roles=frozenset())


def _request(name: str = "Tower", kw: str = "3") -> ProjectDesignRequest:
    return ProjectDesignRequest(
        info=ProjectInfo(name=name),
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=[
                    LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal(kw))
                ],
            )
        ],
    )


def test_save_project_keeps_what_was_entered(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = design_projects.save_project(
        session=as_tenant(),
        user=_user(a),
        request=SaveProjectRequest(name=" Tower ", request=_request(), note="first issue"),
    )
    assert saved.name == "Tower"
    assert saved.revision == 1
    assert [(r.number, r.note, r.author) for r in saved.revisions] == [
        (1, "first issue", "eng@example.com")
    ]
    assert saved.request.boards == _request().boards
    # The title block's revision list is the saved revisions, none approved.
    (listed,) = saved.request.info.revisions
    assert (listed.index, listed.description, listed.approved_by) == ("01", "first issue", "")


def test_revise_project_adds_a_revision_and_keeps_the_old(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    user = _user(a)
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    revised = design_projects.revise_project(
        session=as_tenant(),
        user=user,
        project_id=saved.id,
        request=ReviseProjectRequest(request=_request(kw="5"), note="more sockets"),
    )
    assert revised.revision == 2
    assert revised.request.boards[0].loads[0].power_kw == 5
    first = design_projects.open_project(
        session=as_tenant(), user=user, project_id=saved.id, revision=1
    )
    assert first.request.boards[0].loads[0].power_kw == 3


def test_open_project_refuses_what_is_not_there(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    user = _user(a)
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    with pytest.raises(NotFoundError):
        design_projects.open_project(
            session=as_tenant(), user=user, project_id=saved.id, revision=9
        )
    with pytest.raises(NotFoundError):
        design_projects.open_project(session=as_tenant(), user=user, project_id="not-a-uuid")


def test_another_tenants_project_does_not_exist(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    saved = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="T", request=_request())
    )
    intruder = _user(b)
    with pytest.raises(NotFoundError):
        design_projects.open_project(session=as_tenant(), user=intruder, project_id=saved.id)
    with pytest.raises(NotFoundError):
        design_projects.revise_project(
            session=as_tenant(),
            user=intruder,
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request()),
        )
    with pytest.raises(NotFoundError):
        design_projects.delete_project(session=as_tenant(), user=intruder, project_id=saved.id)
    page = design_projects.list_projects(session=as_tenant(), user=intruder)
    assert page.projects == []


def test_list_projects_pages_most_recent_first(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    user = _user(a)
    ids = [
        design_projects.save_project(
            session=as_tenant(),
            user=user,
            request=SaveProjectRequest(name=f"P{i}", request=_request()),
        ).id
        for i in range(3)
    ]
    # Revising the oldest moves it to the top.
    design_projects.revise_project(
        session=as_tenant(),
        user=user,
        project_id=ids[0],
        request=ReviseProjectRequest(request=_request()),
    )
    first = design_projects.list_projects(session=as_tenant(), user=user, limit=2)
    assert [p.name for p in first.projects] == ["P0", "P2"]
    assert first.projects[0].revisions == 2
    assert first.next_cursor is not None
    rest = design_projects.list_projects(
        session=as_tenant(), user=user, limit=2, cursor=first.next_cursor
    )
    assert [p.name for p in rest.projects] == ["P1"]
    assert rest.next_cursor is None
    with pytest.raises(ValidationError):
        design_projects.list_projects(session=as_tenant(), user=user, cursor="nonsense")


def test_delete_project_takes_its_revisions(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    user = _user(a)
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    design_projects.delete_project(session=as_tenant(), user=user, project_id=saved.id)
    with pytest.raises(NotFoundError):
        design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    with engine.connect() as connection:
        left = connection.execute(text("SELECT count(*) FROM design_project_revisions")).scalar()
    assert left == 0


def test_a_project_holds_a_bounded_number_of_revisions(  # type: ignore[no-untyped-def]
    tenants, as_tenant, monkeypatch
) -> None:
    a, _ = tenants
    user = _user(a)
    monkeypatch.setattr(design_projects, "MAX_REVISIONS", 2)
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    design_projects.revise_project(
        session=as_tenant(),
        user=user,
        project_id=saved.id,
        request=ReviseProjectRequest(request=_request()),
    )
    with pytest.raises(ValidationError) as refused:
        design_projects.revise_project(
            session=as_tenant(),
            user=user,
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request()),
        )
    assert refused.value.code == "project_revisions_full"


def test_approve_revision_signs_it_once_and_locks_the_project(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    user = _user(a)
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    design_projects.revise_project(
        session=as_tenant(),
        user=user,
        project_id=saved.id,
        request=ReviseProjectRequest(request=_request(kw="5"), note="more sockets"),
    )
    approved = design_projects.approve_revision(
        session=as_tenant(),
        user=user,
        project_id=saved.id,
        number=1,
        request=ApproveRevisionRequest(approver=" A. Rabaya "),
    )
    assert approved.revision == 1
    assert approved.revisions[0].approved_by == "A. Rabaya"
    assert approved.revisions[0].approved_at is not None
    assert approved.revisions[1].approved_by is None
    # Opened at revision 1, the title block lists only revision 1, approved.
    (listed,) = approved.request.info.revisions
    assert listed.approved_by == "A. Rabaya"
    latest = design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    assert [r.approved_by for r in latest.request.info.revisions] == ["A. Rabaya", ""]
    with pytest.raises(ConflictError) as again:
        design_projects.approve_revision(
            session=as_tenant(),
            user=user,
            project_id=saved.id,
            number=1,
            request=ApproveRevisionRequest(approver="Someone else"),
        )
    assert again.value.code == "revision_already_approved"
    with pytest.raises(NotFoundError):
        design_projects.approve_revision(
            session=as_tenant(),
            user=user,
            project_id=saved.id,
            number=9,
            request=ApproveRevisionRequest(approver="X"),
        )
    with pytest.raises(ValidationError) as locked:
        design_projects.delete_project(session=as_tenant(), user=user, project_id=saved.id)
    assert locked.value.code == "project_approved"


# --- Phase 1: server-built snapshots, bound approval, revision export --------


def _saved(as_tenant: Any, tenant: uuid.UUID, request: ProjectDesignRequest | None = None) -> Any:
    return design_projects.save_project(
        session=as_tenant(),
        user=_user(tenant),
        request=SaveProjectRequest(name="Tower", request=request or _request()),
    )


def _export(
    as_tenant: Any,
    tenant: uuid.UUID,
    saved: Any,
    purpose: str = "review",
    fmt: str = "circuits_csv",
) -> Any:
    return design_projects.export_revision(
        session=as_tenant(),
        user=_user(tenant),
        project_id=saved.id,
        number=1,
        request=RevisionExportRequest.model_validate({"format": fmt, "purpose": purpose}),
    )


def _approve(as_tenant: Any, tenant: uuid.UUID, saved: Any, approver: str = "R. Engineer") -> Any:
    return design_projects.approve_revision(
        session=as_tenant(),
        user=_user(tenant),
        project_id=saved.id,
        number=1,
        request=ApproveRevisionRequest(approver=approver),
    )


def _first_line(exported: Any) -> str:
    return str(exported.content.decode("utf-8").lstrip("﻿").splitlines()[0])


def _set(engine: Engine, **columns: object) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE design_project_revisions SET {assignments}"), columns)


def test_saving_designs_on_the_server_and_stores_the_snapshot(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    assert saved.validated
    assert saved.rules_version == RULES_VERSION
    assert saved.export_status is ExportStatus.DRAFT
    assert saved.findings
    assert "approval" in saved.execution_blockers
    with engine.connect() as conn:
        stored, digest = conn.execute(
            text("SELECT design_snapshot, snapshot_sha256 FROM design_project_revisions")
        ).one()
    assert snapshot.sha256_of(stored) == digest == saved.snapshot_sha256
    # The snapshot carries no client revision rows.
    assert stored["info"]["revisions"] == []


def test_approval_binds_the_snapshot_and_export_says_review_approved(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    approved = _approve(as_tenant, a, saved)
    assert approved.approved_snapshot_sha256 == saved.snapshot_sha256
    assert approved.export_status is ExportStatus.REVIEW_APPROVED
    first = _first_line(_export(as_tenant, a, saved))
    assert "REVIEW APPROVED — STUDY, NOT FOR EXECUTION" in first
    assert "R. Engineer" in first
    assert (saved.snapshot_sha256 or "")[: design_projects.SHORT_HASH] in first
    assert _export(as_tenant, a, saved, fmt="calculations_pdf").content.startswith(b"%PDF")


def test_an_unapproved_revision_exports_as_a_draft(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    first = _first_line(_export(as_tenant, a, _saved(as_tenant, a)))
    assert "DRAFT — NOT FOR EXECUTION" in first
    assert "revision 01" in first


def test_execution_is_always_refused_and_names_what_is_missing(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.code == "execution_package_incomplete"
    assert "control_schematics" in refused.value.params["missing"]
    assert refused.value.params["approval"] == "present"


def test_a_legacy_revision_opens_but_is_not_approved_or_exported(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _set(engine, design_snapshot=None, snapshot_sha256=None, findings=None, rules_version=None)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert not opened.validated
    assert opened.export_status is ExportStatus.NOT_VALIDATED
    with pytest.raises(ConflictError) as refused:
        _approve(as_tenant, a, saved)
    assert refused.value.code == "revision_not_validated"
    with pytest.raises(ConflictError) as exported:
        _export(as_tenant, a, saved)
    assert exported.value.code == "revision_not_validated"


def test_a_stale_or_altered_revision_is_refused(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _set(engine, rules_version="0.0.0")
    with pytest.raises(ConflictError) as stale:
        _export(as_tenant, a, saved)
    assert stale.value.code == "revision_stale"
    assert "rules_version" in stale.value.params["changed"]
    _set(engine, rules_version=RULES_VERSION, snapshot_sha256="0" * 64)
    with pytest.raises(ConflictError) as altered:
        _export(as_tenant, a, saved)
    assert altered.value.code == "snapshot_integrity_failed"


def test_blocking_findings_refuse_approval(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    blocking = {
        "code": "voltage_drop_exceeded",
        "severity": "blocking",
        "message": "m",
        "source": "s",
    }
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE design_project_revisions SET findings = CAST(:f AS jsonb)"),
            {"f": json.dumps([blocking])},
        )
    with pytest.raises(ConflictError) as refused:
        _approve(as_tenant, a, saved)
    assert refused.value.code == "revision_has_blocking_findings"


def test_an_approval_for_another_snapshot_does_not_count(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    _set(engine, approved_snapshot_sha256="f" * 64)
    first = _first_line(_export(as_tenant, a, saved))
    assert "DRAFT — NOT FOR EXECUTION" in first
    assert "R. Engineer" not in first


def test_another_tenants_revision_cannot_be_exported(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    saved = _saved(as_tenant, a)
    with pytest.raises(NotFoundError) as refused:
        _export(as_tenant, b, saved)
    assert refused.value.code == "revision_not_found"


def test_a_refused_design_is_still_saved_but_not_validated(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    request = _request()
    request.boards[0].fed_from = "NOWHERE"
    saved = _saved(as_tenant, a, request)
    assert not saved.validated
    assert saved.findings[0].code == "design_refused"


def test_a_stored_request_shape_still_opens() -> None:
    """Revisions store ``model_dump(mode="json")``: the full shape, as sampled."""
    stored = {
        "info": {
            "name": "T",
            "number": "",
            "customer": "",
            "consultant": "",
            "contractor": "",
            "revisions": [],
        },
        "boards": [
            {
                "name": "DB1",
                "location": None,
                "supply": {
                    "voltage_v": "400",
                    "phases": 3,
                    "frequency_hz": "50",
                    "earthing": "TN-S",
                    "fault_level_ka": None,
                    "earth_loop_ohm": None,
                },
                "loads": [
                    {
                        "description": "S",
                        "load": "socket",
                        "power_kw": "2",
                        "phases": 1,
                        "power_factor": None,
                        "controlled": False,
                        "feeds": None,
                        "starter": None,
                        "length_m": None,
                    }
                ],
                "conditions": {
                    "installation_method": "B1",
                    "ambient_temp_c": "30",
                    "grouped_circuits": 1,
                    "conductor_material": "copper",
                    "insulation_rating_c": 70,
                },
                "fed_from": None,
                "feeder_length_m": None,
            }
        ],
        "profile": {"key": "iec-default", "market": "PS"},
    }
    assert ProjectDesignRequest.model_validate(stored).model_dump(mode="json") == stored


def test_execution_is_refused_even_for_a_legacy_revision(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _set(engine, design_snapshot=None, snapshot_sha256=None, findings=None)
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.code == "execution_package_incomplete"
    assert "not_validated" in refused.value.params["blockers"]
    assert refused.value.params["approval"] == "missing"


def test_execution_names_an_altered_snapshot(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _set(engine, snapshot_sha256="0" * 64)
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.code == "execution_package_incomplete"
    assert "snapshot_integrity_failed" in refused.value.params["blockers"]


def _hold_revision_lock(engine: Engine) -> Any:
    """A connection holding revision 1's row lock, as an approval in progress does."""
    conn = engine.connect()
    conn.begin()
    conn.execute(text("SELECT id FROM design_project_revisions WHERE number = 1 FOR UPDATE"))
    return conn


def test_a_second_approver_waits_for_the_lock_then_is_refused(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    """Two sessions: the second approval blocks on the row lock, then sees the first."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    holder = _hold_revision_lock(engine)
    outcome: dict[str, BaseException | Any] = {}

    def second() -> None:
        try:
            outcome["result"] = _approve(as_tenant, a, saved, approver="Second Approver")
        except BaseException as exc:
            outcome["result"] = exc

    worker = threading.Thread(target=second)
    worker.start()
    worker.join(timeout=2)
    # Still waiting: the lock holder has not finished.
    assert worker.is_alive()
    holder.execute(
        text(
            "UPDATE design_project_revisions SET approved_by = 'First Approver', "
            "approved_account = 'first@example.com', approved_at = now(), "
            "approved_snapshot_sha256 = snapshot_sha256 WHERE number = 1"
        )
    )
    holder.commit()
    holder.close()
    worker.join(timeout=30)
    assert not worker.is_alive()
    refused = outcome["result"]
    assert isinstance(refused, ConflictError)
    assert refused.code == "revision_already_approved"
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.revisions[0].approved_by == "First Approver"
    assert opened.approved_snapshot_sha256 == saved.snapshot_sha256


def test_a_save_during_an_approval_is_a_new_unapproved_revision(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A save does not wait on, or change, the revision being approved."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    approving = threading.Event()
    release = threading.Event()
    outcome: dict[str, Any] = {}
    original = design_projects._approvable

    def slow(row: Any, number: int) -> None:
        original(row, number)
        approving.set()
        release.wait(timeout=30)

    def approve() -> None:
        outcome["approved"] = _approve(as_tenant, a, saved)

    monkeypatch.setattr(design_projects, "_approvable", slow)
    try:
        worker = threading.Thread(target=approve)
        worker.start()
        assert approving.wait(timeout=30)
        revised = design_projects.revise_project(
            session=as_tenant(),
            user=_user(a),
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request(kw="4"), note="more sockets"),
        )
        release.set()
        worker.join(timeout=30)
    finally:
        release.set()
    assert revised.revision == 2
    assert revised.export_status is ExportStatus.DRAFT
    assert revised.approved_snapshot_sha256 is None
    approved = outcome["approved"]
    assert approved.revision == 1
    assert approved.approved_snapshot_sha256 == saved.snapshot_sha256
    second = design_projects.export_revision(
        session=as_tenant(),
        user=_user(a),
        project_id=saved.id,
        number=2,
        request=RevisionExportRequest.model_validate({"format": "circuits_csv"}),
    )
    assert "DRAFT — NOT FOR EXECUTION" in _first_line(second)


def test_revision_endpoints_answer_revision_not_found_across_tenants(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    saved = _saved(as_tenant, a)
    with pytest.raises(NotFoundError) as approving:
        _approve(as_tenant, b, saved)
    assert approving.value.code == "revision_not_found"
    with pytest.raises(NotFoundError) as opening:
        design_projects.open_project(
            session=as_tenant(), user=_user(b), project_id=saved.id, revision=1
        )
    assert opening.value.code == "revision_not_found"
    # Opened without a revision, the answer is about the project.
    with pytest.raises(NotFoundError) as project:
        design_projects.open_project(session=as_tenant(), user=_user(b), project_id=saved.id)
    assert project.value.code == "project_not_found"
