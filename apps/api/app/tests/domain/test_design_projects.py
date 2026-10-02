"""Tests for `app/domain/design_projects.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFoundError, ValidationError
from app.domain import design_projects
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    ApproveRevisionRequest,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectDesignRequest,
    ProjectInfo,
    ReviseProjectRequest,
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
    with pytest.raises(ValidationError) as again:
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
