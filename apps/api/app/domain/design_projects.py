"""Saved design projects: save, list, open, revise, approve, export, delete.

A project is kept as what the engineer entered, one immutable revision per
save (``app.models.tables.design_projects``). Each save also designs the
request on the server and stores that design (its snapshot), the snapshot's
canonical SHA-256, the profile hash, the rules and catalogue versions, and the
findings (``app.design.integrity``); a design the client sent is never stored.

An engineer approves a revision once, by name, with the revision row locked;
the approval records the snapshot hash it approved, and counts only while that
equals the revision's. A revision with no snapshot (saved before snapshots
were kept, or whose design was refused) or with blocking findings cannot be
approved. A revision export is made from the stored snapshot alone, after its
hash, rules version and profile hash are checked again: a revision whose rules
changed is refused as stale, never silently re-designed. Nothing is exported
for execution: no execution package exists yet.

The revisions up to the one opened are its title block's revision list, with
who approved each. A project with an approved revision cannot be deleted: what
was signed for stays reproducible. Every function binds the session to the
caller's tenant first, so a project of another tenant does not exist as far as
it can tell (ADR 0003), and asking for one is answered as for an id that never
existed.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.design import CATALOGUE_VERSION, RULES_VERSION, drawing_text, integrity, marking, snapshot
from app.design.model import versions
from app.design.model.read import VersionedModel, read_snapshot
from app.domain import billing
from app.domain import design as design_domain
from app.domain.plans import Feature
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    ApproveRevisionRequest,
    DesignFinding,
    ExportPurpose,
    ExportStatus,
    FindingSeverity,
    PricingSettings,
    ProjectDesignRequest,
    ProjectInfo,
    ProjectPage,
    ProjectSummary,
    ReviseProjectRequest,
    Revision,
    RevisionExportRequest,
    RevisionSummary,
    SavedProject,
    SaveProjectRequest,
)
from app.models.tables.design_projects import DesignProjectRow, DesignRevisionRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

#: Revisions one project may hold. A project is revised by hand; a client
#: saving in a loop is stopped here rather than filling the table.
MAX_REVISIONS = 200

#: Projects one tenant may hold.
MAX_PROJECTS = 1000

#: How many hex digits of a snapshot hash an export prints.
SHORT_HASH = 12

_PAGE_DEFAULT = 25
_PAGE_MAX = 100


def _scope_to(session: Session, user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    bind_tenant(session, tenant)
    return tenant


def _release(session: Session) -> None:
    """Roll back after a failure, so no lock outlives it, keeping that failure.

    A rollback that fails too (the connection is gone) is logged rather than
    raised, so the error the caller sees is the one that happened first.
    """
    try:
        session.rollback()
    except Exception:
        logger.exception("design.rollback_failed")


def _load(session: Session, project_id: str, *, lock: bool = False) -> DesignProjectRow:
    """The caller's project; with ``lock``, its row locked (``SELECT ... FOR UPDATE``).

    Locking the project serializes revisions: each save reads and raises
    ``revision_count`` only once the save before it has committed, so
    concurrent saves get distinct numbers rather than colliding.
    """
    try:
        key = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFoundError("no such project", code="project_not_found") from exc
    found = session.get(DesignProjectRow, key, with_for_update=lock, populate_existing=lock)
    if found is None:
        raise NotFoundError("no such project", code="project_not_found")
    return found


@dataclass(frozen=True)
class _Validation:
    """What a revision's stored design says now: its status and why."""

    status: ExportStatus
    findings: list[DesignFinding]
    approval_bound: bool
    #: What changed since it was saved, for a stale revision.
    changed: tuple[str, ...] = ()


def _stored_findings(row: DesignRevisionRow) -> list[DesignFinding]:
    return [DesignFinding.model_validate(found) for found in row.findings or []]


def _snapshot_matches(row: DesignRevisionRow) -> bool:
    """Whether the stored design still hashes to the hash recorded with it.

    A design that cannot be hashed (a float written into it outside this code)
    does not match: it is refused as altered, never answered with a 500.
    """
    try:
        return snapshot.sha256_of(row.design_snapshot) == row.snapshot_sha256
    except ValueError:
        return False


def _approval_bound(row: DesignRevisionRow) -> bool:
    """Whether the revision's approval is of the design it holds."""
    return (
        row.approved_at is not None
        and row.snapshot_sha256 is not None
        and row.approved_snapshot_sha256 == row.snapshot_sha256
    )


def _current_profile_sha256(row: DesignRevisionRow) -> str | None:
    """The hash of the revision's company settings as they resolve now; ``None`` if refused.

    The profile is the one the design was made under: the settings saved in
    the revision's own request. The tenant's company settings do not feed a
    saved design, so changing them does not make a revision stale; a change
    to this software's default profile, which those settings inherit, does.
    """
    try:
        request = ProjectDesignRequest.model_validate(row.request)
        return snapshot.profile_sha256(design_domain.resolve_profile(request.profile))
    except (PydanticValidationError, ValidationError):
        return None


def _classified(findings: Sequence[DesignFinding]) -> list[tuple[str, str, bool]]:
    return sorted(
        (found.code, found.severity.value, found.requires_engineering_confirmation)
        for found in findings
    )


def _policy_changed(row: DesignRevisionRow) -> bool:
    """Whether today's findings policy reads the frozen snapshot differently.

    The policy and integrity checks are re-run on the stored design, which
    recalculates nothing; a reclassification (a warning made blocking) then
    makes the revision stale, rather than leaving its saved findings to say
    what the policy no longer does. A snapshot that no longer reads is left to
    the integrity check, which refuses it with its own code.
    """
    if row.design_snapshot is None or not _snapshot_matches(row):
        return False
    try:
        designed = read_snapshot(row.design_snapshot).project
        request = ProjectDesignRequest.model_validate(row.request)
    except (
        versions.SnapshotSchemaUnknownError,
        versions.SnapshotUnreadableError,
        PydanticValidationError,
    ):
        return False
    conditions = {board.name: board.conditions for board in request.boards}
    current = integrity.project_findings(designed, conditions)
    return _classified(current) != _classified(_stored_findings(row))


def _changed(row: DesignRevisionRow, *, thorough: bool = True) -> tuple[str, ...]:
    """What no longer matches what the revision was designed under.

    ``thorough=False`` compares only the recorded versions, for the list of a
    project's revisions; the revision opened, approved or exported is always
    checked in full, its profile and findings policy included.
    """
    changed: list[str] = []
    if row.rules_version != RULES_VERSION:
        changed.append("rules_version")
    if row.catalogue_version != CATALOGUE_VERSION:
        changed.append("catalogue_version")
    if not thorough:
        return tuple(changed)
    if row.profile_sha256 != _current_profile_sha256(row):
        changed.append("profile")
    if _policy_changed(row):
        changed.append("findings_policy")
    return tuple(changed)


def _request_problem(row: DesignRevisionRow) -> DesignFinding | None:
    """Why the revision's stored request no longer validates; ``None`` if it does.

    An older revision may hold what today's bounds refuse (a 600-character
    load description). It still opens, as stored, but it is not validated:
    it cannot be approved or exported until it is corrected and saved again.
    """
    try:
        ProjectDesignRequest.model_validate(row.request)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return DesignFinding(
            code="request_not_valid",
            severity=FindingSeverity.BLOCKING,
            values={"field": where, "problem": first["type"]},
            message=f"The saved project no longer validates at {where}: {first['msg']}; "
            "correct it and save again",
            requires_engineering_confirmation=False,
            source="policy",
        )
    return None


def _schema_problem(row: DesignRevisionRow) -> DesignFinding | None:
    """Why the revision's snapshot is in a schema this version does not read; ``None`` if not.

    Such a revision still opens, as stored, but it is not validated: it cannot
    be approved or exported (``snapshot_schema_unknown``). A snapshot that is
    not an object at all is left to the integrity check.
    """
    try:
        versions.identify(row.design_snapshot)
    except versions.SnapshotSchemaUnknownError as exc:
        return DesignFinding(
            code="snapshot_schema_unknown",
            severity=FindingSeverity.BLOCKING,
            values={"schema": exc.schema},
            message=f"The stored design is in schema {exc.schema!r}, "
            "which this version of PanelPilot does not read",
            requires_engineering_confirmation=False,
            source="policy",
        )
    except versions.SnapshotUnreadableError:
        return None
    return None


def _integrity_problem(row: DesignRevisionRow) -> DesignFinding | None:
    """Why approval and export would refuse the stored design as altered; ``None`` if not.

    The snapshot is checked as ``_check_snapshot`` checks it, its hash first,
    then read through ``read_snapshot``, so a revision opens as not validated
    exactly when its design would be refused with ``snapshot_integrity_failed``.
    """
    if not _snapshot_matches(row):
        reason = "hash_mismatch"
    else:
        try:
            read_snapshot(row.design_snapshot)
        except versions.SnapshotUnreadableError:
            reason = "unreadable"
        else:
            return None
    return DesignFinding(
        code="snapshot_integrity_failed",
        severity=FindingSeverity.BLOCKING,
        values={"reason": reason},
        message="The stored design does not match its recorded hash or no longer "
        "reads as a design; save it again as a new revision",
        requires_engineering_confirmation=False,
        source="policy",
    )


def _validation(row: DesignRevisionRow, *, thorough: bool = True) -> _Validation:
    findings = _stored_findings(row)
    bound = _approval_bound(row)
    problem = _request_problem(row) if thorough else None
    if problem is not None:
        return _Validation(ExportStatus.NOT_VALIDATED, [*findings, problem], bound)
    if row.design_snapshot is None or row.snapshot_sha256 is None:
        return _Validation(ExportStatus.NOT_VALIDATED, findings, bound)
    unknown = _schema_problem(row)
    if unknown is not None:
        return _Validation(ExportStatus.NOT_VALIDATED, [*findings, unknown], bound)
    unreadable = _integrity_problem(row) if thorough else None
    if unreadable is not None:
        return _Validation(ExportStatus.NOT_VALIDATED, [*findings, unreadable], bound)
    changed = _changed(row, thorough=thorough)
    if changed:
        return _Validation(ExportStatus.STALE, findings, bound, changed)
    if integrity.blocking(findings):
        return _Validation(ExportStatus.BLOCKED, findings, bound)
    if bound:
        return _Validation(ExportStatus.REVIEW_APPROVED, findings, bound)
    return _Validation(ExportStatus.DRAFT, findings, bound)


def _execution_blockers(checked: _Validation) -> list[str]:
    """Why a revision cannot be exported for execution, most fundamental first."""
    blockers = list(marking.EXECUTION_PACKAGE_MISSING)
    if checked.status is ExportStatus.NOT_VALIDATED:
        blockers.append("not_validated")
    if checked.status is ExportStatus.STALE:
        blockers.append("stale")
    if not checked.approval_bound:
        blockers.append("approval")
    blockers.extend(f"finding:{found.code}" for found in integrity.blocking(checked.findings))
    return blockers


#: How an approval names its approver when the account has no display name,
#: is no longer the tenant's, or was not recorded (an approval made before
#: accounts were). Never the name typed at approval, nor an email.
APPROVER_FALLBACK = drawing_text.APPROVER_FALLBACK


def _display_name(full_name: str | None) -> str | None:
    """An account's name as a revision list may show it; ``None`` if it has none.

    A name that holds an ``@`` is refused as well: it may be the email itself,
    and the email is never sent to a client.
    """
    name = (full_name or "").strip()
    return name if name and "@" not in name else None


def _account_id(user: CurrentUser) -> uuid.UUID | None:
    """The caller's account id; ``None`` for a caller with no account row id (a trial)."""
    try:
        return uuid.UUID(user.id)
    except ValueError:
        return None


@dataclass(frozen=True)
class _Names:
    """The display names of the accounts a project's revisions record.

    Read when a response or export is built, from the project's own tenant
    only: an account of another tenant's, or one deleted since, has no name.
    """

    by_email: dict[str, str]
    by_id: dict[uuid.UUID, str]

    def author(self, row: DesignRevisionRow) -> str:
        return self.by_email.get(row.author.lower(), "") if row.author else ""

    def approver(self, row: DesignRevisionRow) -> str | None:
        """The approving account's display name; ``None`` if none resolves.

        An approval that recorded the account's id is resolved by it alone:
        the email it recorded may since belong to another account. One made
        before ids were recorded is resolved by its email, in the tenant. The
        legacy typed name (``approved_by``) is never used.
        """
        if row.approved_at is None:
            return None
        if row.approved_user_id is not None:
            return self.by_id.get(row.approved_user_id)
        if row.approved_account:
            return self.by_email.get(row.approved_account.lower())
        return None

    def printed_approver(self, row: DesignRevisionRow) -> str:
        """The approver as an export prints it: the display name, or the fallback."""
        return self.approver(row) or APPROVER_FALLBACK


def _display_names(
    session: Session,
    tenant: uuid.UUID,
    revisions: Sequence[DesignRevisionRow],
    *,
    also: uuid.UUID | None = None,
) -> _Names:
    """The display names of the accounts the revisions record (and ``also``).

    The rows record an author by email, and an approver by id (or, approved
    before ids were recorded, by email): the audit identity, kept as it was.
    """
    emails = {
        email.lower() for row in revisions for email in (row.author, row.approved_account) if email
    }
    ids = {row.approved_user_id for row in revisions if row.approved_user_id is not None}
    if also is not None:
        ids.add(also)
    if not emails and not ids:
        return _Names(by_email={}, by_id={})
    found = session.execute(
        select(User.id, User.email, User.full_name).where(
            User.tenant_id == tenant,
            or_(func.lower(User.email).in_(emails), User.id.in_(ids)),
        )
    ).all()
    by_email: dict[str, str] = {}
    by_id: dict[uuid.UUID, str] = {}
    for user_id, email, full_name in found:
        name = _display_name(full_name)
        if name is not None:
            by_email[email.lower()] = name
            by_id[user_id] = name
    return _Names(by_email=by_email, by_id=by_id)


def _summary(row: DesignRevisionRow, names: _Names, *, thorough: bool = False) -> RevisionSummary:
    approved = row.approved_at is not None
    return RevisionSummary(
        number=row.number,
        note=row.note,
        author=names.author(row),
        created_at=row.created_at.isoformat(),
        approved_by=names.printed_approver(row) if approved else None,
        approved_at=row.approved_at.isoformat() if row.approved_at else None,
        approver_account_recorded=approved
        and (row.approved_user_id is not None or bool(row.approved_account)),
        approver_name=names.approver(row),
        approval_note=(row.approval_note or None) if approved else None,
        validated=row.design_snapshot is not None,
        # One of up to MAX_REVISIONS rows: compared by recorded versions only.
        # The revision opened is checked in full (``_opened``), and approval and
        # export check theirs in full again under the project lock.
        export_status=_validation(row, thorough=thorough).status,
    )


def _title_revisions(
    revisions: Sequence[DesignRevisionRow],
    upto: int,
    names: _Names,
    *,
    bound_only: bool = False,
) -> list[Revision]:
    """The title block's revision list: each saved revision up to ``upto``.

    Args:
        revisions: The project's revisions, in order.
        upto: The last revision listed.
        names: The accounts' display names, which name the approver.
        bound_only: Name an approver only where the approval is of the design
            the revision holds, as an export prints it.
    """
    listed: list[Revision] = []
    for row in revisions:
        if row.number > upto:
            continue
        shown = row.approved_at is not None and (not bound_only or _approval_bound(row))
        listed.append(
            Revision(
                index=f"{row.number:02d}",
                date=(row.approved_at or row.created_at).date().isoformat(),
                description=row.note,
                approved_by=names.printed_approver(row) if shown else "",
                approval_note=(row.approval_note or "") if shown else "",
            )
        )
    return listed


def _design_refused(exc: ValidationError) -> DesignFinding:
    return DesignFinding(
        code="design_refused",
        severity=FindingSeverity.BLOCKING,
        values={**exc.params, **({"refusal": exc.code} if exc.code else {})},
        message=f"The design was refused: {exc}",
        requires_engineering_confirmation=False,
        source="policy",
    )


def _validate(request: ProjectDesignRequest) -> dict[str, Any]:
    """Design a request on the server: the revision's snapshot columns.

    The snapshot's title block carries no revision rows: who checked and
    approved a revision is read from the database when it is exported, never
    from what the client typed.

    A request the design refuses is still saved, so no entered work is lost,
    with no snapshot and its refusal as a blocking finding: it opens, and
    cannot be approved until a revision of it designs.
    """
    as_entered = request.model_copy(
        update={"info": request.info.model_copy(update={"revisions": []})}
    )
    try:
        designed, company = design_domain.build_design(as_entered)
    except ValidationError as exc:
        return {"findings": [_design_refused(exc).model_dump(mode="json")]}
    conditions = {board.name: board.conditions for board in request.boards}
    findings = integrity.project_findings(designed, conditions)
    stored = designed.model_dump(mode="json")
    return {
        "design_snapshot": stored,
        "snapshot_sha256": snapshot.sha256_of(stored),
        "profile_sha256": snapshot.profile_sha256(company),
        "rules_version": RULES_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "findings": [found.model_dump(mode="json") for found in findings],
    }


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
            **_validate(request),
        )
    )


def _revisions(session: Session, project: DesignProjectRow) -> Sequence[DesignRevisionRow]:
    return session.scalars(
        select(DesignRevisionRow)
        .where(DesignRevisionRow.project_id == project.id)
        .order_by(DesignRevisionRow.number)
    ).all()


def _opened(
    session: Session, project: DesignProjectRow, number: int | None, viewer: CurrentUser
) -> SavedProject:
    revisions = _revisions(session, project)
    chosen = (
        revisions[-1]
        if number is None
        else next((r for r in revisions if r.number == number), None)
    )
    if chosen is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    viewer_id = _account_id(viewer)
    names = _display_names(session, project.tenant_id, revisions, also=viewer_id)
    title = _title_revisions(revisions, chosen.number, names)
    checked = _validation(chosen)
    readable = _request_problem(chosen) is None
    if readable:
        request = ProjectDesignRequest.model_validate(chosen.request)
        request = request.model_copy(
            update={"info": request.info.model_copy(update={"revisions": title})}
        )
    else:
        # Sent back as stored (``SavedProject.as_stored``), with the title
        # block's revisions from the database as for any other revision.
        stored: dict[str, Any] = dict(chosen.request)
        info = stored.get("info")
        stored["info"] = {
            **(info if isinstance(info, dict) else {}),
            "revisions": [listed.model_dump(mode="json") for listed in title],
        }
        request = ProjectDesignRequest.model_construct(**stored)
    opened = SavedProject(
        id=str(project.id),
        name=project.name,
        revisions=[_summary(r, names, thorough=r is chosen) for r in revisions],
        revision=chosen.number,
        request=request,
        findings=checked.findings,
        validated=readable and chosen.design_snapshot is not None,
        snapshot_sha256=chosen.snapshot_sha256,
        rules_version=chosen.rules_version,
        approved_snapshot_sha256=chosen.approved_snapshot_sha256,
        export_status=checked.status,
        execution_blockers=_execution_blockers(checked),
        approving_as=names.by_id.get(viewer_id) if viewer_id is not None else None,
    )
    return opened if readable else opened.as_stored(stored)


def save_project(
    *, session: Session, user: CurrentUser, request: SaveProjectRequest
) -> SavedProject:
    """Save a new project as its first revision, designed on the server.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        request: Its name, the project as entered, and a note.

    Returns:
        The project as saved, with what the checks found.

    Raises:
        ValidationError: If the tenant already holds the most projects
            allowed, or the company settings are unknown or malformed.
        AuthorizationError: If its plan keeps no more projects.
    """
    tenant = _scope_to(session, user)
    # Malformed company settings are the request's error, refused as such,
    # not saved as a design the server could not make.
    design_domain.resolve_profile(request.request.profile)
    billing.require_project_room(session=session, user=user)
    try:
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
    except BaseException:
        # Whatever failed, nothing half-saved is kept and no lock outlives it.
        _release(session)
        raise
    logger.info("design.project_saved", tenant_id=str(tenant), project_id=str(project.id))
    return _opened(session, project, None, user)


def revise_project(
    *, session: Session, user: CurrentUser, project_id: str, request: ReviseProjectRequest
) -> SavedProject:
    """Save a new revision of a project, designed on the server.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        project_id: The project.
        request: The project as entered now, and what changed.

    Returns:
        The project, at its new revision, with what the checks found.

    Raises:
        NotFoundError: If no such project is the caller's.
        ValidationError: If it holds the most revisions allowed, or its
            company settings are unknown or malformed.
    """
    tenant = _scope_to(session, user)
    try:
        project = _load(session, project_id, lock=True)
        # Malformed company settings are refused, not saved as a refused design.
        design_domain.resolve_profile(request.request.profile)
        # Raising the revision count updates the row, which moves its
        # ``updated_at`` (the list's order) to now.
        _add_revision(session, project, tenant, user, request.request, request.note)
        # The number this save took: by the time it is opened, a concurrent
        # save may have added a later one.
        number = project.revision_count
        session.commit()
    except IntegrityError as exc:
        _release(session)
        raise ConflictError(
            "another revision was saved at the same time; save again",
            code="revision_conflict",
        ) from exc
    except BaseException:
        # Any failure, a domain refusal or not, releases the project lock now
        # rather than when the session is closed.
        _release(session)
        raise
    logger.info(
        "design.project_revised",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
    )
    return _opened(session, project, number, user)


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
        The project, its revisions, and that revision as entered, with its
        findings, snapshot hash, rules version, approval binding and export
        status.

    Raises:
        NotFoundError: If no such project or revision is the caller's.
    """
    _scope_to(session, user)
    # Asked for a revision, the answer is about that revision, as approve and
    # export answer: a project that is not the caller's is revision_not_found.
    project = (
        _load(session, project_id) if revision is None else _revision_project(session, project_id)
    )
    return _opened(session, project, revision, user)


def _revision_project(session: Session, project_id: str, *, lock: bool = False) -> DesignProjectRow:
    """The project a revision endpoint names; a missing one is ``revision_not_found``.

    Approve and export answer for a revision, so a project that is not the
    caller's (another tenant's, or none) is refused as its revision would be.
    """
    try:
        return _load(session, project_id, lock=lock)
    except NotFoundError as exc:
        raise NotFoundError("no such revision", code="revision_not_found") from exc


def _locked_revision(session: Session, project: DesignProjectRow, number: int) -> DesignRevisionRow:
    """The revision row, locked until the transaction ends (``SELECT … FOR UPDATE``)."""
    row = session.scalars(
        select(DesignRevisionRow)
        .where(DesignRevisionRow.project_id == project.id, DesignRevisionRow.number == number)
        .with_for_update()
        # Read what the lock protects, not what this session saw earlier.
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    return row


def _check_snapshot(row: DesignRevisionRow, number: int) -> VersionedModel:
    """The revision's stored design, refused unless it is there, unaltered and readable.

    The hash is checked over the snapshot as stored; only then is it read,
    through ``read_snapshot``, which refuses a schema this version does not
    read (``snapshot_schema_unknown``) and content that does not validate.
    """
    if row.design_snapshot is None or row.snapshot_sha256 is None:
        raise ConflictError(
            f"revision {number} was saved before designs were validated on save; "
            "save it again as a new revision",
            code="revision_not_validated",
            params={"revision": number},
        )
    problem = _request_problem(row)
    if problem is not None:
        raise ConflictError(
            f"revision {number}'s saved project no longer validates "
            f"({problem.values['field']}); correct it and save it again as a new revision",
            code="revision_not_validated",
            params={"revision": number, "reason": "request_not_valid", **problem.values},
        )
    if not _snapshot_matches(row):
        raise ConflictError(
            f"revision {number}'s stored design does not match its recorded hash",
            code="snapshot_integrity_failed",
            params={"revision": number},
        )
    try:
        return read_snapshot(row.design_snapshot)
    except versions.SnapshotUnreadableError as exc:
        raise ConflictError(
            f"revision {number}'s stored design no longer reads as a design",
            code="snapshot_integrity_failed",
            params={"revision": number},
        ) from exc


def _refuse_stale(row: DesignRevisionRow, number: int) -> None:
    changed = _changed(row)
    if changed:
        raise ConflictError(
            f"revision {number} was designed under other rules or settings "
            f"({', '.join(changed)} changed); save it again as a new revision",
            code="revision_stale",
            params={
                "revision": number,
                "changed": ",".join(changed),
                "saved_rules_version": row.rules_version or "",
                "current_rules_version": RULES_VERSION,
            },
        )


def approve_revision(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    request: ApproveRevisionRequest,
) -> SavedProject:
    """Approve one revision of a project, once, bound to the design it holds.

    The project row, then the revision row, is locked while it is checked
    and approved, so two approvals cannot both pass the check (nor a delete
    remove it meanwhile), and the approval records the
    snapshot hash it was given for.

    Args:
        session: Open database session.
        user: The authenticated caller; recorded as the approving account.
        project_id: The project.
        number: The revision.
        request: An optional note to the approval. It names no one: the
            approver is ``user``, shown by its display name.

    Returns:
        The project, opened at that revision.

    Raises:
        NotFoundError: If no such project or revision is the caller's.
        ConflictError: If the revision is approved already (``revision_already_approved``).
        ConflictError: If it holds no validated design
            (``revision_not_validated``), its design was altered
            (``snapshot_integrity_failed``), is in a schema this version does not
            read (``snapshot_schema_unknown``), its rules or settings changed
            (``revision_stale``), or it has blocking findings
            (``revision_has_blocking_findings``).
        AuthorizationError: If the caller is not a signed-in account
            (``approval_requires_account``: a trial has none), or their plan
            does not include approval.
    """
    _require_account(user)
    billing.require_feature(session=session, user=user, feature=Feature.APPROVAL_WORKFLOW)
    tenant = _scope_to(session, user)
    try:
        # The project row first, then the revision's, in the order a save and
        # a delete take them: a delete cannot remove a revision as it is
        # approved, and neither can wait on the other's second lock.
        project = _revision_project(session, project_id, lock=True)
        row = _locked_revision(session, project, number)
        _approvable(session, project, row, number)
        # Who approves is the signed-in account, from its token: the request
        # names no one. ``approved_by`` (the legacy typed name) stays NULL.
        row.approved_account = user.email
        row.approved_user_id = _account_id(user)
        row.approval_note = request.note or None
        row.approved_at = datetime.now(UTC)
        row.approved_snapshot_sha256 = row.snapshot_sha256
        session.commit()
    except BaseException:
        # Release the locks now, whatever failed, not when the session is closed.
        _release(session)
        raise
    logger.info(
        "design.revision_approved",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        snapshot_sha256=row.snapshot_sha256,
    )
    return _opened(session, project, number, user)


def _require_account(user: CurrentUser) -> None:
    """Refuse an approval by a caller with no account: it would name no one.

    A trial token resolves to an engineer with no email and no account row,
    so an approval it made could identify nobody.
    """
    if not user.email or _account_id(user) is None:
        raise AuthorizationError(
            "approving a revision requires a signed-in account",
            code="approval_requires_account",
        )


def _approvable(
    session: Session, project: DesignProjectRow, row: DesignRevisionRow, number: int
) -> None:
    """Refuse approving a revision that is approved, unvalidated, stale or blocked."""
    if row.approved_at is not None:
        approver = _display_names(session, project.tenant_id, [row]).printed_approver(row)
        raise ConflictError(
            f"revision {number} is approved already, by {approver}",
            code="revision_already_approved",
            params={"revision": str(number), "approver": approver},
        )
    _check_snapshot(row, number)
    _refuse_stale(row, number)
    blocking = integrity.blocking(_stored_findings(row))
    if blocking:
        raise ConflictError(
            f"revision {number} has {len(blocking)} blocking finding(s); "
            "fix them and save a new revision",
            code="revision_has_blocking_findings",
            params={
                "revision": number,
                "count": len(blocking),
                "codes": ",".join(sorted({found.code for found in blocking})),
            },
        )


def _revision_mark(
    project: DesignProjectRow,
    row: DesignRevisionRow,
    findings: list[DesignFinding],
    names: _Names,
) -> marking.ExportMark:
    digest = (row.snapshot_sha256 or "")[:SHORT_HASH]
    where = f"{project.name}, revision {row.number:02d}"
    if _approval_bound(row) and row.approved_at is not None:
        approver = names.printed_approver(row)
        identity = (
            f"{where}, approved by {approver} on {row.approved_at.date().isoformat()}, "
            f"design {digest}, rules {row.rules_version}"
        )
        label = marking.REVIEW_APPROVED_LABEL
    else:
        identity = f"{where}, not approved, design {digest}, rules {row.rules_version}"
        label = marking.DRAFT_LABEL
    return marking.ExportMark(label=label, identity=identity, findings=tuple(findings))


def export_revision(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    request: RevisionExportRequest,
) -> design_domain.ExportedFile:
    """Export a saved revision from the design the server stored for it.

    Nothing the client sends but the format and pricing is used: the design is
    the revision's snapshot, its hash checked again; the approval, approver
    and date are the database's, and count only for the design approved.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.
        number: The revision.
        request: The format, pricing, and purpose.

    Returns:
        The file, labelled draft or review-approved, with its findings.

    Raises:
        NotFoundError: If no such project or revision is the caller's
            (``revision_not_found``).
        ConflictError: ``revision_not_validated``, ``snapshot_integrity_failed``,
            ``snapshot_schema_unknown``,
            ``revision_stale``, ``export_format_unprotected`` for a format
            that cannot keep a mark once imported, or, for execution, always
            ``execution_package_incomplete``.
        ValidationError: If a quotation lacks pricing or a control program
            has no PLC-switched circuit.
        AuthorizationError: If the caller's plan does not include the format.
    """
    tenant = _scope_to(session, user)
    project = _revision_project(session, project_id)
    revisions = _revisions(session, project)
    row = next((r for r in revisions if r.number == number), None)
    if row is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    findings = _stored_findings(row)
    if request.purpose is ExportPurpose.EXECUTION:
        # Refused before anything else is checked: whatever state the revision
        # is in, no execution package exists, and the refusal says all of why.
        missing = _execution_blockers(_validation(row))
        if (
            row.design_snapshot is not None
            and row.snapshot_sha256 is not None
            and not _snapshot_matches(row)
        ):
            missing.append("snapshot_integrity_failed")
        raise ConflictError(
            "an execution package cannot be exported yet: "
            + ", ".join(marking.EXECUTION_PACKAGE_MISSING)
            + " are not produced",
            code="execution_package_incomplete",
            params={
                "revision": number,
                "missing": ",".join(marking.EXECUTION_PACKAGE_MISSING),
                "approval": "present" if _approval_bound(row) else "missing",
                "blocking_findings": ",".join(
                    sorted({found.code for found in integrity.blocking(findings)})
                ),
                "blockers": ",".join(missing),
            },
        )
    design_domain.refuse_unprotected(request.format)
    designed = _check_snapshot(row, number).project
    _refuse_stale(row, number)
    design_domain.require_format(session=session, user=user, export_format=request.format)
    company = design_domain.resolve_profile(
        ProjectDesignRequest.model_validate(row.request).profile
    )
    names = _display_names(session, project.tenant_id, revisions)
    info: ProjectInfo = designed.info.model_copy(
        update={"revisions": _title_revisions(revisions, number, names, bound_only=True)}
    )
    exported = design_domain.render_export(
        designated=designed.model_copy(update={"info": info}),
        company=company,
        export_format=request.format,
        pricing=_revision_pricing(request.pricing),
        mark=_revision_mark(project, row, findings, names),
    )
    logger.info(
        "design.revision_exported",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        format=request.format.value,
        approved=_approval_bound(row),
        snapshot_sha256=row.snapshot_sha256,
    )
    return exported


def _revision_pricing(pricing: PricingSettings | None) -> PricingSettings | None:
    """The pricing a revision's quotation applies: prices and rates only.

    The client's pricing sets what an item costs, never what is quoted: the
    devices, cables and quantities are the snapshot's. A default cable length
    would turn a cable the revision gives no length into metres it never
    designed, so it is dropped and such a cable stays unpriced, "no length".
    """
    if pricing is None:
        return None
    return pricing.model_copy(update={"cable_length_m": None})


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
        ValidationError: If a revision of it is approved.
    """
    tenant = _scope_to(session, user)
    try:
        # Locked as an approval locks it, first: an approval in progress
        # finishes before this reads whether any revision is approved, and one
        # that starts after waits, then finds no project.
        project = _load(session, project_id, lock=True)
        approved = session.scalars(
            select(DesignRevisionRow.number).where(
                DesignRevisionRow.project_id == project.id,
                DesignRevisionRow.approved_at.is_not(None),
            )
        ).first()
        if approved is not None:
            raise ValidationError(
                "a project with an approved revision cannot be deleted",
                code="project_approved",
                params={"revision": str(approved)},
            )
        session.delete(project)
        session.commit()
    except BaseException:
        _release(session)
        raise
    logger.info("design.project_deleted", tenant_id=str(tenant), project_id=project_id)
