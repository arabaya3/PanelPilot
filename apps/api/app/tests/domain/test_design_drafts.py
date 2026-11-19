"""Server-backed drafts against a real database: privacy, concurrency, retries, purge.

Synthetic data only, in the disposable ``TEST_DATABASE_URL`` database.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant, cross_tenant
from app.domain import design_drafts, design_projects
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import (
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectDesignRequest,
    ProjectInfo,
    ReviseProjectRequest,
    SaveProjectRequest,
)
from app.models.schemas.design_drafts import DraftDelete, DraftWrite
from app.models.tables.base import Base
from app.models.tables.design_drafts import DesignDraftRequestRow, DesignDraftRow
from app.models.tables.tenant import TenantRow
from app.tests.accounts import account

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"), reason="needs Postgres (drafts are JSONB)"
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL, connect_args={"options": "-c lock_timeout=20000"})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="tenants")
def _tenants(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    with sessionmaker(bind=engine)() as session:
        session.execute(
            text(
                "TRUNCATE design_draft_requests, design_drafts, design_project_revisions, "
                "design_projects CASCADE"
            )
        )
        ids = []
        for slug in ("drafts-a", "drafts-b"):
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


@pytest.fixture(name="new")
def _new(engine: Engine) -> Iterator[Any]:
    opened: list[Session] = []

    def make() -> Session:
        session = sessionmaker(bind=engine)()
        opened.append(session)
        return session

    yield make
    for session in opened:
        session.close()


@pytest.fixture(autouse=True)
def _writes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_SERVER_DRAFTS", "true")


def _request() -> ProjectDesignRequest:
    return ProjectDesignRequest(
        info=ProjectInfo(name="Synthetic tower"),
        boards=[
            DistributionBoardRequest(
                name="DB1",
                loads=[
                    LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal("3"))
                ],
            )
        ],
    )


def _project(new: Any, user: CurrentUser) -> str:
    return design_projects.save_project(
        session=new(), user=user, request=SaveProjectRequest(name="T", request=_request())
    ).id


def _body(name: str = "Draft work") -> dict[str, Any]:
    return {
        "clean": "",
        "info": {"name": name, "number": "", "customer": "", "consultant": "", "contractor": ""},
        "boards": [{"name": "DB1", "loads": []}],
        "profileText": "",
        "language": None,
        "market": "",
    }


def _write(
    if_match: str | None,
    name: str = "Draft work",
    *,
    request_id: uuid.UUID | None = None,
    issued_at: datetime | None = None,
) -> DraftWrite:
    return DraftWrite.model_validate(
        {
            "request_id": str(request_id or uuid.uuid4()),
            "issued_at": (issued_at or datetime.now(UTC)).isoformat(),
            "if_match": if_match,
            "base_revision": 1,
            "body": _body(name),
        }
    )


def _delete(if_match: str, request_id: uuid.UUID | None = None) -> DraftDelete:
    return DraftDelete.model_validate(
        {
            "request_id": str(request_id or uuid.uuid4()),
            "issued_at": datetime.now(UTC).isoformat(),
            "if_match": if_match,
        }
    )


def _name(new: Any, user: CurrentUser, project: str) -> str | None:
    found = design_drafts.get_draft(session=new(), user=user, project_id=project).draft
    return None if found is None or found.body is None else found.body.info.name


# --- privacy ---------------------------------------------------------------


def test_only_the_author_reaches_a_draft(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    author = account(a)
    project = _project(new, author)
    design_drafts.put_draft(session=new(), user=author, project_id=project, write=_write(None))
    assert _name(new, author, project) == "Draft work"
    # Another member, an admin, the tenant's owner role: none sees it.
    for other in (account(a), account(a, frozenset({Role.ADMIN}))):
        assert design_drafts.get_draft(session=new(), user=other, project_id=project).draft is None
        assert design_drafts.list_drafts(session=new(), user=other).drafts == []
        with pytest.raises(ConflictError):  # their own "none" is not the author's draft
            design_drafts.delete_draft(
                session=new(), user=other, project_id=project, request=_delete("x.1")
            )
    # Another tenant does not even find the project.
    with pytest.raises(NotFoundError):
        design_drafts.get_draft(session=new(), user=account(b), project_id=project)
    with pytest.raises(NotFoundError):
        design_drafts.put_draft(
            session=new(), user=account(b), project_id=project, write=_write(None)
        )


def test_a_trial_gets_no_server_drafts(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    project = _project(new, account(a))
    trial = CurrentUser(id="trial-session", email="", tenant_id=str(a), roles=frozenset())
    with pytest.raises(AuthorizationError) as raised:
        design_drafts.put_draft(session=new(), user=trial, project_id=project, write=_write(None))
    assert raised.value.code == "server_drafts_need_account"


def test_a_deactivated_author_reaches_nothing_and_the_draft_stays(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    design_drafts.put_draft(session=new(), user=author, project_id=project, write=_write(None))
    with sessionmaker(bind=engine)() as session:
        session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"), {"id": author.id}
        )
        session.commit()
    # Requests are authenticated per call (CurrentUserDep refuses an inactive
    # account); the row stays until retention purges it.
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        assert session.scalars(select(DesignDraftRow)).one().state == "live"


def test_turning_writes_off_keeps_drafts_readable_and_deletable(tenants, new, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    saved = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None)
    )
    monkeypatch.setenv("DESIGN_SERVER_DRAFTS", "false")
    with pytest.raises(AuthorizationError) as raised:
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(saved.token)
        )
    assert raised.value.code == "server_drafts_disabled"
    lookup = design_drafts.get_draft(session=new(), user=author, project_id=project)
    assert lookup.writes_enabled is False
    assert lookup.draft is not None
    assert lookup.draft.token == saved.token
    deleted = design_drafts.delete_draft(
        session=new(), user=author, project_id=project, request=_delete(saved.token)
    )
    assert deleted.state == "deleted"


# --- concurrency -----------------------------------------------------------


def test_stale_writes_and_deletes_change_nothing(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    first = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None, "One")
    )
    second = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(first.token, "Two")
    )
    # Tab B still holds the first token.
    with pytest.raises(ConflictError) as raised:
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(first.token, "Stale")
        )
    assert raised.value.code == "draft_conflict"
    assert raised.value.params["token"] == second.token
    assert "Stale" not in str(raised.value.params)
    assert "Two" not in str(raised.value.params)
    with pytest.raises(ConflictError):
        design_drafts.delete_draft(
            session=new(), user=author, project_id=project, request=_delete(first.token)
        )
    # A second "first" write is refused too.
    with pytest.raises(ConflictError):
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(None, "Also first")
        )
    assert _name(new, author, project) == "Two"


def test_concurrent_first_writes_keep_exactly_one(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    outcomes: list[str] = []
    barrier = threading.Barrier(4)

    def write(name: str) -> None:
        barrier.wait()
        try:
            design_drafts.put_draft(
                session=new(), user=author, project_id=project, write=_write(None, name)
            )
            outcomes.append("ok")
        except ConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=write, args=(f"T{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "conflict", "conflict", "ok"]


def test_delete_and_recreate_never_reuses_a_token(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    seen: list[str] = []
    live = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None, "One")
    )
    seen.append(live.token)
    gone = design_drafts.delete_draft(
        session=new(), user=author, project_id=project, request=_delete(live.token)
    )
    seen.append(gone.token)
    # A delayed write against the old token, or expecting no draft, does not bring it back.
    with pytest.raises(ConflictError):
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(live.token, "Late")
        )
    with pytest.raises(ConflictError) as raised:
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(None, "Late first")
        )
    assert raised.value.params["state"] == "deleted"
    assert _name(new, author, project) is None
    # Written again on purpose, from the tombstone's token.
    again = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(gone.token, "Again")
    )
    assert again.token not in seen
    with pytest.raises(ConflictError):  # the very first token stays dead
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(live.token, "ABA")
        )
    assert _name(new, author, project) == "Again"


# --- idempotency -----------------------------------------------------------


def test_a_retry_is_answered_as_before_and_never_reapplied(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    rid = uuid.uuid4()
    issued = datetime.now(UTC)
    first = design_drafts.put_draft(
        session=new(),
        user=author,
        project_id=project,
        write=_write(None, "One", request_id=rid, issued_at=issued),
    )
    later = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(first.token, "Two")
    )
    retried = design_drafts.put_draft(
        session=new(),
        user=author,
        project_id=project,
        write=_write(None, "One", request_id=rid, issued_at=issued),
    )
    assert retried.replayed is True
    assert retried.token == first.token
    assert _name(new, author, project) == "Two"
    assert later.token != first.token
    # The same id with other input is refused.
    with pytest.raises(ValidationError) as raised:
        design_drafts.put_draft(
            session=new(),
            user=author,
            project_id=project,
            write=_write(None, "Changed", request_id=rid, issued_at=issued),
        )
    assert raised.value.code == "request_id_reused"


def test_a_retried_conflict_stays_a_conflict(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    one = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None)
    )
    rid = uuid.uuid4()
    issued = datetime.now(UTC)
    stale = _write(None, "Stale", request_id=rid, issued_at=issued)
    for _ in range(2):
        with pytest.raises(ConflictError):
            design_drafts.put_draft(session=new(), user=author, project_id=project, write=stale)
    assert one.token


def test_an_expired_request_is_refused(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    old = datetime.now(UTC) - timedelta(hours=25)
    with pytest.raises(ConflictError) as raised:
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(None, issued_at=old)
        )
    assert raised.value.code == "request_expired"


def test_request_records_never_hold_a_body(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None, "Secret words")
    )
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        for row in session.scalars(select(DesignDraftRequestRow)):
            assert "Secret words" not in str(row.outcome)


def test_a_body_over_the_limit_is_refused(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    big = _write(None)
    big.body.boards = [{"name": "DB", "note": "x" * 4000} for _ in range(200)]
    with pytest.raises(ValidationError) as raised:
        design_drafts.put_draft(session=new(), user=author, project_id=project, write=big)
    assert raised.value.code == "draft_too_large"


# --- retention -------------------------------------------------------------


def _age(engine: Engine, days: int) -> None:
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        then = datetime.now(UTC) - timedelta(days=days)
        session.execute(update(DesignDraftRow).values(updated_at=then))
        session.execute(update(DesignDraftRequestRow).values(created_at=then))
        session.commit()


def test_purge_deletes_only_what_is_past_retention(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    old_project = _project(new, author)
    design_drafts.put_draft(session=new(), user=author, project_id=old_project, write=_write(None))
    _age(engine, 31)
    fresh_project = _project(new, author)
    design_drafts.put_draft(
        session=new(), user=author, project_id=fresh_project, write=_write(None, "Fresh")
    )
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        drafts, _requests = design_drafts.purge_expired(session=session)
        session.commit()
    assert drafts == 1
    assert design_drafts.get_draft(session=new(), user=author, project_id=old_project).draft is None
    assert _name(new, author, fresh_project) == "Fresh"


def test_a_write_racing_the_purge_is_kept(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    saved = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None, "Old")
    )
    _age(engine, 31)
    # A write holds the row (updated now, not committed yet) while the purge runs.
    holder = sessionmaker(bind=engine)()
    bind_tenant(holder, a)
    holder.execute(
        update(DesignDraftRow)
        .where(DesignDraftRow.project_id == uuid.UUID(project))
        .values(updated_at=datetime.now(UTC))
    )
    result: list[int] = []

    def purge() -> None:
        with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
            result.append(design_drafts.purge_expired(session=session)[0])
            session.commit()

    thread = threading.Thread(target=purge)
    thread.start()
    thread.join(timeout=1)
    assert thread.is_alive()  # waiting on the row the write holds
    holder.commit()
    holder.close()
    thread.join()
    assert result == [0]
    assert _name(new, author, project) == "Old"
    assert saved.token


def test_after_a_purge_a_late_write_is_refused_and_a_read_finds_nothing(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    saved = design_drafts.put_draft(
        session=new(), user=author, project_id=project, write=_write(None)
    )
    _age(engine, 31)
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        design_drafts.purge_expired(session=session)
        session.commit()
    assert design_drafts.get_draft(session=new(), user=author, project_id=project).draft is None
    with pytest.raises(ConflictError) as raised:
        design_drafts.put_draft(
            session=new(), user=author, project_id=project, write=_write(saved.token, "Late")
        )
    assert raised.value.params["state"] == "none"


def test_deleting_the_project_deletes_its_drafts(tenants, new, engine) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a, frozenset({Role.ADMIN}))
    project = _project(new, author)
    design_drafts.put_draft(session=new(), user=author, project_id=project, write=_write(None))
    design_projects.delete_project(session=new(), user=author, project_id=project)
    with sessionmaker(bind=engine)() as session, cross_tenant(session, reason="test"):
        assert session.scalars(select(DesignDraftRow)).all() == []
        assert session.scalars(select(DesignDraftRequestRow)).all() == []


# --- publishing ------------------------------------------------------------


def test_expected_revision_is_checked_with_the_new_revision(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    design_projects.revise_project(
        session=new(),
        user=author,
        project_id=project,
        request=ReviseProjectRequest(request=_request(), expected_revision=1),
    )
    with pytest.raises(ConflictError) as raised:
        design_projects.revise_project(
            session=new(),
            user=author,
            project_id=project,
            request=ReviseProjectRequest(request=_request(), expected_revision=1),
        )
    assert raised.value.code == "revision_stale_base"
    # Older clients (no expected_revision) keep the earlier contract.
    third = design_projects.revise_project(
        session=new(),
        user=author,
        project_id=project,
        request=ReviseProjectRequest(request=_request()),
    )
    assert third.revision == 3


def test_concurrent_saves_from_the_same_base_keep_one(tenants, new) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    author = account(a)
    project = _project(new, author)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def save() -> None:
        barrier.wait()
        try:
            design_projects.revise_project(
                session=new(),
                user=author,
                project_id=project,
                request=ReviseProjectRequest(request=_request(), expected_revision=1),
            )
            outcomes.append("ok")
        except ConflictError as exc:
            outcomes.append(exc.code or "")

    threads = [threading.Thread(target=save) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["ok", "revision_stale_base"]
