"""AI-assisted requirements intake (M6b): proposals, decisions, and applying one at save.

``docs/architecture/m6b-ai-intake.md`` is the contract. A model reads a
designer's description and proposes entries for the guided requirements
step; this service checks the output (``app.design.ai_intake``), stores it as
a tenant-scoped proposal, records the user's decisions (append-only, who and
when the server's), and, when a revision is saved, checks the placements of
confirmed items and builds the record's ``ai`` block. The designer receives
only the request: nothing here feeds design, calculations or checks.

Every failure saves nothing and never touches the user's draft. The model
call is charged to the tenant's month, and counted against the account's own
month with its one in-flight lease (``ai_intake_usage``), in one short
transaction committed before it is made; the provider is asked outside any
transaction or lock, the proposal stored in a short transaction after, and
the lease released however the call ended.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai import intake_proposer
from app.core.errors import (
    AuthorizationError,
    BadGatewayError,
    ConflictError,
    GatewayTimeoutError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.tenancy import bind_tenant
from app.design import ai_intake
from app.domain import ai_intake_usage, model_budget
from app.models.schemas.auth import CurrentUser
from app.models.schemas.design import (
    AiDecisionRequest,
    AiProposal,
    AiProposalRequest,
    AiRequirementsInput,
    ProjectDesignRequest,
)
from app.models.tables.design_ai_proposals import DesignAiProposalRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

#: Who a decision names when its account has neither a display name nor an email.
UNKNOWN_ACCOUNT = "unknown account"

#: The whole provider call's deadline (S1), seconds; past it the request is
#: 504 ``ai_timeout`` whatever the client's own timeouts did.
PROVIDER_DEADLINE_S = intake_proposer.DEADLINE_S

#: Threads the provider calls run on, so a call can be abandoned at its
#: deadline. A call abandoned keeps its thread only until the client's own
#: timeout (the same deadline, no retry) ends it.
_CALLS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-intake")

#: The most decisions one proposal keeps (a click loop is stopped here).
MAX_DECISIONS = 2000


def get_proposal_provider() -> intake_proposer.ProposalProvider:
    """The provider a proposal asks: the configured model. Tests override this dependency."""
    return intake_proposer.LlmProposalProvider()


def _tenant(session: Session, user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    bind_tenant(session, tenant)
    return tenant


def _account(user: CurrentUser) -> uuid.UUID:
    """The caller's account id; a trial (no email, no account) is refused."""
    try:
        account = uuid.UUID(user.id) if user.email else None
    except ValueError:
        account = None
    if account is None:
        raise AuthorizationError(
            "AI-assisted intake requires a signed-in account", code="ai_requires_account"
        )
    return account


def _name_of(session: Session, tenant: uuid.UUID, account: uuid.UUID, email: str) -> str:
    found = session.execute(
        select(User.full_name).where(User.tenant_id == tenant, User.id == account)
    ).scalar_one_or_none()
    name = (found or "").strip()
    return name or email or UNKNOWN_ACCOUNT


def _row(session: Session, proposal_id: str, *, lock: bool = False) -> DesignAiProposalRow:
    try:
        key = uuid.UUID(str(proposal_id))
    except ValueError as exc:
        raise NotFoundError("no such proposal", code="ai_proposal_not_found") from exc
    found = session.get(DesignAiProposalRow, key, with_for_update=lock, populate_existing=lock)
    if found is None:
        raise NotFoundError("no such proposal", code="ai_proposal_not_found")
    return found


def view(row: DesignAiProposalRow) -> AiProposal:
    """The proposal as the API returns it, each item with where its decisions leave it."""
    latest = ai_intake.latest_decisions(row.decisions or [])
    items = []
    for item in row.items:
        decision = latest.get(str(item["id"]))
        items.append(
            {
                **item,
                "state": ai_intake.state_of(decision),
                "decided_value": ai_intake.decided_value(item, decision),
            }
        )
    return AiProposal.model_validate(
        {
            "id": str(row.id),
            "schema": ai_intake.SCHEMA,
            "created_at": row.created_at.isoformat(),
            "created_by_user_id": str(row.created_by_user_id) if row.created_by_user_id else None,
            "input_text": row.input_text,
            "input_sha256": row.input_sha256,
            "provider": row.provider,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "items": items,
            "server_rejected": row.server_rejected,
            "decisions": row.decisions or [],
            "used": row.used_at is not None,
            "used_by_revision_id": (
                str(row.used_by_revision_id) if row.used_by_revision_id else None
            ),
            "proposal_sha256": row.proposal_sha256,
            "life_safety_mentioned": ai_intake.life_safety(row.input_text),
            "ai_consent_at": row.ai_consent_at.isoformat() if row.ai_consent_at else None,
            "ai_consent_by_user_id": (
                str(row.ai_consent_by_user_id) if row.ai_consent_by_user_id else None
            ),
        }
    )


def create_proposal(
    *,
    session: Session,
    user: CurrentUser,
    request: AiProposalRequest,
    provider: intake_proposer.ProposalProvider,
) -> AiProposal:
    """Ask the model for a proposal, check it, and store it for the user's decisions.

    Raises:
        ValidationError: ``ai_consent_required``, ``ai_input_empty``,
            ``ai_input_too_long``.
        AuthorizationError: ``ai_requires_account`` (a trial).
        ConflictError: ``ai_request_in_flight`` (the account's other call).
        ServiceUnavailableError: ``ai_unavailable``.
        BadGatewayError: ``ai_proposal_malformed``.
        GatewayTimeoutError: ``ai_timeout``.
        TooManyRequestsError: ``ai_user_limit_exceeded`` (the account's
            month), ``model_budget_exceeded`` (the tenant's).
    """
    text = request.description
    if not request.ai_consent:
        raise ValidationError(
            "confirm that the description will be sent to the AI model provider",
            code="ai_consent_required",
        )
    if not text.strip():
        raise ValidationError("describe the project first", code="ai_input_empty")
    if len(text) > ai_intake.MAX_INPUT_CHARS:
        raise ValidationError(
            f"a description holds at most {ai_intake.MAX_INPUT_CHARS} characters",
            code="ai_input_too_long",
            params={"limit": ai_intake.MAX_INPUT_CHARS, "length": len(text)},
        )
    tenant = _tenant(session, user)
    account = _account(user)
    boards = ai_intake.existing_boards(request.request)
    # The call is charged in a short transaction of its own, committed
    # before the model is asked: no row lock or connection is held while the
    # provider answers (up to the client's read timeout), and a call the
    # month's ceiling refuses never reaches the model. A failed call stays
    # charged: the provider was asked. The account's own month and its one
    # call in flight are taken under its row lock in the same transaction,
    # so either refusal charges nothing (S2).
    try:
        lease = ai_intake_usage.acquire(session=session, tenant_id=tenant, user_id=account)
        model_budget.charge_model_call(session=session, tenant_id=tenant)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    try:
        row = _ask_and_store(
            session=session,
            tenant=tenant,
            account=account,
            text=text,
            boards=boards,
            provider=provider,
        )
    finally:
        try:
            ai_intake_usage.release(session=session, lease=lease)
        except Exception:  # the lease expires on its own
            logger.warning("design.ai_lease_not_released", tenant_id=str(tenant))
    return view(row).model_copy(update={"calls_remaining": lease.remaining})


def _ask_and_store(
    *,
    session: Session,
    tenant: uuid.UUID,
    account: uuid.UUID,
    text: str,
    boards: list[str],
    provider: intake_proposer.ProposalProvider,
) -> DesignAiProposalRow:
    """Ask the provider (no transaction open), check its answer and store the proposal."""
    try:
        call = _CALLS.submit(provider.propose, description=text, boards=boards)
        try:
            answer = call.result(timeout=PROVIDER_DEADLINE_S)
        except FutureTimeout as exc:
            call.cancel()
            raise intake_proposer.ProviderTimeout("past the deadline") from exc
    except intake_proposer.ProviderTimeout as exc:
        raise GatewayTimeoutError(
            "the model did not answer in time; try again", code="ai_timeout"
        ) from exc
    except intake_proposer.ProviderMalformed as exc:
        raise BadGatewayError(
            "the model's proposal could not be read; try again", code="ai_proposal_malformed"
        ) from exc
    except intake_proposer.ProviderUnavailable as exc:
        raise ServiceUnavailableError(
            "AI-assisted intake is not available; enter the requirements in the editor",
            code="ai_unavailable",
        ) from exc
    try:
        items, rejected = ai_intake.check_output(answer.payload, text, boards)
    except ai_intake.ProposalMalformed as exc:
        # Never logged: the reason may echo the model's output.
        logger.info("design.ai_proposal_malformed", tenant_id=str(tenant))
        raise BadGatewayError(
            "the model's proposal could not be read; try again", code="ai_proposal_malformed"
        ) from exc
    provenance = {
        "provider": str(answer.provider)[:40],
        "model": str(answer.model)[:200],
        "prompt_version": str(answer.prompt_version)[:64],
    }
    try:
        now = datetime.now(UTC)
        row = DesignAiProposalRow(
            tenant_id=tenant,
            created_by_user_id=account,
            created_at=now,
            ai_consent_at=now,
            ai_consent_by_user_id=account,
            input_text=text,
            input_sha256=ai_intake.input_sha256(text),
            items=items,
            server_rejected=rejected,
            decisions=[],
            proposal_sha256=ai_intake.proposal_sha256(
                input_text=text, items=items, server_rejected=rejected, **provenance
            ),
            **provenance,
        )
        session.add(row)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    logger.info(
        "design.ai_proposal_created",
        tenant_id=str(tenant),
        proposal_id=str(row.id),
        items=len(items),
        rejected=len(rejected),
    )
    return row


def get_proposal(*, session: Session, user: CurrentUser, proposal_id: str) -> AiProposal:
    """The caller's tenant's proposal; another tenant's is ``ai_proposal_not_found``."""
    _tenant(session, user)
    return view(_row(session, proposal_id))


def delete_proposal(*, session: Session, user: CurrentUser, proposal_id: str) -> None:
    """Delete one of the caller's own proposals that no saved revision applied (S3).

    A proposal a revision applied is part of that revision's record and is
    never deleted here; the revision keeps its own copy in any case.

    Raises:
        NotFoundError: ``ai_proposal_not_found`` (also another tenant's).
        AuthorizationError: ``ai_requires_account``; ``ai_proposal_not_yours``
            (another account's proposal in the tenant).
        ConflictError: ``ai_proposal_used``.
    """
    tenant = _tenant(session, user)
    try:
        account = _account(user)
        row = _row(session, proposal_id, lock=True)
        if row.created_by_user_id != account:
            raise AuthorizationError(
                "only the account that asked for a proposal can delete it",
                code="ai_proposal_not_yours",
            )
        if row.used_at is not None:
            raise ConflictError(
                "this proposal was applied by a saved revision and is part of its record",
                code="ai_proposal_used",
            )
        session.delete(row)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    logger.info("design.ai_proposal_deleted", tenant_id=str(tenant), proposal_id=str(row.id))


def purge_unused_proposals(
    *, session: Session, older_than_days: int | None = None, now: datetime | None = None
) -> int:
    """Delete every proposal no saved revision applied, older than the retention (S3).

    Spans tenants: the caller (a system job) lifts the tenant filter. A
    proposal a save is applying holds its row lock; the delete waits for it
    and then re-reads ``used_at``, so an applied proposal is never purged.
    The caller commits.

    Args:
        session: A session that may span tenants.
        older_than_days: ``AI_INTAKE_UNUSED_RETENTION_DAYS`` by default.
        now: Injected for tests.

    Returns:
        How many proposals were deleted.
    """
    from datetime import timedelta

    from app.core.config import get_settings

    days = older_than_days or get_settings().ai_intake_unused_retention_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    result = session.execute(
        delete(DesignAiProposalRow)
        .where(DesignAiProposalRow.used_at.is_(None), DesignAiProposalRow.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def decide(
    *,
    session: Session,
    user: CurrentUser,
    proposal_id: str,
    item_id: str,
    decision: AiDecisionRequest,
) -> AiProposal:
    """Record a decision on one item (append-only); who and when are the server's.

    Raises:
        NotFoundError: ``ai_proposal_not_found``, ``ai_item_not_found`` (also
            an item the server refused: it takes no decision).
        ConflictError: ``ai_proposal_used``.
        ValidationError: ``ai_edit_not_valid``, ``ai_decisions_full``.
        AuthorizationError: ``ai_requires_account``.
    """
    tenant = _tenant(session, user)
    try:
        account = _account(user)
        row = _row(session, proposal_id, lock=True)
        if row.used_at is not None:
            raise ConflictError(
                "this proposal was applied by a saved revision; it takes no more decisions",
                code="ai_proposal_used",
            )
        item = next((i for i in row.items if str(i["id"]) == item_id), None)
        if item is None:
            raise NotFoundError("the proposal has no such item", code="ai_item_not_found")
        value = None
        if decision.decision == "edit":
            try:
                value = ai_intake.check_edit(item["kind"], decision.value or {})
            except ValueError as exc:
                raise ValidationError(
                    str(exc), code="ai_edit_not_valid", params={"kind": item["kind"]}
                ) from exc
        if len(row.decisions or []) >= MAX_DECISIONS:
            raise ValidationError(
                f"a proposal keeps at most {MAX_DECISIONS} decisions",
                code="ai_decisions_full",
                params={"limit": MAX_DECISIONS},
            )
        entry: dict[str, Any] = {
            "item_id": item_id,
            "decision": decision.decision,
            "value": value,
            "by_user_id": str(account),
            "by_name": _name_of(session, tenant, account, user.email),
            "at": datetime.now(UTC).isoformat(),
        }
        row.decisions = [*(row.decisions or []), entry]
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return view(row)


def apply(
    *,
    session: Session,
    user: CurrentUser,
    request: ProjectDesignRequest,
    given: AiRequirementsInput,
    control: Sequence[Any],
    lock: bool = True,
) -> tuple[DesignAiProposalRow, dict[str, Any]]:
    """Check a save's AI block and build the record's ``ai`` block (the proposal not yet marked).

    Call inside the save's transaction (the session bound to the tenant):
    the proposal row is locked until it commits, so two saves cannot both
    apply it.

    Args:
        session: The save's session.
        user: The caller (a trial is refused).
        request: The request as saved.
        given: The proposal and placements the save names.
        control: The save's control needs (``ControlRequirementInput``).
        lock: Lock the proposal (a save); ``False`` for a preview.

    Returns:
        The proposal row, and the ``ai`` block without ``applied_in_revision``.

    Raises:
        NotFoundError: ``ai_proposal_not_found``.
        ConflictError: ``ai_proposal_used``.
        ValidationError: ``ai_item_not_confirmed``, ``ai_placement_unknown``,
            ``ai_placement_invalid``, ``ai_placement_twice``.
        AuthorizationError: ``ai_requires_account`` (a trial).
    """
    _account(user)
    row = _row(session, given.proposal_id, lock=lock)
    if row.used_at is not None:
        raise ConflictError(
            "this proposal was already applied by a saved revision; ask for a new one",
            code="ai_proposal_used",
        )
    needs = {(c.board_index, c.load_index): [n.value for n in c.needs] for c in control}
    try:
        placements = ai_intake.place(
            request,
            needs,
            row.items,
            row.decisions or [],
            [p.model_dump() for p in given.placements],
        )
    except ai_intake.PlacementError as exc:
        raise ValidationError(str(exc), code=exc.code, params=exc.params) from exc
    block = {
        "proposal": {
            "id": str(row.id),
            "schema": ai_intake.SCHEMA,
            "created_at": row.created_at.isoformat(),
            "created_by_user_id": (str(row.created_by_user_id) if row.created_by_user_id else None),
            "input_text": row.input_text,
            "input_sha256": row.input_sha256,
            "provider": row.provider,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "items": list(row.items),
            "server_rejected": list(row.server_rejected),
            "proposal_sha256": row.proposal_sha256,
        },
        "decisions": list(row.decisions or []),
        "placements": placements,
        "applied_at": datetime.now(UTC).isoformat(),
    }
    return row, block


def mark_used(row: DesignAiProposalRow, revision_id: uuid.UUID) -> None:
    """Mark the proposal applied by ``revision_id``: no more decisions, no second save."""
    row.used_at = datetime.now(UTC)
    row.used_by_revision_id = revision_id
