"""Server-backed design drafts (slice 1): private, explicitly saved unsaved work.

Guarantees, for every operation:

* **Private.** The tenant is bound and the caller's account must be the
  draft's author. Anyone else (another member, the owner, an admin, another
  tenant) gets 404. A trial has no account and gets no server drafts.
* **Never a revision.** A draft is structurally checked inputs only. It is
  never engineering-validated, approved or exported, and no revision,
  snapshot, hash or approval is read or written here. Publishing goes
  through the normal save.
* **No lost update.** Every write names the token it was made against
  (``if_match``; ``None`` for "no draft"). The project row is locked for the
  write, so concurrent writes, first writes included, apply one at a time,
  and a write whose token is not the current one is refused (409
  ``draft_conflict``) without changing anything.
* **No ABA.** One row per (tenant, project, author) for its whole life:
  deleting leaves a tombstone and every write raises ``version``, so the
  token ``"<id>.<version>"`` is never reused.
* **Idempotent writes.** A request id is remembered per (tenant, author) with
  what was asked and how it was answered. A retry gets that answer again and
  is never applied again, even after later writes. The same id with other
  input is refused (422 ``request_id_reused``). A request first sent more
  than 24 hours ago is refused (409 ``request_expired``); ids are kept for
  ``DESIGN_DRAFT_REQUEST_RETENTION_HOURS``.
* **Private contents.** A draft body is never logged and never put in an
  error message or a request record.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import design_server_drafts, get_settings
from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design_drafts import (
    DRAFT_BODY_MAX_BYTES,
    DraftBody,
    DraftDelete,
    DraftList,
    DraftLookup,
    DraftState,
    DraftWrite,
)
from app.models.tables.design_drafts import DesignDraftRequestRow, DesignDraftRow
from app.models.tables.design_projects import DesignProjectRow

#: A request is accepted this long after the client first sent it.
REQUEST_VALID_FOR = timedelta(hours=24)
#: How far ahead of the server's clock a request's ``issued_at`` may be.
CLOCK_SKEW = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(UTC)


def _author(session: Session, user: CurrentUser) -> tuple[uuid.UUID, uuid.UUID]:
    """Bind the caller's tenant; (tenant, account id). A trial has no account id."""
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    try:
        author = uuid.UUID(user.id)
    except ValueError as exc:
        raise AuthorizationError(
            "server drafts need a signed-in account", code="server_drafts_need_account"
        ) from exc
    bind_tenant(session, tenant)
    return tenant, author


def _project(session: Session, project_id: str, *, lock: bool = False) -> DesignProjectRow:
    try:
        key = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFoundError("no such project", code="project_not_found") from exc
    found = session.get(DesignProjectRow, key, with_for_update=lock, populate_existing=lock)
    if found is None:
        raise NotFoundError("no such project", code="project_not_found")
    return found


def _row(session: Session, project: uuid.UUID, author: uuid.UUID) -> DesignDraftRow | None:
    return session.scalars(
        select(DesignDraftRow).where(
            DesignDraftRow.project_id == project, DesignDraftRow.author_user_id == author
        )
    ).one_or_none()


def token_of(row: DesignDraftRow) -> str:
    """The concurrency token of a draft row as it stands."""
    return f"{row.id}.{row.version}"


def _state(row: DesignDraftRow, *, with_body: bool, replayed: bool = False) -> DraftState:
    body = None
    if with_body and row.state == "live" and row.body is not None:
        body = DraftBody.model_validate(row.body)
    return DraftState(
        project_id=row.project_id,
        token=token_of(row),
        state="live" if row.state == "live" else "deleted",
        base_revision=row.base_revision,
        updated_at=row.updated_at,
        body=body,
        replayed=replayed,
    )


def _conflict(row: DesignDraftRow | None) -> ConflictError:
    """409 naming what the server holds now (never its body)."""
    params = (
        {"state": "none"}
        if row is None
        else {
            "state": row.state,
            "token": token_of(row),
            "base_revision": str(row.base_revision),
            "updated_at": row.updated_at.isoformat(),
        }
    )
    return ConflictError(
        "the server holds another version of this draft; nothing was changed",
        code="draft_conflict",
        params=params,
    )


def _check_issued(issued_at: datetime, now: datetime) -> None:
    stamped = issued_at if issued_at.tzinfo is not None else issued_at.replace(tzinfo=UTC)
    if now - stamped > REQUEST_VALID_FOR or stamped - now > CLOCK_SKEW:
        raise ConflictError(
            "this request is too old to apply; read the draft again and send a new request",
            code="request_expired",
        )


def _fingerprint(op: str, project: uuid.UUID, fields: dict[str, Any]) -> str:
    text = json.dumps({"op": op, "project": str(project), **fields}, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _replay(
    session: Session, author: uuid.UUID, request_id: uuid.UUID, fingerprint: str
) -> DraftState | None:
    """The recorded answer of an earlier request with this id, if any."""
    found = session.scalars(
        select(DesignDraftRequestRow).where(
            DesignDraftRequestRow.author_user_id == author,
            DesignDraftRequestRow.request_id == request_id,
        )
    ).one_or_none()
    if found is None:
        return None
    if found.fingerprint != fingerprint:
        raise ValidationError(
            "this request id was already used for another request",
            code="request_id_reused",
        )
    outcome = found.outcome
    if outcome.get("status") == "conflict":
        raise ConflictError(
            "the server holds another version of this draft; nothing was changed",
            code="draft_conflict",
            params=dict(outcome.get("params", {})),
        )
    state = DraftState.model_validate(outcome["state"])
    return state.model_copy(update={"replayed": True})


def _record(
    session: Session,
    *,
    tenant: uuid.UUID,
    author: uuid.UUID,
    request_id: uuid.UUID,
    project: uuid.UUID,
    fingerprint: str,
    outcome: dict[str, Any],
) -> None:
    session.add(
        DesignDraftRequestRow(
            tenant_id=tenant,
            author_user_id=author,
            request_id=request_id,
            project_id=project,
            fingerprint=fingerprint,
            outcome=outcome,
        )
    )


def _require_writes() -> None:
    if not design_server_drafts():
        raise AuthorizationError(
            "server drafts are turned off for this deployment", code="server_drafts_disabled"
        )


def get_draft(*, session: Session, user: CurrentUser, project_id: str) -> DraftLookup:
    """The caller's own draft of a saved project (or its tombstone), with its body."""
    _tenant, author = _author(session, user)
    project = _project(session, project_id)
    row = _row(session, project.id, author)
    return DraftLookup(
        writes_enabled=design_server_drafts(),
        draft=None if row is None else _state(row, with_body=True),
    )


def list_drafts(*, session: Session, user: CurrentUser) -> DraftList:
    """The caller's own live drafts, without bodies, most recent first."""
    _tenant, author = _author(session, user)
    rows = session.scalars(
        select(DesignDraftRow)
        .where(DesignDraftRow.author_user_id == author, DesignDraftRow.state == "live")
        .order_by(DesignDraftRow.updated_at.desc())
    ).all()
    return DraftList(
        writes_enabled=design_server_drafts(),
        drafts=[_state(row, with_body=False) for row in rows],
    )


def put_draft(
    *, session: Session, user: CurrentUser, project_id: str, write: DraftWrite
) -> DraftState:
    """Keep the caller's draft of a saved project, if ``if_match`` is current.

    Raises:
        AuthorizationError: Writes are off, or the caller has no account.
        NotFoundError: No such project for this tenant.
        ConflictError: ``draft_conflict`` (another version is current; nothing
            changed) or ``request_expired``.
        ValidationError: ``request_id_reused`` or ``draft_too_large``.
    """
    _require_writes()
    tenant, author = _author(session, user)
    now = _now()
    _check_issued(write.issued_at, now)
    body = write.body.model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(body, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode()) > DRAFT_BODY_MAX_BYTES:
        raise ValidationError(
            f"a draft holds at most {DRAFT_BODY_MAX_BYTES} bytes",
            code="draft_too_large",
            params={"max_bytes": str(DRAFT_BODY_MAX_BYTES)},
        )
    body_sha = hashlib.sha256(encoded.encode()).hexdigest()
    try:
        project = _project(session, project_id, lock=True)
        fingerprint = _fingerprint(
            "put",
            project.id,
            {
                "if_match": write.if_match,
                "base_revision": write.base_revision,
                "body_sha256": body_sha,
                "issued_at": write.issued_at.isoformat(),
            },
        )
        replayed = _replay(session, author, write.request_id, fingerprint)
        if replayed is not None:
            session.rollback()
            return replayed
        row = _row(session, project.id, author)
        current = None if row is None else token_of(row)
        if write.if_match != current:
            error = _conflict(row)
            _record(
                session,
                tenant=tenant,
                author=author,
                request_id=write.request_id,
                project=project.id,
                fingerprint=fingerprint,
                outcome={"status": "conflict", "params": error.params},
            )
            session.commit()
            raise error
        if row is None:
            row = DesignDraftRow(
                id=uuid.uuid4(),
                tenant_id=tenant,
                project_id=project.id,
                author_user_id=author,
                state="live",
                version=1,
                base_revision=write.base_revision,
                body=body,
                body_sha256=body_sha,
            )
            session.add(row)
        else:
            row.version += 1
            row.state = "live"
            row.base_revision = write.base_revision
            row.body = body
            row.body_sha256 = body_sha
            row.updated_at = now
        session.flush()
        answer = _state(row, with_body=False)
        _record(
            session,
            tenant=tenant,
            author=author,
            request_id=write.request_id,
            project=project.id,
            fingerprint=fingerprint,
            outcome={"status": "ok", "state": answer.model_dump(mode="json")},
        )
        session.commit()
        return answer
    except IntegrityError as exc:
        # A first write racing another past the lock (the unique constraint is
        # the backstop): the other one won; this one changes nothing.
        session.rollback()
        raise _conflict(_row(session, uuid.UUID(project_id), author)) from exc
    except Exception:
        session.rollback()
        raise


def delete_draft(
    *, session: Session, user: CurrentUser, project_id: str, request: DraftDelete
) -> DraftState:
    """Delete the caller's draft, if ``if_match`` is its current token.

    The row stays as a tombstone (no body) with a new token, so a delayed
    write made against the old token, or one that expected no draft, is
    refused instead of bringing it back. Deletion affects the live database;
    copies in backups are outside this service's control.

    Writes being off does not stop a delete.
    """
    tenant, author = _author(session, user)
    now = _now()
    _check_issued(request.issued_at, now)
    try:
        project = _project(session, project_id, lock=True)
        fingerprint = _fingerprint(
            "delete",
            project.id,
            {"if_match": request.if_match, "issued_at": request.issued_at.isoformat()},
        )
        replayed = _replay(session, author, request.request_id, fingerprint)
        if replayed is not None:
            session.rollback()
            return replayed
        row = _row(session, project.id, author)
        if row is None or row.state != "live" or token_of(row) != request.if_match:
            error = _conflict(row)
            _record(
                session,
                tenant=tenant,
                author=author,
                request_id=request.request_id,
                project=project.id,
                fingerprint=fingerprint,
                outcome={"status": "conflict", "params": error.params},
            )
            session.commit()
            raise error
        row.version += 1
        row.state = "deleted"
        row.body = None
        row.body_sha256 = None
        row.updated_at = now
        session.flush()
        answer = _state(row, with_body=False)
        _record(
            session,
            tenant=tenant,
            author=author,
            request_id=request.request_id,
            project=project.id,
            fingerprint=fingerprint,
            outcome={"status": "ok", "state": answer.model_dump(mode="json")},
        )
        session.commit()
        return answer
    except Exception:
        session.rollback()
        raise


def purge_expired(
    *,
    session: Session,
    now: datetime | None = None,
    draft_days: int | None = None,
    request_hours: int | None = None,
) -> tuple[int, int]:
    """Delete drafts and tombstones not updated within the retention, and old request ids.

    Spans tenants: the caller (a system job) lifts the tenant filter and
    commits. A write in progress holds the draft row; the delete waits for it
    and re-checks ``updated_at``, so a draft written meanwhile is kept.
    Deletes from the live database only.

    Returns:
        (drafts deleted, request records deleted).
    """
    settings = get_settings()
    moment = now or _now()
    draft_cutoff = moment - timedelta(days=draft_days or settings.design_draft_retention_days)
    request_cutoff = moment - timedelta(
        hours=request_hours or settings.design_draft_request_retention_hours
    )
    drafts = session.execute(
        delete(DesignDraftRow)
        .where(DesignDraftRow.updated_at < draft_cutoff)
        .execution_options(synchronize_session=False)
    )
    requests = session.execute(
        delete(DesignDraftRequestRow)
        .where(DesignDraftRequestRow.created_at < request_cutoff)
        .execution_options(synchronize_session=False)
    )
    return (
        int(getattr(drafts, "rowcount", 0) or 0),
        int(getattr(requests, "rowcount", 0) or 0),
    )
