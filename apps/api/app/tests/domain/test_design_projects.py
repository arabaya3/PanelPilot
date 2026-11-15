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
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.design import RENDERER_VERSION, RULES_VERSION, findings_policy, marking, snapshot
from app.design.findings_policy import Classification
from app.domain import design_projects, roles
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import (
    ApproveRevisionRequest,
    DistributionBoardRequest,
    ExportStatus,
    FindingSeverity,
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
from app.models.tables.user import User
from app.tests.accounts import account

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: revisions are JSONB and tenant isolation is the database's",
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    # A lock waited on longer than any test holds one is a failure, not a hang.
    engine = create_engine(DATABASE_URL, connect_args={"options": "-c lock_timeout=20000"})
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


#: The callers here hold every permission (``app.domain.permissions``):
#: what an owner, a reviewer or another member may do is tested there and
#: in ``routes/test_permissions.py``.
ADMIN = frozenset({Role.ADMIN})


def _user(tenant: uuid.UUID, email: str = "eng@example.com") -> CurrentUser:
    """A new admin account of ``tenant`` (permissions are read from its row)."""
    return account(tenant, ADMIN, email)


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
    # The account has no users row here, so no display name: never its email.
    assert [(r.number, r.note, r.author) for r in saved.revisions] == [(1, "first issue", "")]
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
        request=ApproveRevisionRequest(note=" A. Rabaya "),
    )
    assert approved.revision == 1
    # No users row is behind this caller, so the approver is the fallback:
    # the note is never the approver.
    assert approved.revisions[0].approved_by == design_projects.APPROVER_FALLBACK
    assert approved.revisions[0].approval_note == "A. Rabaya"
    assert approved.revisions[0].approved_at is not None
    # The account comes from the approver's token, never from the request,
    # and is recorded; its email is not sent.
    assert approved.revisions[0].approver_account_recorded is True
    assert approved.revisions[0].approver_name is None
    assert approved.revisions[1].approved_by is None
    assert approved.revisions[1].approver_account_recorded is False
    # Opened at revision 1, the title block lists only revision 1, approved.
    (listed,) = approved.request.info.revisions
    assert listed.approved_by == design_projects.APPROVER_FALLBACK
    assert listed.approval_note == "A. Rabaya"
    latest = design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    assert [r.approved_by for r in latest.request.info.revisions] == [
        design_projects.APPROVER_FALLBACK,
        "",
    ]
    with pytest.raises(ConflictError) as again:
        design_projects.approve_revision(
            session=as_tenant(),
            user=user,
            project_id=saved.id,
            number=1,
            request=ApproveRevisionRequest(note="Someone else"),
        )
    assert again.value.code == "revision_already_approved"
    with pytest.raises(NotFoundError):
        design_projects.approve_revision(
            session=as_tenant(),
            user=user,
            project_id=saved.id,
            number=9,
            request=ApproveRevisionRequest(),
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
    fmt: str = "json",
) -> Any:
    return design_projects.export_revision(
        session=as_tenant(),
        user=_user(tenant),
        project_id=saved.id,
        number=1,
        request=RevisionExportRequest.model_validate({"format": fmt, "purpose": purpose}),
    )


def _approve(as_tenant: Any, tenant: uuid.UUID, saved: Any, note: str = "R. Engineer") -> Any:
    return design_projects.approve_revision(
        session=as_tenant(),
        user=_user(tenant),
        project_id=saved.id,
        number=1,
        request=ApproveRevisionRequest(note=note),
    )


def _first_line(exported: Any) -> str:
    """The export's status, as the project JSON states it: label, then identity."""
    status = json.loads(exported.content)["export_status"]
    return f"{status['label']} | {status['identity']}"


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
    # The caller has no users row: the account is named by the fallback, and
    # the note sent with the approval is never the approver.
    assert f"approved by {design_projects.APPROVER_FALLBACK}" in first
    assert "R. Engineer" not in first
    assert (saved.snapshot_sha256 or "")[: design_projects.SHORT_HASH] in first
    assert _export(as_tenant, a, saved, fmt="calculations_pdf").content.startswith(b"%PDF")


def test_an_unapproved_revision_exports_as_a_draft(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    first = _first_line(_export(as_tenant, a, _saved(as_tenant, a)))
    assert "DRAFT — NOT FOR EXECUTION" in first
    assert "revision 01" in first


@pytest.mark.parametrize("approved", [False, True])
def test_a_format_whose_mark_an_import_strips_is_refused(tenants, as_tenant, approved) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    if approved:
        _approve(as_tenant, a, saved)
    for fmt in ("circuits_csv", "qet", "aml", "plc_st"):
        with pytest.raises(ConflictError) as refused:
            _export(as_tenant, a, saved, fmt=fmt)
        assert refused.value.code == "export_format_unprotected"
        assert refused.value.params["format"] == fmt


def test_execution_is_always_refused_and_names_what_is_missing(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.code == "execution_package_incomplete"
    assert "control_schematics" in refused.value.params["missing"]
    assert refused.value.params["approval"] == "present"


def test_execution_stays_refused_when_nothing_is_listed_missing(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Producing the missing parts (Phase 2) must not by itself unlock execution.

    Releasing an execution package needs its own completeness and engineering
    validation; until that exists, emptying the list changes nothing.
    """
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    monkeypatch.setattr(marking, "EXECUTION_PACKAGE_MISSING", ())
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.code == "execution_package_incomplete"


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


def _reclassify_as_blocking(monkeypatch: pytest.MonkeyPatch, saved: Any) -> str:
    """Make today's policy call one of the revision's recorded findings blocking."""
    code: str = next(f.code for f in saved.findings if f.code in findings_policy.POLICY)
    monkeypatch.setitem(
        findings_policy.POLICY, code, Classification(FindingSeverity.BLOCKING, False, "test")
    )
    return code


def test_blocking_findings_refuse_approval(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    code = _reclassify_as_blocking(monkeypatch, _saved(as_tenant, a))
    # Saved under the stricter policy, so its recorded findings agree with it.
    saved = _saved(as_tenant, a)
    assert code in {f.code for f in saved.findings if f.severity is FindingSeverity.BLOCKING}
    with pytest.raises(ConflictError) as refused:
        _approve(as_tenant, a, saved)
    assert refused.value.code == "revision_has_blocking_findings"


def test_a_policy_change_after_approval_is_evaluated_not_ignored(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    _reclassify_as_blocking(monkeypatch, saved)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.export_status is ExportStatus.STALE
    # The approved snapshot is not rewritten: the same hash, still bound to it.
    assert opened.snapshot_sha256 == saved.snapshot_sha256
    assert opened.approved_snapshot_sha256 == saved.snapshot_sha256
    with pytest.raises(ConflictError) as stale:
        _export(as_tenant, a, saved)
    assert stale.value.code == "revision_stale"
    assert "findings_policy" in stale.value.params["changed"]


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
            outcome["result"] = _approve(as_tenant, a, saved, note="Second Approver")
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
    # A legacy typed name is never the approver; its account is not this
    # tenant's, so the approval names the fallback.
    assert opened.revisions[0].approved_by == design_projects.APPROVER_FALLBACK
    assert "First Approver" not in opened.model_dump_json()
    assert opened.approved_snapshot_sha256 == saved.snapshot_sha256


def test_a_save_during_an_approval_is_a_new_unapproved_revision(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A save waits for the approval holding the project, then is a new revision.

    It does not change the revision being approved: the approval is of
    revision 1's design, and the save is revision 2, unapproved.
    """
    a, _ = tenants
    saved = _saved(as_tenant, a)
    approving = threading.Event()
    release = threading.Event()
    outcome: dict[str, Any] = {}
    original = design_projects._approvable

    def slow(session: Any, project: Any, row: Any, number: int, applicable: Any) -> None:
        original(session, project, row, number, applicable)
        approving.set()
        release.wait(timeout=30)

    def approve() -> None:
        outcome["approved"] = _approve(as_tenant, a, saved)

    def revise() -> None:
        outcome["revised"] = design_projects.revise_project(
            session=as_tenant(),
            user=_user(a),
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request(kw="4"), note="more sockets"),
        )

    monkeypatch.setattr(design_projects, "_approvable", slow)
    approver = threading.Thread(target=approve)
    reviser = threading.Thread(target=revise)
    try:
        approver.start()
        assert approving.wait(timeout=30)
        reviser.start()
        reviser.join(timeout=1)
        # The approval holds the project row, as a save does: the save waits.
        assert reviser.is_alive()
    finally:
        release.set()
        approver.join(timeout=30)
        reviser.join(timeout=30)
    revised = outcome["revised"]
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
        request=RevisionExportRequest.model_validate({"format": "json"}),
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


def test_a_delete_during_an_approval_waits_then_is_refused(tenants, as_tenant, engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A delete started while a revision is being approved never removes it."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    approving = threading.Event()
    release = threading.Event()
    outcome: dict[str, Any] = {}
    original = design_projects._approvable

    def slow(session: Any, project: Any, row: Any, number: int, applicable: Any) -> None:
        original(session, project, row, number, applicable)
        approving.set()
        release.wait(timeout=30)

    def approve() -> None:
        outcome["approved"] = _approve(as_tenant, a, saved)

    def delete() -> None:
        try:
            design_projects.delete_project(session=as_tenant(), user=_user(a), project_id=saved.id)
            outcome["deleted"] = None
        except BaseException as exc:
            outcome["deleted"] = exc

    monkeypatch.setattr(design_projects, "_approvable", slow)
    approver = threading.Thread(target=approve)
    deleter = threading.Thread(target=delete)
    try:
        approver.start()
        assert approving.wait(timeout=30)
        deleter.start()
        deleter.join(timeout=1)
        # The approval holds the project row: the delete waits for it.
        assert deleter.is_alive()
    finally:
        release.set()
        approver.join(timeout=30)
        deleter.join(timeout=30)
    assert outcome["approved"].approved_snapshot_sha256 == saved.snapshot_sha256
    refused = outcome["deleted"]
    assert isinstance(refused, ValidationError)
    assert refused.code == "project_approved"
    with engine.connect() as conn:
        kept = conn.execute(
            text("SELECT approval_note FROM design_project_revisions WHERE number = 1")
        ).scalar_one()
    assert kept == "R. Engineer"


def test_an_approval_during_a_delete_waits_then_finds_no_revision(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    """An approval started while the project is being deleted approves nothing."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    holder = engine.connect()
    holder.begin()
    holder.execute(text("DELETE FROM design_projects WHERE id = :id"), {"id": saved.id})
    outcome: dict[str, Any] = {}

    def approve() -> None:
        try:
            outcome["approved"] = _approve(as_tenant, a, saved)
        except BaseException as exc:
            outcome["approved"] = exc

    worker = threading.Thread(target=approve)
    try:
        worker.start()
        worker.join(timeout=1)
        # The delete holds the project row: the approval waits for it.
        waited = worker.is_alive()
    finally:
        holder.commit()
        holder.close()
        worker.join(timeout=30)
    assert waited
    refused = outcome["approved"]
    assert isinstance(refused, NotFoundError)
    assert refused.code == "revision_not_found"
    with engine.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM design_project_revisions")).scalar()
    assert left == 0


def test_an_approval_request_names_no_one_and_bounds_its_note() -> None:
    for named in ({"approver": "Someone Else"}, {"approver": "X", "note": "ok"}):
        with pytest.raises(PydanticValidationError):
            ApproveRevisionRequest.model_validate(named)
    with pytest.raises(PydanticValidationError):
        ApproveRevisionRequest(note="n" * 201)
    assert ApproveRevisionRequest(note="  checked on site ").note == "checked on site"
    assert ApproveRevisionRequest().note == ""


def _long_description(engine: Engine) -> str:
    """Store a request today's bounds refuse, as an older revision may hold."""
    long = "x" * 600
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_project_revisions SET request = jsonb_set("
                "request, '{boards,0,loads,0,description}', to_jsonb(CAST(:d AS text)))"
            ),
            {"d": long},
        )
    return long


def test_a_revision_whose_request_no_longer_validates_still_opens(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    long = _long_description(engine)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    # The stored request is returned as it is, for the engineer to correct.
    body = opened.model_dump(mode="json")
    assert body["request"]["boards"][0]["loads"][0]["description"] == long
    assert [r["index"] for r in body["request"]["info"]["revisions"]] == ["01"]
    assert opened.export_status is ExportStatus.NOT_VALIDATED
    assert opened.revisions[0].export_status is ExportStatus.NOT_VALIDATED
    assert "request_not_valid" in {found.code for found in opened.findings}
    assert "not_validated" in opened.execution_blockers
    with pytest.raises(ConflictError) as exported:
        _export(as_tenant, a, saved)
    assert exported.value.code == "revision_not_validated"
    with pytest.raises(ConflictError) as approving:
        _approve(as_tenant, a, saved)
    assert approving.value.code == "revision_not_validated"


def test_a_non_domain_error_releases_the_project_lock(tenants, as_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)

    def broken(request: Any) -> dict[str, Any]:
        raise RuntimeError("design engine failed")

    monkeypatch.setattr(design_projects, "_validate", broken)
    # Kept open, as a request's session is until its response is sent.
    failed = as_tenant()
    with pytest.raises(RuntimeError):
        design_projects.revise_project(
            session=failed,
            user=_user(a),
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request()),
        )
    monkeypatch.undo()
    outcome: dict[str, Any] = {}

    def revise() -> None:
        outcome["revised"] = design_projects.revise_project(
            session=as_tenant(),
            user=_user(a),
            project_id=saved.id,
            request=ReviseProjectRequest(request=_request(kw="4")),
        )

    worker = threading.Thread(target=revise)
    worker.start()
    worker.join(timeout=10)
    blocked = worker.is_alive()
    failed.close()
    worker.join(timeout=30)
    assert not blocked, "the failed revise still held the project lock"
    assert outcome["revised"].revision == 2


def test_a_failed_rollback_does_not_replace_the_error() -> None:
    class GoneConnection:
        def rollback(self) -> None:
            raise RuntimeError("connection gone")

    # Logged, not raised: the caller re-raises the failure that came first.
    design_projects._release(GoneConnection())  # type: ignore[arg-type]


# --- Approval identity: the signed-in account, by display name, never email --


def _account(engine: Engine, tenant: uuid.UUID, email: str, full_name: str | None) -> CurrentUser:
    """A users row for ``email`` in ``tenant``, replacing any left by another test.

    Returns:
        The caller signed in as that account.
    """
    with sessionmaker(bind=engine)() as session:
        session.execute(
            text(
                "UPDATE tenants SET owner_user_id = NULL WHERE owner_user_id IN "
                "(SELECT id FROM users WHERE email = :email)"
            ),
            {"email": email},
        )
        session.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        user_id = uuid.uuid4()
        session.add(User(id=user_id, tenant_id=tenant, email=email, full_name=full_name))
        session.flush()
        bind_tenant(session, tenant)
        roles.grant_role(session=session, email=email, role=Role.ADMIN)
        session.commit()
    return CurrentUser(id=str(user_id), email=email, tenant_id=str(tenant), roles=ADMIN)


def _emails_in(value: object) -> list[str]:
    return [token for token in json.dumps(value).split('"') if "@" in token]


def _approve_as(as_tenant: Any, user: CurrentUser, saved: Any, note: str = "") -> Any:
    return design_projects.approve_revision(
        session=as_tenant(),
        user=user,
        project_id=saved.id,
        number=1,
        request=ApproveRevisionRequest(note=note),
    )


def _pdf_text(raw: bytes) -> str:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _body(exported: Any, fmt: str) -> str:
    content = exported.content
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    return _pdf_text(raw) if fmt.endswith("pdf") else raw.decode("utf-8", "replace")


def test_an_approval_names_the_account_and_never_sends_its_email(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    user = _account(engine, a, "named-approver@example.com", "  Rana Haddad ")
    saved = design_projects.save_project(
        session=as_tenant(), user=user, request=SaveProjectRequest(name="T", request=_request())
    )
    assert saved.approving_as == "Rana Haddad"
    approved = _approve_as(as_tenant, user, saved, note="R. Haddad")
    (revision,) = approved.revisions
    assert revision.approved_by == "Rana Haddad"
    assert revision.approver_account_recorded is True
    assert revision.approver_name == "Rana Haddad"
    assert revision.approval_note == "R. Haddad"
    assert revision.author == "Rana Haddad"
    (listed,) = approved.request.info.revisions
    assert (listed.approved_by, listed.approval_note) == ("Rana Haddad", "R. Haddad")
    assert _emails_in(approved.model_dump(mode="json")) == []
    opened = design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    assert _emails_in(opened.model_dump(mode="json")) == []
    page = design_projects.list_projects(session=as_tenant(), user=user)
    assert _emails_in(page.model_dump(mode="json")) == []
    # The audit identity is the server's: email and id, from the token. The
    # legacy typed-name column is left empty, and the note is kept apart.
    with engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT approved_account, approved_user_id, approved_by, approval_note "
                "FROM design_project_revisions"
            )
        ).one()
    assert tuple(stored) == ("named-approver@example.com", uuid.UUID(user.id), None, "R. Haddad")


def test_every_export_names_the_account_and_labels_the_note(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    user = _account(engine, a, "eng@example.com", "Rana Haddad")
    saved = _saved(as_tenant, a)
    _approve_as(as_tenant, user, saved, note="Site check ZQN")
    for fmt in ("json", "pdf", "calculations_pdf", "dxf"):
        exported = _export(as_tenant, a, saved, fmt=fmt)
        body = _body(exported, fmt)
        assert "eng@example.com" not in body, fmt
        assert "Rana Haddad" in body, fmt
        if fmt == "calculations_pdf":
            assert "Review approved by Rana Haddad" in body
            assert "Approval note: Site check ZQN" in body
            assert "approved by Site check" not in body
        if fmt == "json":
            document = json.loads(body)
            (row,) = document["project"]["info"]["revisions"]
            assert (row["approved_by"], row["approval_note"]) == ("Rana Haddad", "Site check ZQN")
            assert "approved by Rana Haddad" in json.dumps(document["export_status"])
        if fmt in ("pdf", "dxf"):
            # The drawing's slots name the approver only; the note has none.
            assert "Site check ZQN" not in body, fmt


@pytest.mark.parametrize("full_name", [None, "", "   ", "eng@example.com"])
def test_an_account_without_a_usable_name_is_a_team_member(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, full_name
) -> None:
    a, _ = tenants
    user = _account(engine, a, "eng@example.com", full_name)
    saved = _saved(as_tenant, a)
    opened = design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    assert opened.approving_as is None
    approved = _approve_as(as_tenant, user, saved)
    (revision,) = approved.revisions
    assert revision.approver_account_recorded is True
    assert revision.approver_name is None
    assert revision.approved_by == design_projects.APPROVER_FALLBACK
    assert revision.author == ""
    assert _emails_in(approved.model_dump(mode="json")) == []
    calc = _body(_export(as_tenant, a, saved, fmt="calculations_pdf"), "calculations_pdf")
    assert "Review approved by a team member" in calc
    assert "eng@example.com" not in calc


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("legacy@example.com", "Real Legacy Account"),
        ("gone@example.com", design_projects.APPROVER_FALLBACK),
        (None, design_projects.APPROVER_FALLBACK),
    ],
)
def test_a_legacy_typed_name_is_never_the_approver(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, email, expected
) -> None:
    """A row approved before this change: a typed name, perhaps an email, no id."""
    a, _ = tenants
    viewer = _account(engine, a, "legacy@example.com", "Real Legacy Account")
    saved = _saved(as_tenant, a)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_project_revisions SET approved_by = 'TYPED LEGACY ZQX', "
                "approved_account = :e, approved_at = now(), "
                "approved_snapshot_sha256 = snapshot_sha256, "
                # Recorded, so the export shows the name it is about.
                "approved_renderer_version = :r WHERE project_id = :p"
            ),
            {"e": email, "p": uuid.UUID(saved.id), "r": RENDERER_VERSION},
        )
    opened = design_projects.open_project(session=as_tenant(), user=viewer, project_id=saved.id)
    (revision,) = opened.revisions
    assert revision.approved_by == expected
    assert revision.approver_account_recorded is (email is not None)
    assert "TYPED LEGACY ZQX" not in opened.model_dump_json()
    for fmt in ("json", "pdf", "calculations_pdf", "dxf"):
        body = _body(_export(as_tenant, a, saved, fmt=fmt), fmt)
        assert "TYPED LEGACY ZQX" not in body, fmt
        assert expected in body, fmt


def test_an_approval_by_id_ignores_an_email_now_held_by_another_account(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    first = _account(engine, a, "reused@example.com", "First Holder")
    saved = _saved(as_tenant, a)
    _approve_as(as_tenant, first, saved)
    # The account is deleted and its email given to a new account.
    _account(engine, a, "reused@example.com", "Second Holder")
    opened = design_projects.open_project(session=as_tenant(), user=first, project_id=saved.id)
    # The name is frozen with the approval: who signed stays who signed, and
    # the email's new holder never lends a name.
    assert opened.revisions[0].approved_by == "First Holder"
    assert "Second Holder" not in opened.model_dump_json()


def test_a_trial_caller_cannot_approve(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = _saved(as_tenant, a)
    # A trial token resolves to an engineer with no email (``resolve_roles``).
    trial = CurrentUser(id=str(uuid.uuid4()), email="", tenant_id=str(a), roles=frozenset())
    with pytest.raises(AuthorizationError) as refused:
        _approve_as(as_tenant, trial, saved)
    assert refused.value.code == "approval_requires_account"
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.revisions[0].approved_at is None


def test_another_tenants_account_lends_no_name_and_sees_no_approval(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, b = tenants
    # The approving account's row belongs to tenant B: tenant A's revision
    # recorded it, but its name is not A's to read.
    # A caller whose token names tenant A but whose account is B's holds no
    # permission in A (``permissions.require`` reads the account in A), so
    # the recorded approver is pointed at B's account directly.
    in_b = _account(engine, b, "eng@example.com", "Someone In B")
    forged = in_b.model_copy(update={"tenant_id": str(a)})
    saved = _saved(as_tenant, a)
    with pytest.raises(AuthorizationError):
        _approve_as(as_tenant, forged, saved)
    _approve_as(as_tenant, _user(a), saved)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE design_project_revisions SET approved_user_id = :u WHERE project_id = :p"),
            {"u": uuid.UUID(in_b.id), "p": uuid.UUID(saved.id)},
        )
    approved = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    (revision,) = approved.revisions
    assert revision.approver_account_recorded is True
    assert revision.approver_name is None
    assert revision.approved_by == design_projects.APPROVER_FALLBACK
    assert approved.approving_as is None
    assert "Someone In B" not in approved.model_dump_json()
    # Tenant B sees neither the project nor its approval.
    with pytest.raises(NotFoundError):
        design_projects.open_project(session=as_tenant(), user=_user(b), project_id=saved.id)
    listed = design_projects.list_projects(session=as_tenant(), user=_user(b))
    assert listed.model_dump(mode="json")["projects"] == []


# --------------------------------------------------------------------------- #
# Permissions (app.domain.permissions): who approves and who deletes
# --------------------------------------------------------------------------- #


def _member(tenant: uuid.UUID, *roles: Role) -> CurrentUser:
    """A caller who is not the tenant's owner, holding ``roles`` and no admin."""
    return account(tenant, frozenset(roles), "member@example.com")


def test_responses_carry_the_callers_permissions(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="T", request=_request())
    )
    assert saved.permissions == ["approve_revision", "delete_project", "edit_company_settings"]
    page = design_projects.list_projects(session=as_tenant(), user=_member(a))
    assert page.permissions == []
    opened = design_projects.open_project(
        session=as_tenant(), user=_member(a, Role.REVIEWER), project_id=saved.id
    )
    assert opened.permissions == ["approve_revision"]


def test_a_member_may_neither_approve_nor_delete(tenants, as_tenant, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="T", request=_request())
    )
    member = _member(a)
    with pytest.raises(AuthorizationError) as refused:
        _approve_as(as_tenant, member, saved)
    assert refused.value.code == "approval_not_permitted"
    with pytest.raises(AuthorizationError) as refused:
        design_projects.delete_project(session=as_tenant(), user=member, project_id=saved.id)
    assert refused.value.code == "project_delete_not_permitted"
    # Nothing changed, and the refusals released the project's lock: the
    # owner approves straight after.
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM design_projects")).scalar() == 1
    approved = _approve_as(as_tenant, _user(a), saved)
    assert approved.revisions[0].approved_at is not None


def test_a_reviewer_approves_but_does_not_delete(tenants, as_tenant) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    saved = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="T", request=_request())
    )
    other = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="U", request=_request())
    )
    reviewer = _member(a, Role.REVIEWER)
    approved = _approve_as(as_tenant, reviewer, saved)
    assert approved.revisions[0].approved_at is not None
    with pytest.raises(AuthorizationError) as refused:
        design_projects.delete_project(session=as_tenant(), user=reviewer, project_id=other.id)
    assert refused.value.code == "project_delete_not_permitted"


@pytest.mark.parametrize("who", ["admin", "member", "reviewer"])
def test_another_tenants_project_is_not_found_whoever_asks(  # type: ignore[no-untyped-def]
    tenants, as_tenant, who
) -> None:
    """Checked after the project is found, so a refusal never confirms one exists."""
    a, b = tenants
    saved = design_projects.save_project(
        session=as_tenant(), user=_user(a), request=SaveProjectRequest(name="T", request=_request())
    )
    caller = {
        "admin": _user(b),
        "member": _member(b),
        "reviewer": _member(b, Role.REVIEWER),
    }[who]
    with pytest.raises(NotFoundError) as missing:
        _approve_as(as_tenant, caller, saved)
    assert missing.value.code == "revision_not_found"
    with pytest.raises(NotFoundError) as missing:
        design_projects.delete_project(session=as_tenant(), user=caller, project_id=saved.id)
    assert missing.value.code == "project_not_found"


def test_an_approval_records_the_policy_and_name_it_was_granted_under(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    a, _ = tenants
    user = _account(engine, a, "policy-approver@example.com", " Lina Odeh ")
    saved = _saved(as_tenant, a)
    _approve_as(as_tenant, user, saved)
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT profile_sha256, rules_version, catalogue_version, findings, "
                    "approved_profile_sha256, approved_rules_version, "
                    "approved_catalogue_version, approved_findings_policy, "
                    "approved_display_name FROM design_project_revisions"
                )
            )
            .mappings()
            .one()
        )
    assert row["approved_profile_sha256"] == row["profile_sha256"]
    assert row["approved_rules_version"] == row["rules_version"] == RULES_VERSION
    assert row["approved_catalogue_version"] == row["catalogue_version"]
    stored = sorted(
        [f["code"], f["severity"], f["requires_engineering_confirmation"]] for f in row["findings"]
    )
    assert row["approved_findings_policy"] == snapshot.sha256_of(stored)
    assert row["approved_display_name"] == "Lina Odeh"
    opened = design_projects.open_project(session=as_tenant(), user=user, project_id=saved.id)
    assert opened.approval_current is True
    assert opened.approval_changed == []


@pytest.mark.parametrize("backfilled", [True, False])
def test_a_legacy_approved_row_counts_only_with_a_recorded_renderer(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, backfilled
) -> None:
    """A row approved before the policy was recorded: as the migration leaves it, or untouched."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_project_revisions SET approved_at = now(), "
                "approved_snapshot_sha256 = snapshot_sha256, approved_account = 'x@example.com'"
            )
        )
        if backfilled:
            # What b4e8d2f6a9c1 writes: the revision's own versions and hash,
            # the findings policy left NULL, the name frozen ("" for none).
            conn.execute(
                text(
                    "UPDATE design_project_revisions SET "
                    "approved_profile_sha256 = profile_sha256, "
                    "approved_rules_version = rules_version, "
                    "approved_catalogue_version = catalogue_version, "
                    "approved_display_name = ''"
                )
            )
    # No renderer is recorded for it: which one exported it is unknown, so it
    # does not count, and its exports are refused until it is granted again.
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_current is False
    assert opened.approval_changed == ["renderer_version"]
    with pytest.raises(ConflictError) as stale:
        _export(as_tenant, a, saved)
    assert stale.value.params["changed"] == "renderer_version"
    assert stale.value.params["approved_renderer_version"] == "unversioned"
    # Otherwise it counts, as the policy the migration recorded says.
    _set(engine, approved_renderer_version=RENDERER_VERSION)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_current is True
    assert opened.export_status is ExportStatus.REVIEW_APPROVED
    first = _first_line(_export(as_tenant, a, saved))
    assert "REVIEW APPROVED" in first
    assert f"approved by {design_projects.APPROVER_FALLBACK}" in first


def test_an_approval_recorded_under_other_rules_is_not_current(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    """The recorded policy is compared even where the revision's own still matches."""
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    _set(engine, approved_rules_version="0.0.0-approved")
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_current is False
    assert opened.approval_changed == ["rules_version"]
    assert opened.export_status is ExportStatus.DRAFT
    assert "approval" in opened.execution_blockers
    with pytest.raises(ConflictError) as stale:
        _export(as_tenant, a, saved)
    assert stale.value.code == "revision_stale"
    assert stale.value.params["changed"] == "rules_version"


def test_a_renderer_change_since_approval_refuses_the_approved_export(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, monkeypatch
) -> None:
    """A material renderer change never prints a different file as the same approval.

    The snapshot, its hash and the approval record are frozen; only the
    approval stops counting. A revision saved again under the new renderer
    can be approved and exported.
    """
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    with engine.connect() as conn:
        frozen = conn.execute(
            text(
                "SELECT design_snapshot, snapshot_sha256, approved_snapshot_sha256, "
                "approved_renderer_version FROM design_project_revisions"
            )
        ).one()
    assert frozen.approved_renderer_version == RENDERER_VERSION
    assert "REVIEW APPROVED" in _first_line(_export(as_tenant, a, saved))

    monkeypatch.setattr(design_projects, "RENDERER_VERSION", "9999.01.01.1")
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_current is False
    assert opened.approval_changed == ["renderer_version"]
    for fmt in ("json", "pdf", "dxf"):
        with pytest.raises(ConflictError) as stale:
            _export(as_tenant, a, saved, fmt=fmt)
        assert stale.value.code == "revision_stale"
        assert stale.value.params["changed"] == "renderer_version"
        assert stale.value.params["approved_renderer_version"] == frozen.approved_renderer_version
        assert stale.value.params["current_renderer_version"] == "9999.01.01.1"
    with pytest.raises(ConflictError) as execution:
        _export(as_tenant, a, saved, purpose="execution")
    assert execution.value.code == "execution_package_incomplete"
    with engine.connect() as conn:
        after = conn.execute(
            text(
                "SELECT design_snapshot, snapshot_sha256, approved_snapshot_sha256, "
                "approved_renderer_version FROM design_project_revisions"
            )
        ).one()
    assert tuple(after) == tuple(frozen)


def test_an_unrelated_settings_change_leaves_the_approved_exports_unchanged(  # type: ignore[no-untyped-def]
    tenants, as_tenant
) -> None:
    """A live company settings change does not reach a saved revision's files.

    The drawing PDF, calculations PDF and DXF of an approved revision are
    exported, the tenant's company settings are changed, and they are
    exported again: byte for byte the same. The files carry no time of
    export (the PDF is written ``invariant``), so nothing is expected to
    differ.
    """
    from app.domain import company_settings

    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    formats = ("pdf", "calculations_pdf", "dxf")
    before = {fmt: _export(as_tenant, a, saved, fmt=fmt).content for fmt in formats}
    company_settings.save_settings(
        session=as_tenant(),
        user=_user(a),
        settings={"key": "synthetic-co", "name": "Synthetic Renamed Co", "max_circuits_per_rcd": 4},
    )
    after = {fmt: _export(as_tenant, a, saved, fmt=fmt).content for fmt in formats}
    assert after == before


def test_a_legacy_approval_is_recovered_on_a_new_revision_and_kept_in_history(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    """The recovery path for an approval granted before renderer versions were recorded.

    The legacy approval is never re-granted in place (that would overwrite the
    only record of it): the stored request is saved again as a new revision,
    designed now, and that one is approved. The old revision keeps its
    snapshot and its approval record, shown as no longer counting, and its
    exports stay refused.
    """
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    _set(engine, approved_renderer_version=None)
    columns = (
        "number, design_snapshot, snapshot_sha256, approved_at, approved_snapshot_sha256, "
        "approved_display_name, approved_rules_version, approved_renderer_version"
    )
    with engine.connect() as conn:
        legacy = conn.execute(text(f"SELECT {columns} FROM design_project_revisions")).one()
        # Every column of the old row, to compare once the recovery is done.
        whole = dict(conn.execute(text("SELECT * FROM design_project_revisions")).mappings().one())

    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_changed == ["renderer_version"]
    with pytest.raises(ConflictError) as stale:
        _export(as_tenant, a, saved, fmt="pdf")
    assert stale.value.code == "revision_stale"
    # Not re-approved in place.
    with pytest.raises(ConflictError) as again:
        _approve(as_tenant, a, saved)
    assert again.value.code == "revision_already_approved"

    # Recovery: save the stored request again, then approve the new revision.
    revised = design_projects.revise_project(
        session=as_tenant(),
        user=_user(a),
        project_id=saved.id,
        request=ReviseProjectRequest(
            request=opened.request, note="Re-approval under the current renderer"
        ),
    )
    assert revised.revision == 2
    design_projects.approve_revision(
        session=as_tenant(),
        user=_user(a),
        project_id=saved.id,
        number=2,
        request=ApproveRevisionRequest(note="R. Engineer"),
    )
    exported = design_projects.export_revision(
        session=as_tenant(),
        user=_user(a),
        project_id=saved.id,
        number=2,
        request=RevisionExportRequest.model_validate({"format": "json", "purpose": "review"}),
    )
    first = _first_line(exported)
    assert "REVIEW APPROVED" in first
    assert "revision 02" in first
    assert f"renderer {RENDERER_VERSION}" in first

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {columns} FROM design_project_revisions ORDER BY number")
        ).all()
    assert tuple(rows[0]) == tuple(legacy)
    with engine.connect() as conn:
        after = dict(
            conn.execute(text("SELECT * FROM design_project_revisions WHERE number = 1"))
            .mappings()
            .one()
        )
    assert after == whole
    # Same inputs, same rules: the same design, now approved under this renderer.
    assert rows[1].snapshot_sha256 == legacy.snapshot_sha256
    assert rows[1].approved_renderer_version == RENDERER_VERSION
    with pytest.raises(ConflictError):
        _export(as_tenant, a, saved, fmt="pdf")
    history = design_projects.open_project(
        session=as_tenant(), user=_user(a), project_id=saved.id
    ).revisions
    assert [entry.number for entry in history] == [1, 2]
    assert history[0].approved_at is not None


# --- An approval of a snapshot that no longer reads; earlier revisions' approvals


def _replace_snapshot(engine: Engine, stored: object, *, rehash: bool) -> None:
    """Write ``stored`` as revision 1's design; with ``rehash`` its hash (and the approval's) too."""
    digest = snapshot.sha256_of(stored)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE design_project_revisions SET design_snapshot = CAST(:stored AS JSONB)"
                + (
                    ", snapshot_sha256 = :digest, approved_snapshot_sha256 = :digest"
                    if rehash
                    else ""
                )
                + " WHERE number = 1"
            ),
            {"stored": json.dumps(stored), "digest": digest},
        )


@pytest.mark.parametrize(
    ("stored", "rehash"),
    [
        # Hashes as recorded, but is a finding, not a design.
        (
            {
                "code": "x",
                "severity": "warning",
                "values": {},
                "message": "m",
                "requires_engineering_confirmation": False,
                "source": "policy",
            },
            True,
        ),
        ({"schema": "unknown-9"}, True),
        # Altered without its hash: fails the hash check.
        ({"schema": "unknown-9"}, False),
    ],
)
def test_an_approval_of_a_snapshot_that_does_not_read_does_not_count(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine, stored, rehash
) -> None:
    a, _ = tenants
    saved = _saved(as_tenant, a)
    _approve(as_tenant, a, saved)
    _replace_snapshot(engine, stored, rehash=rehash)
    opened = design_projects.open_project(session=as_tenant(), user=_user(a), project_id=saved.id)
    assert opened.approval_current is False
    assert "snapshot" in opened.approval_changed
    assert opened.revisions[0].approval_current is False
    assert "approval" in opened.execution_blockers
    assert opened.export_status is not ExportStatus.REVIEW_APPROVED
    with pytest.raises(ConflictError) as refused:
        _export(as_tenant, a, saved, purpose="execution")
    assert refused.value.params["approval"] == "missing"
    assert "approval" in refused.value.params["blockers"].split(",")


def test_an_earlier_approval_under_another_profile_is_not_current_in_the_list(  # type: ignore[no-untyped-def]
    tenants, as_tenant, engine
) -> None:
    """The list and the title block compare an earlier approval's profile and findings too."""
    a, _ = tenants
    user = _account(engine, a, "earlier-approver@example.com", "Rana Haddad")
    saved = _saved(as_tenant, a)
    _approve_as(as_tenant, user, saved, note="Site check")
    design_projects.revise_project(
        session=as_tenant(),
        user=_user(a),
        project_id=saved.id,
        request=ReviseProjectRequest(request=_request(kw="5"), note="more"),
    )

    def _listed_and_titled() -> tuple[bool | None, str]:
        opened = design_projects.open_project(
            session=as_tenant(), user=_user(a), project_id=saved.id
        )
        exported = design_projects.export_revision(
            session=as_tenant(),
            user=_user(a),
            project_id=saved.id,
            number=2,
            request=RevisionExportRequest.model_validate({"format": "json", "purpose": "review"}),
        )
        first = json.loads(exported.content)["project"]["info"]["revisions"][0]
        return opened.revisions[0].approval_current, first["approved_by"]

    assert _listed_and_titled() == (True, "Rana Haddad")
    for column, value in (
        ("approved_profile_sha256", "d" * 64),
        ("approved_findings_policy", "0" * 64),
    ):
        with engine.begin() as conn:
            original = conn.execute(
                text(f"SELECT {column} FROM design_project_revisions WHERE number = 1")
            ).scalar_one()
            conn.execute(
                text(f"UPDATE design_project_revisions SET {column} = :v WHERE number = 1"),
                {"v": value},
            )
        assert _listed_and_titled() == (False, ""), column
        with engine.begin() as conn:
            conn.execute(
                text(f"UPDATE design_project_revisions SET {column} = :v WHERE number = 1"),
                {"v": original},
            )
