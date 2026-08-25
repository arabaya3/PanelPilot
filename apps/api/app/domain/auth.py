"""Authentication and tenancy service.

Owns account creation, credential checking, refresh-token rotation, and the
free-tier quota. Framework-agnostic: nothing here imports FastAPI.

Two invariants worth stating plainly, because both are the kind that fail
silently:

* **Every user belongs to exactly one tenant.** A trial user gets an implicit
  single-user tenant, so no code path ever has to handle "no tenant". The
  schema enforces it; this module is what creates the tenant.
* **The quota is counted on the server.** A client-reported count is a number
  the client chooses. ``consume_free_question`` is the only thing that
  increments it, and it does so on a *completed* answer — a refused or failed
  diagnosis must not burn a question the engineer never received.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_claim_secret,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.auth_flows import QuotaStatus, TokenPair
from app.models.tables.session import AnonymousSessionRow, RefreshTokenRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

# How long a refresh token stays usable. Longer than an access token by design:
# it is the thing that saves the user from logging in every hour.
REFRESH_TOKEN_TTL = timedelta(days=30)


def _slugify_email(email: str) -> str:
    """Derive a tenant slug from an email address.

    Args:
        email: The signup address.

    Returns:
        A slug fragment; uniqueness is ensured by the caller appending entropy.
    """
    local = email.split("@", 1)[0].lower()
    return "".join(c if c.isalnum() else "-" for c in local)[:40].strip("-") or "tenant"


def signup(
    *,
    session: Session,
    email: str,
    password: str,
    full_name: str | None = None,
    claim_session_id: str | None = None,
    claim_secret: str | None = None,
) -> TokenPair:
    """Create an account, its implicit tenant, and a token pair.

    A trial user gets a single-user tenant created here, so the schema never
    special-cases "no tenant".

    When ``claim_session_id`` names an unclaimed anonymous session, the new
    user joins **that session's existing tenant** rather than getting a fresh
    one. The conversation history is therefore already under the right tenant
    and nothing is copied or re-pointed — which is what makes claiming safe,
    rather than a bulk update that could half-succeed.

    Args:
        session: Open database session. The caller commits.
        email: The new account's address; must not already exist.
        password: Plaintext password, hashed before storage.
        full_name: Optional display name.
        claim_session_id: Anonymous session to carry into the new account.
        claim_secret: The secret issued when that trial session started.
            Required alongside ``claim_session_id``: the session id travels
            in URLs and is not secret, so accepting it alone let anyone who
            learned one join that session's tenant as a full user.

    Returns:
        A fresh access/refresh token pair.

    Raises:
        ValidationError: If the email is already registered, or the password is
            unusable.
        NotFoundError: If ``claim_session_id`` names no anonymous session.
    """
    normalised = email.strip().lower()
    existing = session.execute(select(User).where(User.email == normalised)).scalar_one_or_none()
    if existing is not None:
        raise ValidationError("that email is already registered")

    claimed: AnonymousSessionRow | None = None
    if claim_session_id:
        claimed = _load_claimable_session(
            session=session, session_id=claim_session_id, claim_secret=claim_secret
        )
        tenant = session.get(TenantRow, claimed.tenant_id)
        if tenant is None:  # pragma: no cover — FK guarantees this
            raise NotFoundError("the anonymous session's tenant is missing")
    else:
        tenant = TenantRow(
            slug=f"{_slugify_email(normalised)}-{uuid.uuid4().hex[:8]}",
            name=full_name or normalised,
        )
        session.add(tenant)
        session.flush()

    user = User(
        tenant_id=tenant.id,
        email=normalised,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    session.flush()

    if claimed is not None:
        claimed.claimed_by_user_id = user.id
        claimed.claimed_at = datetime.now(UTC)

    return _issue_tokens(session=session, user=user, tenant=tenant)


def _load_claimable_session(
    *, session: Session, session_id: str, claim_secret: str | None
) -> AnonymousSessionRow:
    """Load an anonymous session that may still be claimed.

    Three things must hold, and each has been a real hole:

    1. **The caller proves ownership.** The session id is not a credential; it
       appears in URLs. Without the secret issued when the trial started, any
       un-claimed session id was a takeover of that session's tenant.
    2. **The row is locked.** Otherwise concurrent signups all read
       ``claimed_by_user_id IS NULL`` and every one of them joins the tenant.
    3. **The tenant is still empty.** A provisional trial tenant has no users.
       If it has any, this is not a trial being claimed — it is an attempt to
       join somebody's existing account.

    Args:
        session: Open database session.
        session_id: The anonymous session's identifier.
        claim_secret: The secret issued when the trial started.

    Returns:
        The row, locked, unclaimed, unexpired, and provably the caller's.

    Raises:
        NotFoundError: If no such session exists.
        ValidationError: If it is expired, already claimed, or its tenant is
            no longer a fresh trial.
        AuthenticationError: If the secret does not match.
    """
    try:
        parsed = uuid.UUID(session_id)
    except ValueError as exc:
        raise NotFoundError(f"no anonymous session {session_id!r}") from exc

    row = session.execute(
        select(AnonymousSessionRow).where(AnonymousSessionRow.id == parsed).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no anonymous session {session_id!r}")

    # Constant-time, and before every other check, so a failure reveals nothing
    # about whether the session is claimed or expired.
    if not claim_secret or not secrets.compare_digest(
        hash_claim_secret(claim_secret), row.claim_secret_hash
    ):
        raise AuthenticationError("that trial session cannot be claimed with that secret")

    if row.is_expired:
        raise ValidationError("that trial session has expired")
    if row.claimed_by_user_id is not None:
        raise ValidationError("that trial session has already been claimed")

    occupied = session.execute(
        select(User.id).where(User.tenant_id == row.tenant_id).limit(1)
    ).scalar_one_or_none()
    if occupied is not None:
        raise ValidationError("that trial session belongs to an existing account")

    return row


def login(*, session: Session, email: str, password: str) -> TokenPair:
    """Exchange credentials for a token pair.

    Args:
        session: Open database session. The caller commits.
        email: The account address.
        password: The plaintext password.

    Returns:
        A fresh access/refresh token pair.

    Raises:
        AuthenticationError: If the credentials do not match, or the account is
            inactive. The message is identical in every case so it cannot be
            used to discover which addresses are registered.
    """
    normalised = email.strip().lower()
    user = session.execute(select(User).where(User.email == normalised)).scalar_one_or_none()

    # Hash even when the user is absent: returning early on an unknown address
    # makes login time a reliable oracle for which emails have accounts.
    # A user with no password set (SSO, or mid-invite) can never match, but
    # must still cost the same time as a wrong password.
    stored = (user.password_hash if user is not None else None) or _DUMMY_HASH
    matched = verify_password(password, stored)

    if user is None or not matched or not user.is_active:
        raise AuthenticationError("email or password is incorrect")

    tenant = session.get(TenantRow, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise AuthenticationError("email or password is incorrect")

    return _issue_tokens(session=session, user=user, tenant=tenant)


# A real bcrypt hash of a value nothing will match, used to keep the timing of
# a failed lookup indistinguishable from a wrong password.
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.9k1KZ0MFxLQaK0OyZ8/1xxDhKZ3ZQzO"


def refresh(*, session: Session, refresh_token: str) -> TokenPair:
    """Rotate a refresh token for a new pair.

    The presented token is revoked as part of issuing its replacement, so a
    token cannot be used twice. Presenting an already-revoked token is a replay
    and is refused.

    Args:
        session: Open database session. The caller commits.
        refresh_token: The token as presented by the client.

    Returns:
        A fresh access/refresh token pair.

    Raises:
        AuthenticationError: If the token is unknown, expired, or already used.
    """
    row = session.execute(
        select(RefreshTokenRow).where(
            RefreshTokenRow.token_hash == hash_refresh_token(refresh_token)
        )
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None:
        raise AuthenticationError("refresh token is not valid")
    if row.expires_at <= datetime.now(UTC):
        raise AuthenticationError("refresh token has expired")

    user = session.get(User, row.user_id)
    tenant = session.get(TenantRow, row.tenant_id)
    if user is None or tenant is None or not user.is_active or not tenant.is_active:
        raise AuthenticationError("refresh token is not valid")

    row.revoked_at = datetime.now(UTC)
    return _issue_tokens(session=session, user=user, tenant=tenant)


def _issue_tokens(*, session: Session, user: User, tenant: TenantRow) -> TokenPair:
    """Mint an access token and a stored refresh token for a user.

    Args:
        session: Open database session.
        user: The authenticated user.
        tenant: The tenant they belong to.

    Returns:
        The token pair to hand back to the client.
    """
    from app.core.config import get_settings

    token, token_hash = generate_refresh_token()
    session.add(
        RefreshTokenRow(
            tenant_id=tenant.id,
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + REFRESH_TOKEN_TTL,
        )
    )
    session.flush()

    settings = get_settings()
    return TokenPair(
        access_token=create_access_token(
            subject=str(user.id),
            tenant_id=str(tenant.id),
            roles=frozenset({Role.ENGINEER}),
        ),
        refresh_token=token,
        expires_in=settings.access_token_ttl_seconds,
    )


def get_quota(*, session: Session, tenant_id: str) -> QuotaStatus:
    """Report a tenant's free-tier usage.

    Args:
        session: Open database session.
        tenant_id: The tenant to report on.

    Returns:
        Usage as counted on the server.

    Raises:
        NotFoundError: If the tenant does not exist.
    """
    tenant = _load_tenant(session=session, tenant_id=tenant_id)
    return QuotaStatus(
        questions_used=tenant.free_questions_used,
        question_limit=tenant.free_question_limit,
        questions_remaining=max(0, tenant.free_question_limit - tenant.free_questions_used),
    )


def check_free_question_allowed(*, session: Session, tenant_id: str) -> None:
    """Report early whether the allowance is spent, for a friendlier refusal.

    **Advisory only.** This is an unlocked read, so its answer can be stale by
    the time the question is answered. ``consume_free_question`` is what
    actually enforces the limit, under a row lock, and raises on its own.
    Never use this as the gate.

    Args:
        session: Open database session.
        tenant_id: The asking tenant.

    Raises:
        ValidationError: If the allowance is exhausted.
        NotFoundError: If the tenant does not exist.
    """
    tenant = _load_tenant(session=session, tenant_id=tenant_id)
    if not tenant.has_free_questions_remaining():
        raise ValidationError(f"free question limit of {tenant.free_question_limit} reached")


def consume_free_question(*, session: Session, tenant_id: str) -> QuotaStatus:
    """Record that a question was answered.

    Called only after a diagnosis completes. Incrementing on request instead
    would charge the engineer for an answer they never got.

    **The check and the increment are one locked operation on purpose.**
    Splitting them across two calls made the limit advisory rather than real:
    twenty concurrent requests against a limit of five each read "allowed" and
    then each incremented, serving fifteen. A row lock only helps if the
    decision is taken while holding it.

    Args:
        session: Open database session. The caller commits.
        tenant_id: The asking tenant.

    Returns:
        Usage after the increment.

    Raises:
        ValidationError: If the allowance is already exhausted.
        NotFoundError: If the tenant does not exist.
    """
    tenant = _load_tenant(session=session, tenant_id=tenant_id, for_update=True)
    if not tenant.has_free_questions_remaining():
        raise ValidationError(f"free question limit of {tenant.free_question_limit} reached")
    tenant.free_questions_used += 1
    session.flush()
    return QuotaStatus(
        questions_used=tenant.free_questions_used,
        question_limit=tenant.free_question_limit,
        questions_remaining=max(0, tenant.free_question_limit - tenant.free_questions_used),
    )


def _load_tenant(*, session: Session, tenant_id: str, for_update: bool = False) -> TenantRow:
    """Load a tenant by id.

    Args:
        session: Open database session.
        tenant_id: The tenant identifier.
        for_update: Take a row lock, for read-modify-write on the counter.

    Returns:
        The tenant row.

    Raises:
        NotFoundError: If no such tenant exists.
    """
    try:
        parsed = uuid.UUID(tenant_id)
    except ValueError as exc:
        raise NotFoundError(f"no tenant {tenant_id!r}") from exc

    statement = select(TenantRow).where(TenantRow.id == parsed)
    if for_update:
        statement = statement.with_for_update()
    tenant = session.execute(statement).scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"no tenant {tenant_id!r}")
    return tenant


def resolve_caller(*, session: Session, caller: CurrentUser) -> User:
    """Load the user a token identifies, confirming they still exist.

    A token stays valid until it expires, so a user deactivated mid-session
    would otherwise keep working until then.

    Args:
        session: Open database session.
        caller: The caller decoded from the access token.

    Returns:
        The live user row.

    Raises:
        AuthenticationError: If the user is gone, inactive, or no longer in the
            tenant their token claims.
    """
    try:
        user = session.get(User, uuid.UUID(caller.id))
    except ValueError as exc:
        raise AuthenticationError("token subject is not a user id") from exc

    if user is None or not user.is_active:
        raise AuthenticationError("account is not active")
    # A token whose tenant no longer matches the user's is stale or forged;
    # trusting it would let a moved user act on their old tenant's data.
    if str(user.tenant_id) != caller.tenant_id:
        raise AuthenticationError("token tenant does not match the account")
    return user
