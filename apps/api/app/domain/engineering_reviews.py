"""Engineering reviewers and their review decisions, owned by the server (M3).

**Who.** The tenant's recorded owner designates an account as a qualified
engineering reviewer, recording the owner's statement of that engineer's
qualification verbatim (``designate``); the server neither verifies nor
invents it. A designation grants ``review_engineering_definitions``, which no
role grants and the owner and admins do not hold implicitly
(``app.domain.permissions``). The owner revokes it (``revoke``). Grants are
rows, never changed: the newest for an account says whether it holds one.

**What.** A designated reviewer records a decision (``approved``,
``rejected`` or ``withdrawn``) on a template, connection pattern or rule,
bound to its exact digest, in their tenant (``record_decision``). Global
decisions have no web path: an operator records them with the worker's
``record-global-review`` (``record_global_decision``). Every decision is
append-only; the newest for a scope, kind, ref and digest supersedes the
older ones, which stay.

**Effect.** ``anchor_for`` gives the decisions that apply in a tenant, which
``app.design.model.anchoring`` uses to anchor a stored design's review
statuses. A tenant decision counts only while its reviewer still holds a
designation and an active account: a revocation or deactivation stops their
decisions counting (they stay recorded, and ``decisions`` shows them as
such). No decision makes anything ready for execution.

**Scopes.** A global decision is for a ``global/`` ref only, so it never
reaches a tenant's own definition that happens to share a ref; the
``synthetic/`` rule scope (tests only) is refused for every recorded
decision.

**Independence.** A reviewer may not decide on a tenant definition they
authored. The server knows a tenant template's or pattern's authors as the
accounts that saved a revision of the tenant holding that exact definition
(ref and digest): such a reviewer is refused (``engineering_review_self_review``).
Where no author is known (a global definition, a rule, a definition in no
saved revision, and every global decision) the decision is recorded with
``independence_checked`` false and says so; nothing there implies an
independent review. Whether an owner may review at all is open (O-10).

Every function scoped to a caller binds the session to its tenant first
(ADR 0003); an account of another tenant is "not found".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.tenancy import bind_tenant
from app.design.model import anchoring, codec_v2, versions
from app.design.model.read import read_snapshot
from app.design.rules import evaluation, registry
from app.domain import permissions, team
from app.models.schemas.auth import CurrentUser, Permission
from app.models.schemas.engineering_reviews import (
    DecisionOut,
    DecisionRequest,
    ReviewerOut,
    ReviewerStanding,
)
from app.models.tables.design_projects import DesignRevisionRow
from app.models.tables.engineering_reviews import (
    EngineeringReviewDecisionRow,
    EngineeringReviewerGrantRow,
    GlobalEngineeringReviewDecisionRow,
)
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

SUBJECT_KINDS = ("template", "pattern", "rule")

#: The longest standard reference accepted, and how many.
MAX_STANDARD = 200
MAX_STANDARDS = 20
MAX_TEXT = 2000


def _tenant(user: CurrentUser) -> uuid.UUID:
    try:
        return uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc


def _account(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError("no such member") from exc


def check_subject(kind: str, ref: str, *, scope: str | None = None) -> None:
    """Refuse a subject that is not a well-formed ref of its kind.

    Templates and patterns use M2's ref form (``global|tenant/<id>/v<n>``),
    rules the registry's (``<scope>/<id>/v<n>``); neither holds ``@``.

    Args:
        kind: ``template``, ``pattern`` or ``rule``.
        ref: The ref.
        scope: For a decision being recorded, ``tenant`` or ``global``: the
            ``synthetic/`` scope is refused (it is for tests only), and a
            global decision takes a ``global/`` ref only. ``None`` (a
            listing) checks the form alone.

    Raises:
        ValidationError: ``review_subject_invalid``.
    """
    pattern = registry.RULE_REF_PATTERN if kind == "rule" else codec_v2.REF_PATTERN
    if kind not in SUBJECT_KINDS or "@" in ref or not pattern.fullmatch(ref):
        raise ValidationError(
            f"{ref!r} is not a {kind} ref", code="review_subject_invalid", params={"kind": kind}
        )
    if scope is None:
        return
    if ref.startswith(f"{registry.SYNTHETIC_SCOPE}/"):
        raise ValidationError(
            "synthetic rules are for tests only; no decision is recorded on one",
            code="review_subject_invalid",
            params={"kind": kind, "reason": "synthetic_scope"},
        )
    if scope == "global" and not ref.startswith("global/"):
        raise ValidationError(
            "a global decision is for a global/ ref only",
            code="review_subject_invalid",
            params={"kind": kind, "reason": "not_global"},
        )


def _plain(value: str, field: str, limit: int = MAX_TEXT) -> str:
    if not value.strip() or len(value) > limit:
        raise ValidationError(
            f"{field} is blank or too long", code="review_field_invalid", params={"field": field}
        )
    return value


def _standards(given: Iterable[str]) -> list[str]:
    items = list(given)
    if len(items) > MAX_STANDARDS:
        raise ValidationError(
            "too many standards", code="review_field_invalid", params={"field": "standards"}
        )
    for item in items:
        _plain(item, "standards", MAX_STANDARD)
    if len(set(items)) != len(items):
        raise ValidationError(
            "a standard is named twice", code="review_field_invalid", params={"field": "standards"}
        )
    return sorted(items)


# --------------------------------------------------------------------------- #
# Designations
# --------------------------------------------------------------------------- #


def _latest_grant(session: Session, account: uuid.UUID) -> EngineeringReviewerGrantRow | None:
    return session.scalars(
        select(EngineeringReviewerGrantRow)
        .where(EngineeringReviewerGrantRow.user_id == account)
        .order_by(EngineeringReviewerGrantRow.seq.desc())
        .limit(1)
    ).first()


def _current_grant(session: Session, account: uuid.UUID) -> EngineeringReviewerGrantRow | None:
    found = _latest_grant(session, account)
    return found if found is not None and found.action == "grant" else None


def is_designated(*, session: Session, user: CurrentUser) -> bool:
    """Whether the caller holds a current grant from the owner.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller.

    Returns:
        ``True`` when its newest grant row is a grant.
    """
    try:
        account = uuid.UUID(user.id)
    except ValueError:
        return False
    bind_tenant(session, _tenant(user))
    return _current_grant(session, account) is not None


def _require_owner(session: Session, user: CurrentUser) -> uuid.UUID:
    found = team.standing(session=session, user=user, lock="share")
    if found is None or not found.owner:
        raise AuthorizationError(
            "only the account's owner designates engineering reviewers",
            code="reviewer_designation_owner_only",
        )
    return _tenant(user)


def _member(session: Session, tenant: uuid.UUID, member_id: str) -> User:
    """An active account of the tenant, locked ``FOR UPDATE``; else not found."""
    row = session.scalars(
        select(User)
        .where(User.id == _account(member_id), User.tenant_id == tenant, User.is_active.is_(True))
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("no such member")
    return row


def _reviewer_out(row: EngineeringReviewerGrantRow, name: str | None) -> ReviewerOut:
    return ReviewerOut(
        member_id=str(row.user_id),
        full_name=name,
        qualification_statement=row.qualification_statement or "",
        granted_by=str(row.granted_by_user_id),
        granted_at=row.created_at,
    )


def designate(
    *, session: Session, user: CurrentUser, member_id: str, qualification_statement: str
) -> ReviewerOut:
    """The owner designates an account as an engineering reviewer.

    Args:
        session: Open database session; bound to the caller's tenant here.
        user: The caller; must be the tenant's recorded owner.
        member_id: An active account of the tenant (the owner's own included).
        qualification_statement: The owner's statement, recorded verbatim.

    Returns:
        The designation.

    Raises:
        AuthorizationError: ``reviewer_designation_owner_only``.
        NotFoundError: For an account not active in the tenant.
        ValidationError: ``review_field_invalid``; ``reviewer_already_designated``.
    """
    tenant = _require_owner(session, user)
    statement = _plain(qualification_statement, "qualification_statement")
    member = _member(session, tenant, member_id)
    if _current_grant(session, member.id) is not None:
        raise ValidationError(
            "that account is designated already; revoke it first to change the statement",
            code="reviewer_already_designated",
        )
    row = EngineeringReviewerGrantRow(
        tenant_id=tenant,
        user_id=member.id,
        action="grant",
        qualification_statement=statement,
        granted_by_user_id=uuid.UUID(user.id),
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    logger.info("engineering_reviews.designated", tenant_id=str(tenant), member_id=str(member.id))
    return _reviewer_out(row, member.full_name)


def revoke(*, session: Session, user: CurrentUser, member_id: str) -> None:
    """The owner revokes a designation; decisions already recorded stay.

    Raises:
        AuthorizationError: ``reviewer_designation_owner_only``.
        NotFoundError: For an account not in the tenant, or not designated.
    """
    tenant = _require_owner(session, user)
    account = _account(member_id)
    member = session.scalars(
        select(User).where(User.id == account, User.tenant_id == tenant).with_for_update()
    ).first()
    if member is None or _current_grant(session, member.id) is None:
        raise NotFoundError("no such designated reviewer")
    session.add(
        EngineeringReviewerGrantRow(
            tenant_id=tenant,
            user_id=member.id,
            action="revoke",
            qualification_statement=None,
            granted_by_user_id=uuid.UUID(user.id),
        )
    )
    session.flush()
    logger.info("engineering_reviews.revoked", tenant_id=str(tenant), member_id=str(member.id))


def reviewers(*, session: Session, user: CurrentUser) -> list[ReviewerOut]:
    """The tenant's current designations, for its owner or an admin.

    Raises:
        AuthorizationError: ``team_owner_only`` for anyone else.
    """
    found = team.standing(session=session, user=user)
    if found is None or not found.manages:
        raise AuthorizationError(
            "only the account's owner or an admin sees its engineering reviewers",
            code="team_owner_only",
        )
    rows = session.scalars(
        select(EngineeringReviewerGrantRow).order_by(EngineeringReviewerGrantRow.seq)
    ).all()
    latest: dict[uuid.UUID, EngineeringReviewerGrantRow] = {}
    for row in rows:
        latest[row.user_id] = row
    current = [row for row in latest.values() if row.action == "grant"]
    names = {
        u.id: u.full_name
        for u in session.scalars(select(User).where(User.id.in_([r.user_id for r in current])))
    }
    return [
        _reviewer_out(row, names.get(row.user_id)) for row in sorted(current, key=lambda r: r.seq)
    ]


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


def record_decision(
    *, session: Session, user: CurrentUser, request: DecisionRequest
) -> DecisionOut:
    """A designated reviewer records a decision in their tenant.

    The qualification statement recorded with it is the one on the
    reviewer's current grant, as the owner supplied it.

    Raises:
        AuthorizationError: ``engineering_review_not_permitted``.
        ValidationError: ``review_subject_invalid``, ``review_field_invalid``.
    """
    tenant = _tenant(user)
    bind_tenant(session, tenant)
    check_subject(request.subject_kind, request.subject_ref, scope="tenant")
    standards = _standards(request.standards)
    reason = _plain(request.reason, "reason")
    permissions.require(
        session=session, user=user, permission=Permission.REVIEW_ENGINEERING_DEFINITIONS
    )
    account = uuid.UUID(user.id)
    grant = _current_grant(session, account)
    if grant is None or grant.qualification_statement is None:  # pragma: no cover - require
        raise AuthorizationError("not designated", code="engineering_review_not_permitted")
    authors = definition_authors(
        session=session,
        tenant_id=tenant,
        kind=request.subject_kind,
        ref=request.subject_ref,
        digest=request.subject_digest,
    )
    if authors is not None and user.email.strip().lower() in authors:
        raise AuthorizationError(
            "a reviewer may not decide on a definition they saved; "
            "another designated reviewer must review it",
            code="engineering_review_self_review",
        )
    row = EngineeringReviewDecisionRow(
        tenant_id=tenant,
        subject_kind=request.subject_kind,
        subject_ref=request.subject_ref,
        subject_digest=request.subject_digest,
        decision=request.decision,
        reviewer_user_id=account,
        qualification_statement=grant.qualification_statement,
        standards=standards,
        reason=reason,
        independence_checked=authors is not None,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    logger.info(
        "engineering_reviews.decision_recorded",
        tenant_id=str(tenant),
        kind=row.subject_kind,
        ref=row.subject_ref,
        decision=row.decision,
    )
    return _tenant_out(row, current=True, standing="designated")


def definition_authors(
    *, session: Session, tenant_id: uuid.UUID, kind: str, ref: str, digest: str
) -> frozenset[str] | None:
    """The accounts (emails, lower case) known to have authored a tenant definition.

    A tenant template's or pattern's authors are the accounts that saved a
    revision of the tenant whose stored design holds that exact definition
    (ref, and digest with its review unset). ``None`` when the server knows
    no author: a rule, a ``global/`` definition (the software's), or one no
    saved revision of the tenant holds (independence is then not checked).

    Args:
        session: Open database session, bound to ``tenant_id``.
        tenant_id: The tenant.
        kind: ``template``, ``pattern`` or ``rule``.
        ref: The definition's ref.
        digest: Its digest (``anchoring.definition_digest``).
    """
    if kind not in ("template", "pattern") or not ref.startswith("tenant/"):
        return None
    key = "templates" if kind == "template" else "patterns"
    rows = session.execute(
        select(DesignRevisionRow.author, DesignRevisionRow.design_snapshot).where(
            DesignRevisionRow.tenant_id == tenant_id,
            DesignRevisionRow.design_snapshot.contains({"model": {key: [{"ref": ref}]}}),
        )
    ).all()
    authors: set[str] = set()
    for author, stored in rows:
        try:
            model = read_snapshot(stored).v2
        except (versions.SnapshotSchemaUnknownError, versions.SnapshotUnreadableError):
            continue
        if model is None:
            continue
        held = model.templates if kind == "template" else model.patterns
        if any(d.ref == ref and anchoring.definition_digest(d) == digest for d in held):
            authors.add((author or "").strip().lower())
    authors.discard("")
    return frozenset(authors) if authors else None


def record_global_decision(
    *,
    session: Session,
    subject_kind: str,
    subject_ref: str,
    subject_digest: str,
    decision: str,
    reviewer_label: str,
    qualification_statement: str,
    standards: Sequence[str],
    reason: str,
    recorded_by: str,
) -> GlobalEngineeringReviewDecisionRow:
    """An operator records a global-scope decision (worker ``record-global-review``).

    The reviewer label and qualification statement are entered by the
    operator as the owner supplied them; nothing is filled in.

    Raises:
        ValidationError: ``review_subject_invalid``, ``review_field_invalid``.
    """
    check_subject(subject_kind, subject_ref, scope="global")
    if not registry.is_digest(subject_digest):
        raise ValidationError(
            "not a digest", code="review_field_invalid", params={"field": "subject_digest"}
        )
    if decision not in registry.DECISIONS:
        raise ValidationError(
            "not a decision", code="review_field_invalid", params={"field": "decision"}
        )
    row = GlobalEngineeringReviewDecisionRow(
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        subject_digest=subject_digest,
        decision=decision,
        reviewer_label=_plain(reviewer_label, "reviewer_label", 200),
        qualification_statement=_plain(qualification_statement, "qualification_statement"),
        standards=_standards(standards),
        reason=_plain(reason, "reason"),
        recorded_by=_plain(recorded_by, "recorded_by", 200),
        # The server knows no author of a global definition.
        independence_checked=False,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


#: What a decision says about independence, as every output states it.
INDEPENDENCE_CHECKED = (
    "Checked only that the reviewer saved no revision of this tenant holding this "
    "definition; nothing else about their independence is checked"
)
INDEPENDENCE_NOT_CHECKED = (
    "Independence was not checked: the server knows no author of this definition, "
    "so the reviewer may have authored it"
)


def _independence(checked: bool) -> str:
    return INDEPENDENCE_CHECKED if checked else INDEPENDENCE_NOT_CHECKED


def _tenant_out(
    row: EngineeringReviewDecisionRow, *, current: bool, standing: ReviewerStanding
) -> DecisionOut:
    return DecisionOut(
        id=str(row.id),
        scope="tenant",
        subject_kind=row.subject_kind,
        subject_ref=row.subject_ref,
        subject_digest=row.subject_digest,
        decision=row.decision,
        reviewer=str(row.reviewer_user_id),
        qualification_statement=row.qualification_statement,
        standards=list(row.standards),
        reason=row.reason,
        recorded_at=row.created_at,
        current=current,
        reviewer_standing=standing,
        independence_checked=row.independence_checked,
        independence=_independence(row.independence_checked),
    )


def _global_out(row: GlobalEngineeringReviewDecisionRow, *, current: bool) -> DecisionOut:
    return DecisionOut(
        id=str(row.id),
        scope="global",
        subject_kind=row.subject_kind,
        subject_ref=row.subject_ref,
        subject_digest=row.subject_digest,
        decision=row.decision,
        reviewer=row.reviewer_label,
        qualification_statement=row.qualification_statement,
        standards=list(row.standards),
        reason=row.reason,
        recorded_at=row.created_at,
        current=current,
        reviewer_standing="operator",
        independence_checked=row.independence_checked,
        independence=_independence(row.independence_checked),
    )


def _standings(session: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, ReviewerStanding]:
    """Each account of the tenant that ever held a designation: its standing now.

    ``designated`` while its newest grant row is a grant and the account is
    active; ``revoked`` when that row is a revocation; ``inactive`` when the
    account is deactivated (or gone).
    """
    latest: dict[uuid.UUID, str] = {}
    for grant in session.scalars(
        select(EngineeringReviewerGrantRow)
        .where(EngineeringReviewerGrantRow.tenant_id == tenant_id)
        .order_by(EngineeringReviewerGrantRow.seq)
    ):
        latest[grant.user_id] = grant.action
    active = set(
        session.scalars(
            select(User.id).where(
                User.id.in_(list(latest)), User.tenant_id == tenant_id, User.is_active.is_(True)
            )
        )
    )
    out: dict[uuid.UUID, ReviewerStanding] = {}
    for account, action in latest.items():
        if action != "grant":
            out[account] = "revoked"
        elif account not in active:
            out[account] = "inactive"
        else:
            out[account] = "designated"
    return out


def decisions(
    *, session: Session, user: CurrentUser, subject_kind: str, subject_ref: str
) -> list[DecisionOut]:
    """Every decision on a subject the caller's tenant can see, newest first.

    Its own tenant's and the global ones; ``current`` marks the newest for
    each scope and digest.

    Raises:
        AuthorizationError: ``engineering_review_account_required`` for a
            caller with no active account (a trial).
        ValidationError: ``review_subject_invalid``.
    """
    check_subject(subject_kind, subject_ref)
    if team.standing(session=session, user=user) is None:
        raise AuthorizationError(
            "review decisions are shown to the accounts of a team",
            code="engineering_review_account_required",
        )
    local = session.scalars(
        select(EngineeringReviewDecisionRow)
        .where(
            EngineeringReviewDecisionRow.subject_kind == subject_kind,
            EngineeringReviewDecisionRow.subject_ref == subject_ref,
        )
        .order_by(EngineeringReviewDecisionRow.seq.desc())
    ).all()
    shared = session.scalars(
        select(GlobalEngineeringReviewDecisionRow)
        .where(
            GlobalEngineeringReviewDecisionRow.subject_kind == subject_kind,
            GlobalEngineeringReviewDecisionRow.subject_ref == subject_ref,
        )
        .order_by(GlobalEngineeringReviewDecisionRow.seq.desc())
    ).all()
    standings = _standings(session, _tenant(user))
    out: list[tuple[DecisionOut, int]] = []
    seen: set[str] = set()
    for row in local:
        standing = standings.get(row.reviewer_user_id, "inactive")
        counts = standing == "designated"
        out.append(
            (
                _tenant_out(
                    row,
                    current=counts and row.subject_digest not in seen,
                    standing=standing,
                ),
                row.seq,
            )
        )
        if counts:
            seen.add(row.subject_digest)
    seen = set()
    for grow in shared:
        out.append((_global_out(grow, current=grow.subject_digest not in seen), grow.seq))
        seen.add(grow.subject_digest)
    return [
        d for d, _ in sorted(out, key=lambda pair: (pair[0].recorded_at, pair[1]), reverse=True)
    ]


def anchor_for(*, session: Session, tenant_id: uuid.UUID) -> anchoring.ReviewAnchor:
    """The current decisions that apply in a tenant, for anchoring.

    Args:
        session: Open database session; bound to ``tenant_id`` here.
        tenant_id: The tenant.

    Returns:
        The newest decision per kind, ref and digest, in the tenant and
        globally; a tenant decision only while its reviewer is designated
        and active (``_standings``).
    """
    bind_tenant(session, tenant_id)
    standings = _standings(session, tenant_id)
    local: dict[tuple[str, str, str], anchoring.Decided] = {}
    for row in session.scalars(
        select(EngineeringReviewDecisionRow).order_by(EngineeringReviewDecisionRow.seq)
    ):
        if standings.get(row.reviewer_user_id) != "designated":
            continue
        local[(row.subject_kind, row.subject_ref, row.subject_digest)] = anchoring.Decided(
            decision=row.decision,
            scope="tenant",
            record_id=str(row.id),
            decided_at=row.created_at.isoformat(),
            standards=tuple(row.standards),
        )
    shared: dict[tuple[str, str, str], anchoring.Decided] = {}
    for grow in session.scalars(
        select(GlobalEngineeringReviewDecisionRow).order_by(GlobalEngineeringReviewDecisionRow.seq)
    ):
        shared[(grow.subject_kind, grow.subject_ref, grow.subject_digest)] = anchoring.Decided(
            decision=grow.decision,
            scope="global",
            record_id=str(grow.id),
            decided_at=grow.created_at.isoformat(),
            standards=tuple(grow.standards),
        )
    return anchoring.ReviewAnchor(tenant=local, global_=shared)


def ever_decided(*, session: Session, tenant_id: uuid.UUID) -> frozenset[tuple[str, str, str, str]]:
    """Every ``(kind, ref, digest, decision)`` ever recorded in the tenant or globally.

    Whoever recorded it and whether it still counts: what a stored design's
    findings may have been anchored to when it was saved
    (``design_projects._reviews_explained``).
    """
    bind_tenant(session, tenant_id)
    found: set[tuple[str, str, str, str]] = set()
    for table in (EngineeringReviewDecisionRow, GlobalEngineeringReviewDecisionRow):
        for kind, ref, digest, decision in session.execute(
            select(table.subject_kind, table.subject_ref, table.subject_digest, table.decision)
        ):
            found.add((kind, ref, digest, decision))
    return frozenset(found)


#: A rule's effective status in a tenant (``evaluation.rule_status``).
rule_status = evaluation.rule_status
