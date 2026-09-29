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
from typing import NoReturn

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
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
from app.core.tenancy import TenantScopeError, bind_tenant, cross_tenant
from app.domain.roles import IMPLICIT_ROLES, roles_of
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.auth_flows import QuotaStatus, TokenPair, TrialStart
from app.models.tables.diagnostics import DiagnosticSessionRow
from app.models.tables.session import AnonymousSessionRow, RefreshTokenRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

# How long a refresh token stays usable. Longer than an access token by design:
# it is the thing that saves the user from logging in every hour.
REFRESH_TOKEN_TTL = timedelta(days=30)

# How long an unclaimed trial stays claimable. Long enough that someone can
# come back after a night shift; short enough that an abandoned session's
# tenant is not claimable indefinitely by whoever finds the secret.
TRIAL_TTL = timedelta(days=7)

# The one message for an address that already has an account, whether the
# pre-check or the unique index catches it.
_EMAIL_TAKEN = "that email is already registered"

# tenants.name is String(200).
_TENANT_NAME_MAX = 200

# The one message for a trial that cannot be resumed for any reason visible
# before the secret has been checked, so a guessed id learns nothing.
_RESUME_REFUSED = "that trial session cannot be resumed with that secret"


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
    with cross_tenant(
        session,
        reason="an email identifies its account, and a trial its tenant, before the caller has one",
    ):
        normalised = email.strip().lower()
        # Hashed before the existence check, not after. Skipping bcrypt for a
        # registered address made signup answer measurably faster for it — a
        # timing oracle for which emails have accounts, even had the message
        # below been made vague.
        password_hash = hash_password(password)
        existing = session.execute(
            select(User).where(User.email == normalised)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValidationError(_EMAIL_TAKEN)

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
                # Truncated to the column (String(200)): an address can be longer
                # than that and still valid, and must not surface as a DataError.
                name=(full_name or normalised)[:_TENANT_NAME_MAX],
            )
            session.add(tenant)
            session.flush()

        user = User(
            tenant_id=tenant.id,
            email=normalised,
            full_name=full_name,
            password_hash=password_hash,
            is_active=True,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            # Two concurrent signups for one address both pass the check above;
            # the unique index stops the second here. Same answer as the check,
            # rather than the 500 an escaped IntegrityError used to be.
            raise ValidationError(_EMAIL_TAKEN) from exc

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
    with cross_tenant(session, reason="an email identifies its account before any tenant is known"):
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
    token cannot be used twice.

    **Rotation is one conditional UPDATE.** It used to be a SELECT followed by
    setting ``revoked_at``, and two concurrent refreshes both read the token
    as live and both minted a pair — one stolen token became two live
    sessions. The UPDATE only matches a live row, and Postgres re-checks that
    condition after waiting on the row lock, so exactly one caller wins.

    **Presenting a spent token revokes the whole family.** A spent token only
    comes back if it was copied: either the legitimate client or a thief
    already rotated it, and there is no telling which. Revoking every live
    refresh token for that user ends the thief's session at the cost of one
    re-login for the real user — the standard reuse-detection trade (RFC 9700
    §4.14.2).

    Args:
        session: Open database session. The caller commits.
        refresh_token: The token as presented by the client.

    Returns:
        A fresh access/refresh token pair.

    Raises:
        AuthenticationError: If the token is unknown, expired, or already used.
    """
    with cross_tenant(
        session, reason="a refresh token identifies its account before any tenant is known"
    ):
        now = datetime.now(UTC)
        token_hash = hash_refresh_token(refresh_token)
        rotated = session.execute(
            update(RefreshTokenRow)
            .where(
                RefreshTokenRow.token_hash == token_hash,
                RefreshTokenRow.revoked_at.is_(None),
                RefreshTokenRow.expires_at > now,
            )
            .values(revoked_at=now)
            .returning(RefreshTokenRow.user_id, RefreshTokenRow.tenant_id)
            # The statement names its row by hash, not by identity; there is no
            # loaded object to keep in step, and evaluating the WHERE in Python
            # would be a second, weaker copy of the condition.
            .execution_options(synchronize_session=False)
        ).one_or_none()

        if rotated is None:
            _refuse_unrotatable_token(session=session, token_hash=token_hash, now=now)

        user = session.get(User, rotated.user_id)
        tenant = session.get(TenantRow, rotated.tenant_id)
        if user is None or tenant is None or not user.is_active or not tenant.is_active:
            raise AuthenticationError("refresh token is not valid")

        return _issue_tokens(session=session, user=user, tenant=tenant)


def _refuse_unrotatable_token(*, session: Session, token_hash: str, now: datetime) -> NoReturn:
    """Explain why a token could not be rotated, revoking its family on replay.

    Args:
        session: Open database session.
        token_hash: Hash of the presented token.
        now: The rotation attempt's timestamp.

    Raises:
        AuthenticationError: Always.
    """
    presented = session.execute(
        select(RefreshTokenRow.user_id, RefreshTokenRow.revoked_at).where(
            RefreshTokenRow.token_hash == token_hash
        )
    ).one_or_none()

    if presented is None:
        raise AuthenticationError("refresh token is not valid")

    if presented.revoked_at is None:
        # Live but not matched by the rotation: its expiry has passed.
        raise AuthenticationError("refresh token has expired")

    session.execute(
        update(RefreshTokenRow)
        .where(
            RefreshTokenRow.user_id == presented.user_id,
            RefreshTokenRow.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    # Committed here, against the usual "the caller commits": the refusal
    # below makes the request's session roll back, and a family revocation
    # that is rolled back is a detection that protects nobody. Nothing else
    # is pending in this transaction — rotation is the first thing refresh
    # does — so this commits the revocation and only the revocation.
    session.commit()
    # The user id only: the token, its hash, and the caller's address are
    # not needed to investigate, and a log is the wrong place for any of them.
    logger.warning("auth.refresh_token_reused", user_id=str(presented.user_id))
    raise AuthenticationError("refresh token is not valid")


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
            # A hint for the client only. Authorization re-reads the account's
            # roles on every request (`authenticate`), so this cannot grant
            # anything, and a revocation does not wait for it to expire.
            roles=roles_of(user),
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


def start_trial(
    *,
    session: Session,
    now: datetime | None = None,
    access_token_ttl_seconds: int | None = None,
) -> TrialStart:
    """Begin an anonymous trial, returning credentials that can actually ask.

    Args:
        session: Open database session. The caller commits.
        now: Injected for tests.
        access_token_ttl_seconds: Token lifetime. Read from settings when not
            given, so a caller that has no configured environment — a unit
            test, most usefully — can still exercise this.

    Returns:
        The session id, its one-time claim secret, and an access token scoped
        to the provisional tenant.

    Three rows, in one transaction, because a trial is only useful if all three
    exist together:

    1. A **provisional tenant**, carrying the free-question quota. Signup later
       joins the new account to *this* tenant rather than making another, which
       is what lets the conversation survive the claim without copying a row.
    2. A **placeholder user**, because every diagnostics route resolves its
       caller to a live account and refuses a token whose subject is not one.
       Marked with a non-routable ``.invalid`` address so it cannot collide
       with a real signup and cannot be mistaken for a contactable person.
    3. The **anonymous session** itself, holding only the *hash* of the claim
       secret — the plaintext is returned here once and never stored.

    The trial user is deliberately NOT the account the visitor ends up with.
    Signup creates their real user in the same tenant and marks this session
    claimed; the placeholder stays as the author of the trial's turns, so the
    history reads correctly rather than retroactively appearing to be written
    by an account that did not exist at the time.
    """
    with cross_tenant(
        session, reason="a trial creates its tenant; there is none to be bound to beforehand"
    ):
        moment = now or datetime.now(UTC)

        tenant = TenantRow(
            slug=f"trial-{uuid.uuid4().hex[:12]}",
            name="Trial",
        )
        session.add(tenant)
        session.flush()

        # No user row, deliberately. `_load_claimable_session` refuses to claim a
        # trial whose tenant already has one — "a provisional trial tenant has no
        # users. If it has any, this is not a trial being claimed, it is an attempt
        # to join somebody's existing account." A placeholder here would satisfy
        # authentication and make every trial permanently unclaimable, which is the
        # one thing the whole pair exists to allow.
        #
        # `diagnostic_sessions.user_id` is nullable for exactly this case.
        diagnostic = DiagnosticSessionRow(tenant_id=tenant.id, user_id=None)
        session.add(diagnostic)
        session.flush()

        # Generated here and returned once. Only the hash is persisted.
        claim_secret = secrets.token_urlsafe(32)
        anonymous = AnonymousSessionRow(
            tenant_id=tenant.id,
            diagnostic_session_id=diagnostic.id,
            claim_secret_hash=hash_claim_secret(claim_secret),
            expires_at=moment + TRIAL_TTL,
        )
        session.add(anonymous)
        session.flush()

        if access_token_ttl_seconds is None:
            from app.core.config import get_settings

            access_token_ttl_seconds = get_settings().access_token_ttl_seconds

        # The subject is the anonymous session itself. There is no user to name,
        # and `resolve_caller` validates a trial subject against this row instead —
        # the same liveness question, asked of the thing that actually exists.
        token = create_access_token(
            subject=str(anonymous.id),
            tenant_id=str(tenant.id),
            roles=frozenset({Role.ENGINEER}),
            ttl_seconds=access_token_ttl_seconds,
        )

        return TrialStart(
            # The ANONYMOUS session's id, not the diagnostic session's. This value
            # comes back as `claim_session_id` at signup, and `_load_claimable_session`
            # looks it up by `AnonymousSessionRow.id`. Returning the diagnostic id
            # here would produce a trial that starts cleanly and then cannot be
            # claimed — a failure that only surfaces at the moment someone commits
            # to signing up.
            session_id=str(anonymous.id),
            claim_secret=claim_secret,
            conversation_id=str(diagnostic.id),
            access_token=token,
            expires_in=access_token_ttl_seconds,
            questions_remaining=tenant.free_question_limit - tenant.free_questions_used,
        )


def resume_trial(
    *,
    session: Session,
    session_id: str,
    claim_secret: str,
    access_token_ttl_seconds: int | None = None,
) -> TrialStart:
    """Issue a fresh access token for a trial the caller already started.

    A trial's access token lives an hour; the trial itself lives a week. A
    visitor who comes back after lunch still holds the session id and claim
    secret, and without this the only way forward was starting a new trial —
    abandoning the conversation and minting another free allowance, which is
    the opposite of what the trial limit wants.

    Args:
        session: Open database session. Nothing is written.
        session_id: The anonymous session id ``start_trial`` returned.
        claim_secret: The secret ``start_trial`` returned with it.
        access_token_ttl_seconds: Token lifetime; read from settings when not
            given, as in ``start_trial``.

    Returns:
        The same shape ``start_trial`` returns: the inputs echoed back, and a
        new access token minted exactly as ``start_trial`` mints one — scoped
        to the trial's tenant, with the anonymous session as its subject.

    Raises:
        AuthenticationError: If the session is unknown, the secret does not
            match, or the trial has expired or been claimed.

    Deliberately not ``_load_claimable_session``. That takes a row lock and
    refuses a tenant that has users — both right for joining a tenant, and
    both irrelevant to reading a credential for it. What resume shares with
    it is the order of checks: the secret first, in constant time, so a
    guessed id learns nothing about whether it exists, has expired, or was
    claimed.
    """
    with cross_tenant(
        session, reason="a trial's id and secret identify its tenant before any is known"
    ):
        try:
            row = session.get(AnonymousSessionRow, uuid.UUID(session_id))
        except ValueError:
            row = None

        # Compared even when the row is absent, against a hash nothing matches,
        # so an unknown id costs the same as a wrong secret.
        expected = row.claim_secret_hash if row is not None else _NO_SECRET_HASH
        matched = secrets.compare_digest(hash_claim_secret(claim_secret), expected)
        if row is None or not matched:
            raise AuthenticationError(_RESUME_REFUSED)

        # Past the secret, the caller has proved ownership and may be told why.
        # The same two conditions `_resolve_trial_caller` enforces on every
        # request: a token minted here for a dead trial would be refused anyway.
        if row.is_expired:
            raise AuthenticationError("that trial session has expired")
        if row.claimed_by_user_id is not None:
            raise AuthenticationError("that trial session has been claimed; log in instead")

        tenant = session.get(TenantRow, row.tenant_id)
        if tenant is None or not tenant.is_active:
            raise AuthenticationError(_RESUME_REFUSED)

        if access_token_ttl_seconds is None:
            from app.core.config import get_settings

            access_token_ttl_seconds = get_settings().access_token_ttl_seconds

        token = create_access_token(
            subject=str(row.id),
            tenant_id=str(tenant.id),
            roles=frozenset({Role.ENGINEER}),
            ttl_seconds=access_token_ttl_seconds,
        )
        return TrialStart(
            session_id=session_id,
            claim_secret=claim_secret,
            conversation_id=str(row.diagnostic_session_id),
            access_token=token,
            expires_in=access_token_ttl_seconds,
            questions_remaining=max(0, tenant.free_question_limit - tenant.free_questions_used),
        )


# A SHA-256 hex digest no secret hashes to in practice; see `resume_trial`.
_NO_SECRET_HASH = "0" * 64


def known_user_id(*, session: Session, subject: str | uuid.UUID) -> uuid.UUID | None:
    """Return a token subject as a user id, if it names a real user row.

    Args:
        session: Open database session.
        subject: A token subject.

    Returns:
        The id when a ``users`` row has it, otherwise ``None``.

    A trial token's subject is its anonymous session, not a user — by design,
    so the tenant stays claimable (see ``start_trial``). A column that is a
    foreign key into ``users`` therefore cannot take a subject on trust:
    written blindly, a trial caller's id is a ForeignKeyViolation, and the
    request fails with a 500 for something the caller did nothing wrong in.
    """
    try:
        identifier = subject if isinstance(subject, uuid.UUID) else uuid.UUID(subject)
    except ValueError:
        return None
    exists = session.execute(select(User.id).where(User.id == identifier)).scalar_one_or_none()
    return identifier if exists is not None else None


def authenticate(*, session: Session, caller: CurrentUser) -> CurrentUser:
    """Turn a decoded token into the caller authorization should trust.

    ``resolve_caller`` proves the account is live and scopes the session to
    its tenant; this then builds the caller from the **database**, not the
    token: the roles an account holds now, and its email. A token's own role
    claim is ignored, so a grant applies from the next request and a
    revocation cannot be outlived by a token minted before it.

    Args:
        session: Open database session; bound to the caller's tenant here.
        caller: The caller decoded from the access token.

    Returns:
        The caller, with roles and email as the database has them. A trial,
        which has no account, is an engineer and nothing more.

    Raises:
        AuthenticationError: As ``resolve_caller``.
    """
    user = resolve_caller(session=session, caller=caller)
    if user is None:
        return caller.model_copy(update={"roles": IMPLICIT_ROLES, "email": ""})
    return caller.model_copy(update={"roles": roles_of(user), "email": user.email})


def resolve_caller(*, session: Session, caller: CurrentUser) -> User | None:
    """Load the user a token identifies, confirming they still exist.

    A token stays valid until it expires, so a user deactivated mid-session
    would otherwise keep working until then.

    Args:
        session: Open database session.
        caller: The caller decoded from the access token.

    Returns:
        The live user row, or ``None`` when the caller is an anonymous trial —
        which has no user by design, so the tenant stays claimable.

    Raises:
        AuthenticationError: If the user is gone, inactive, or no longer in the
            tenant their token claims; or, for a trial, if the session is
            missing, expired, already claimed, or in another tenant.
    """
    try:
        subject = uuid.UUID(caller.id)
    except ValueError as exc:
        raise AuthenticationError("token subject is not a user id") from exc

    # Bound before the account is even loaded, so from here on this session
    # sees only the tenant the token claims: an account or trial in any other
    # tenant is simply not found. This is the one place a request's session
    # gets its tenant (ADR 0003); every query after it is filtered to it.
    try:
        bind_tenant(session, caller.tenant_id)
    except TenantScopeError as exc:
        raise AuthenticationError("token tenant is not valid") from exc

    user = session.get(User, subject)
    if user is None:
        # A trial names its anonymous session rather than a user, because the
        # tenant must stay user-less to remain claimable. The liveness question
        # is the same one, asked of the row that does exist.
        _resolve_trial_caller(session=session, subject=subject, caller=caller)
        return None

    if not user.is_active:
        raise AuthenticationError("account is not active")
    # A token whose tenant no longer matches the user's is stale or forged;
    # trusting it would let a moved user act on their old tenant's data.
    if str(user.tenant_id) != caller.tenant_id:
        raise AuthenticationError("token tenant does not match the account")
    return user


def _resolve_trial_caller(*, session: Session, subject: uuid.UUID, caller: CurrentUser) -> None:
    """Validate a token whose subject is an anonymous trial session.

    Args:
        session: Open database session.
        subject: The token subject.
        caller: The decoded caller.

    Raises:
        AuthenticationError: If no such trial exists, it has expired, it has
            already been claimed, or its tenant does not match the token.

    Every check here has a counterpart in the user path, and skipping any one
    of them would make a trial token strictly more powerful than a real
    account's:

    * **Expiry** is enforced against the row, not just the JWT. A token could
      outlive its trial otherwise, and the trial's expiry is the only thing
      bounding how long an abandoned conversation stays reachable.
    * **Already claimed** matters because the claim moves the tenant to a real
      user. Continuing to honour the trial token afterwards would leave a
      second, unrevocable credential on somebody's actual account.
    * **Tenant match** is the isolation boundary, exactly as above.
    """
    row = session.get(AnonymousSessionRow, subject)
    if row is None:
        raise AuthenticationError("account is not active")

    if str(row.tenant_id) != caller.tenant_id:
        raise AuthenticationError("token tenant does not match the account")

    expires_at = row.expires_at
    if expires_at.tzinfo is None:  # pragma: no cover - Postgres returns aware
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise AuthenticationError("that trial session has expired")

    if row.claimed_by_user_id is not None:
        raise AuthenticationError("that trial session has been claimed")
