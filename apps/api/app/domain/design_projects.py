"""Saved design projects: save, list, open, revise, delete.

A project is kept as what the engineer entered, one immutable revision per
save (``app.models.tables.design_projects``); opening it re-designs it. Every
function binds the session to the caller's tenant first, so a project of
another tenant does not exist as far as it can tell (ADR 0003), and asking
for one is answered as for an id that never existed.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

import structlog
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    ProjectDesignRequest,
    ProjectPage,
    ProjectSummary,
    ReviseProjectRequest,
    RevisionSummary,
    SavedProject,
    SaveProjectRequest,
)
from app.models.tables.design_projects import DesignProjectRow, DesignRevisionRow

logger = structlog.get_logger(__name__)

#: Revisions one project may hold. A project is revised by hand; a client
#: saving in a loop is stopped here rather than filling the table.
MAX_REVISIONS = 200

#: Projects one tenant may hold.
MAX_PROJECTS = 1000

_PAGE_DEFAULT = 25
_PAGE_MAX = 100


def _scope_to(session: Session, user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    bind_tenant(session, tenant)
    return tenant


def _load(session: Session, project_id: str) -> DesignProjectRow:
    try:
        key = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFoundError("no such project", code="project_not_found") from exc
    found = session.get(DesignProjectRow, key)
    if found is None:
        raise NotFoundError("no such project", code="project_not_found")
    return found


def _summary(row: DesignRevisionRow) -> RevisionSummary:
    return RevisionSummary(
        number=row.number,
        note=row.note,
        author=row.author,
        created_at=row.created_at.isoformat(),
    )


def _add_revision(
    session: Session,
    project: DesignProjectRow,
    tenant: uuid.UUID,
    user: CurrentUser,
    request: ProjectDesignRequest,
    note: str,
) -> None:
    if project.revision_count >= MAX_REVISIONS:
        raise ValidationError(
            f"a project holds at most {MAX_REVISIONS} revisions",
            code="project_revisions_full",
            params={"limit": str(MAX_REVISIONS)},
        )
    project.revision_count += 1
    session.add(
        DesignRevisionRow(
            tenant_id=tenant,
            project_id=project.id,
            number=project.revision_count,
            note=note,
            author=user.email,
            request=request.model_dump(mode="json"),
        )
    )


def _opened(session: Session, project: DesignProjectRow, number: int | None) -> SavedProject:
    revisions = session.scalars(
        select(DesignRevisionRow)
        .where(DesignRevisionRow.project_id == project.id)
        .order_by(DesignRevisionRow.number)
    ).all()
    chosen = (
        revisions[-1]
        if number is None
        else next((r for r in revisions if r.number == number), None)
    )
    if chosen is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    return SavedProject(
        id=str(project.id),
        name=project.name,
        revisions=[_summary(r) for r in revisions],
        revision=chosen.number,
        request=ProjectDesignRequest.model_validate(chosen.request),
    )


def save_project(
    *, session: Session, user: CurrentUser, request: SaveProjectRequest
) -> SavedProject:
    """Save a new project as its first revision.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        request: Its name, the project as entered, and a note.

    Returns:
        The project as saved.

    Raises:
        ValidationError: If the tenant already holds the most projects allowed.
    """
    tenant = _scope_to(session, user)
    held = session.scalars(select(DesignProjectRow.id).limit(MAX_PROJECTS)).all()
    if len(held) >= MAX_PROJECTS:
        raise ValidationError(
            f"at most {MAX_PROJECTS} projects can be saved",
            code="projects_full",
            params={"limit": str(MAX_PROJECTS)},
        )
    project = DesignProjectRow(tenant_id=tenant, name=request.name.strip(), revision_count=0)
    session.add(project)
    session.flush()
    _add_revision(session, project, tenant, user, request.request, request.note)
    session.commit()
    logger.info("design.project_saved", tenant_id=str(tenant), project_id=str(project.id))
    return _opened(session, project, None)


def revise_project(
    *, session: Session, user: CurrentUser, project_id: str, request: ReviseProjectRequest
) -> SavedProject:
    """Save a new revision of a project.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        project_id: The project.
        request: The project as entered now, and what changed.

    Returns:
        The project, at its new revision.

    Raises:
        NotFoundError: If no such project is the caller's.
        ValidationError: If it holds the most revisions allowed.
    """
    tenant = _scope_to(session, user)
    project = _load(session, project_id)
    # Raising the revision count updates the row, which moves its
    # ``updated_at`` (the list's order) to now.
    _add_revision(session, project, tenant, user, request.request, request.note)
    session.commit()
    logger.info(
        "design.project_revised",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=project.revision_count,
    )
    return _opened(session, project, None)


def open_project(
    *, session: Session, user: CurrentUser, project_id: str, revision: int | None = None
) -> SavedProject:
    """Open a project at its latest revision, or at one asked for.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.
        revision: The revision to open; ``None`` for the latest.

    Returns:
        The project, its revisions, and that revision as entered.

    Raises:
        NotFoundError: If no such project or revision is the caller's.
    """
    _scope_to(session, user)
    return _opened(session, _load(session, project_id), revision)


def _cursor(updated_at: datetime, project_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{project_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        when, key = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(when), uuid.UUID(key)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("not a cursor this service issued", code="bad_cursor") from exc


def list_projects(
    *, session: Session, user: CurrentUser, limit: int = _PAGE_DEFAULT, cursor: str | None = None
) -> ProjectPage:
    """List the caller's saved projects, most recently saved first.

    Args:
        session: Open database session.
        user: The authenticated caller.
        limit: Most rows to return; held to a server-side maximum.
        cursor: From a previous page, or ``None`` for the first.

    Returns:
        One page of projects.

    Raises:
        ValidationError: If ``limit`` is not positive or the cursor is not one
            this service issued.
    """
    _scope_to(session, user)
    if limit < 1:
        raise ValidationError("limit must be at least 1")
    limit = min(limit, _PAGE_MAX)
    query = select(DesignProjectRow).order_by(
        DesignProjectRow.updated_at.desc(), DesignProjectRow.id.desc()
    )
    if cursor is not None:
        when, key = _decode(cursor)
        query = query.where(
            tuple_(DesignProjectRow.updated_at, DesignProjectRow.id) < tuple_(when, key)
        )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = rows[:limit]
    return ProjectPage(
        projects=[
            ProjectSummary(
                id=str(row.id),
                name=row.name,
                revisions=row.revision_count,
                updated_at=row.updated_at.isoformat(),
            )
            for row in page
        ],
        next_cursor=_cursor(page[-1].updated_at, page[-1].id) if len(rows) > limit else None,
    )


def delete_project(*, session: Session, user: CurrentUser, project_id: str) -> None:
    """Delete a project and every revision of it.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.

    Raises:
        NotFoundError: If no such project is the caller's.
    """
    tenant = _scope_to(session, user)
    project = _load(session, project_id)
    session.delete(project)
    session.commit()
    logger.info("design.project_deleted", tenant_id=str(tenant), project_id=project_id)
