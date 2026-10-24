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

import contextlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import structlog
from sqlalchemy import ColumnElement, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import (
    create_access_token,
    derive_refresh_token,
    generate_session_salt,
    hash_claim_secret,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.core.tenancy import TenantScopeError, bind_tenant, cross_tenant
from app.domain import team
from app.domain.roles import IMPLICIT_ROLES, roles_of
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.auth_flows import QuotaStatus, TokenPair, TrialStart
from app.models.tables.diagnostics import DiagnosticSessionRow
from app.models.tables.session import AnonymousSessionRow, AuthSessionRow, RefreshTokenRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

# How long a refresh token stays usable. Longer than an access token by design:
# it is the thing that saves the user from logging in every hour.
REFRESH_TOKEN_TTL = timedelta(days=30)

# How long after a rotation the spent token is still answered, with the same
# live successor, for a refresh that raced the one that rotated it (tabs
# sharing the cookie) or a reload whose answer was lost. See `refresh`.
REFRESH_REUSE_GRACE = timedelta(seconds=10)

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
    invite_token: str | None = None,
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
        invite_token: An invitation to a team (``team.invite``): the account
            joins the inviting tenant instead of getting its own. The
            invitation must be for this email, and cannot be combined with a
            trial claim.

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
        if invite_token and claim_session_id:
            raise ValidationError(
                "an invitation and a trial cannot both be used", code="team_invitation_with_trial"
            )
        if invite_token:
            joined = session.get(
                TenantRow, team.accept(session=session, token=invite_token, email=normalised)
            )
            if joined is None:  # pragma: no cover — FK guarantees this
                raise NotFoundError("the invitation's tenant is missing")
            tenant = joined
        elif claim_session_id:
            claimed = _load_claimable_session(
                session=session, session_id=claim_session_id, claim_secret=claim_secret
            )
            found = session.get(TenantRow, claimed.tenant_id)
            if found is None:  # pragma: no cover — FK guarantees this
                raise NotFoundError("the anonymous session's tenant is missing")
            tenant = found
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


def refresh(
    *,
    session: Session,
    refresh_token: str,
    now: datetime | None = None,
    bearer: CurrentUser | None = None,
) -> TokenPair:
    """Rotate a refresh token for a new pair in the same sign-in session.

    **One live token per session, always.** A session's tokens form a chain
    of generations; exactly one is live.

    **Serialised on the session rows.** Every session of the account is
    locked first, in id order (`_lock_sessions`). A logout locks the same
    rows, so a refresh and a logout cannot interleave: a refresh that loses is
    refused, and a logout that loses revokes the token the refresh minted.

    Then, for the presented token:

    * **Live:** it is spent and its successor (next generation) is minted.
    * **The live token's immediate predecessor, spent within
      ``REFRESH_REUSE_GRACE``:** a tab that presented the same cookie as the
      one that just rotated it (tabs share the cookie), or a reload whose
      answer was lost. It is handed the *same* live successor, re-derived
      (`derive_refresh_token`), not a new one: the session keeps exactly one
      live token, and every answer sets the same cookie, so the order in
      which answers land cannot leave the jar holding a dead value.
    * **Anything else spent** (after the window, or more than one generation
      behind): a replay. Either the client or a thief rotated it, and there
      is no telling which, so **this session** is ended (RFC 9700 §4.14.2).
      The account's other sessions are separate token families the copy
      never touched, and stay; ``logout_everywhere`` ends them on purpose.

    The bound on a thief: holding a spent token P, it gains anything only
    within the window after the rotation that spent P, and then only the one
    live token the victim also holds. The next rotation by either party
    leaves the other with a spent token, whose next use outside the window
    ends the session for both.

    Args:
        session: Open database session. The caller commits.
        refresh_token: The token as presented by the client.
        now: Injected for tests.
        bearer: The caller's access token, already verified (signature and
            expiry) by the route, when one came. Proof of possession for
            the predecessor case: see `_reissue_live_successor`.

    Returns:
        A fresh access token and the session's live refresh token.

    Raises:
        AuthenticationError: If the token is unknown, expired, a replay, from
            before sessions existed, or its session has ended.
    """
    with cross_tenant(
        session, reason="a refresh token identifies its account before any tenant is known"
    ):
        moment = now or datetime.now(UTC)
        token_hash = hash_refresh_token(refresh_token)
        owner = session.execute(
            select(RefreshTokenRow.session_id, RefreshTokenRow.user_id).where(
                RefreshTokenRow.token_hash == token_hash
            )
        ).one_or_none()
        if owner is None:
            raise AuthenticationError("refresh token is not valid")
        if owner.session_id is None:
            # Issued before sign-in sessions existed. It names no session a
            # logout could end, so it is refused rather than adopted.
            raise AuthenticationError(
                "refresh token predates sign-in sessions; sign in again", code="session_ended"
            )

        locked = _lock_sessions(session=session, condition=AuthSessionRow.user_id == owner.user_id)
        sign_in = next((row for row in locked if row.id == owner.session_id), None)
        if sign_in is None or sign_in.revoked_at is not None:
            raise AuthenticationError("this sign-in has ended", code="session_ended")

        # Read under the lock, not before it: a concurrent refresh that held
        # the lock first has spent this token since, and acting on the earlier
        # read would rotate it a second time into a second live token.
        presented = session.execute(
            select(
                RefreshTokenRow.id,
                RefreshTokenRow.revoked_at,
                RefreshTokenRow.expires_at,
                RefreshTokenRow.generation,
            ).where(RefreshTokenRow.token_hash == token_hash)
        ).one()

        user = session.get(User, sign_in.user_id)
        tenant = session.get(TenantRow, sign_in.tenant_id)
        if user is None or tenant is None or not user.is_active or not tenant.is_active:
            raise AuthenticationError("refresh token is not valid")

        generation = presented.generation or 0
        if presented.revoked_at is None:
            if presented.expires_at <= moment:
                raise AuthenticationError("refresh token has expired", code="session_ended")
            # Only the session's newest live token rotates. The old grace model
            # could leave a second, older live token beside it; presenting
            # that one is treated as a replay, never rotated into a collision.
            if presented.id != _newest_live_token_id(session=session, sign_in=sign_in):
                _end_session_on_replay(session=session, sign_in=sign_in, now=moment)
            session.execute(
                update(RefreshTokenRow)
                .where(RefreshTokenRow.id == presented.id)
                .values(revoked_at=moment)
                .execution_options(synchronize_session=False)
            )
            try:
                return _issue_tokens(
                    session=session,
                    user=user,
                    tenant=tenant,
                    sign_in=sign_in,
                    generation=generation + 1,
                    now=moment,
                )
            except IntegrityError as exc:
                # A derived token colliding with one already stored: a
                # leftover of a model this code no longer runs. A refusal,
                # never a 500.
                raise AuthenticationError("refresh token is not valid") from exc

        reissued = _reissue_live_successor(
            session=session,
            sign_in=sign_in,
            user=user,
            spent_at=presented.revoked_at,
            generation=generation,
            now=moment,
            bearer=bearer,
        )
        if reissued is None:
            _end_session_on_replay(session=session, sign_in=sign_in, now=moment)
        return TokenPair(
            access_token=_access_token(user=user, tenant=tenant, sign_in=sign_in),
            refresh_token=reissued,
            expires_in=_access_token_ttl(),
        )


def logout(
    *,
    session: Session,
    caller: CurrentUser | None,
    refresh_token: str | None,
    now: datetime | None = None,
) -> None:
    """End one sign-in: its refresh tokens and every access token it issued.

    The session ended is the one the caller's access token names (``sid``)
    and, when the refresh cookie came too, the one its token belongs to:
    holding either is proof enough of that sign-in. Revoking the session row
    is what ends the access tokens: ``authenticate`` refuses any token whose
    session is revoked, from the next request on. Other sign-ins of the same
    account (another device) are left alone; ``logout_everywhere`` ends those.

    **Idempotent and silent.** An unknown, already-revoked, or expired token,
    or none at all, revokes nothing and is not an error, so the endpoint
    cannot be used to learn whether a token exists and a retry never fails.

    Args:
        session: Open database session. The caller commits.
        caller: The authenticated caller, when an access token came.
        refresh_token: The refresh cookie's token, when it came.
        now: Injected for tests.
    """
    moment = now or datetime.now(UTC)
    with cross_tenant(
        session, reason="a refresh cookie identifies its sign-in before any tenant is known"
    ):
        sessions: set[uuid.UUID] = set()
        if caller is not None and caller.session_id is not None:
            # A malformed sid was refused by `authenticate`; there is nothing to end.
            with contextlib.suppress(ValueError):
                sessions.add(uuid.UUID(caller.session_id))
        if refresh_token:
            owned = session.execute(
                select(RefreshTokenRow.session_id).where(
                    RefreshTokenRow.token_hash == hash_refresh_token(refresh_token)
                )
            ).scalar_one_or_none()
            if owned is not None:
                sessions.add(owned)
        if not sessions:
            return
        _revoke_sessions(session=session, condition=AuthSessionRow.id.in_(sessions), now=moment)


def logout_everywhere(
    *, session: Session, caller: CurrentUser, now: datetime | None = None
) -> None:
    """End every sign-in of the caller's account, this one included.

    Args:
        session: Open database session, bound to the caller's tenant. The
            caller commits.
        caller: The authenticated caller.
        now: Injected for tests.

    Raises:
        AuthenticationError: If the caller is a trial, which has no account
            and no sign-ins to end.
    """
    user_id = known_user_id(session=session, subject=caller.id)
    if user_id is None:
        raise AuthenticationError("a trial has no sign-ins to end", code="session_ended")
    moment = now or datetime.now(UTC)
    _revoke_sessions(session=session, condition=AuthSessionRow.user_id == user_id, now=moment)
    # Tokens from before sessions existed have no session to be ended with.
    session.execute(
        update(RefreshTokenRow)
        .where(RefreshTokenRow.user_id == user_id, RefreshTokenRow.revoked_at.is_(None))
        .values(revoked_at=moment)
        .execution_options(synchronize_session=False)
    )


def _revoke_sessions(*, session: Session, condition: ColumnElement[bool], now: datetime) -> None:
    """Revoke the sessions matching ``condition`` and all their refresh tokens.

    The session rows are locked first, in id order (`_lock_sessions`), which
    is what a concurrent ``refresh`` waits on. By the time the tokens are revoked,
    any rotation that won the lock has committed its new token, and this
    statement (a new snapshot under READ COMMITTED) revokes that one too.

    Args:
        session: Open database session.
        condition: Which ``auth_sessions`` rows to end.
        now: The revocation's timestamp.
    """
    _lock_sessions(session=session, condition=condition)
    session.execute(
        update(AuthSessionRow)
        .where(condition, AuthSessionRow.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    # Every session the condition names, ended now or before, so a retried
    # logout still sweeps up any token a racing refresh managed to mint.
    targets = session.execute(select(AuthSessionRow.id).where(condition)).scalars().all()
    if not targets:
        return
    session.execute(
        update(RefreshTokenRow)
        .where(RefreshTokenRow.session_id.in_(targets), RefreshTokenRow.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )


def _reissue_live_successor(
    *,
    session: Session,
    sign_in: AuthSessionRow,
    user: User,
    spent_at: datetime | None,
    generation: int,
    now: datetime,
    bearer: CurrentUser | None,
) -> str | None:
    """The session's live token again, for its immediate predecessor.

    Two ways to qualify, both only for the token whose rotation minted the
    live one (one generation behind), in a live session:

    * **Cookie alone:** spent within ``REFRESH_REUSE_GRACE`` (10 s): tabs that
      raced the rotation.
    * **Cookie and the session's own access token:** spent within an access
      token's lifetime, and the request carries an unexpired access token
      whose ``sid`` is this session and whose ``sub`` its user. A tab whose
      jar a late Set-Cookie moved back to the predecessor (D1) still holds
      such a token. A cookie-only holder (a thief with a copied cookie) still
      gets only the 10 s.

    Args:
        session: Open database session, holding the session's lock.
        sign_in: The presented token's session, live.
        user: The session's account.
        spent_at: When the presented token was spent.
        generation: The presented token's generation.
        now: The refresh attempt's timestamp.
        bearer: The verified access token that came with the request, if any.

    Returns:
        The live token's value, re-derived, when the presented token is the one
        whose rotation minted it and qualifies as above; otherwise ``None``
        (a replay). Nothing is written: the session keeps its one live token.

    Raises:
        AuthenticationError: When the predecessor qualifies but the live token
            cannot be re-derived (minted at random before derivation, or under
            an earlier derivation key). Refused without ending the session:
            this is an older token, not a sign of theft.
    """
    if spent_at is None:
        return None
    age = now - spent_at
    proven = (
        bearer is not None
        and bearer.session_id == str(sign_in.id)
        and bearer.id == str(user.id)
        and age <= timedelta(seconds=_access_token_ttl())
    )
    if age > REFRESH_REUSE_GRACE and not proven:
        return None
    live = session.execute(
        select(RefreshTokenRow.token_hash, RefreshTokenRow.generation, RefreshTokenRow.expires_at)
        .where(RefreshTokenRow.session_id == sign_in.id, RefreshTokenRow.revoked_at.is_(None))
        .order_by(RefreshTokenRow.created_at.desc(), RefreshTokenRow.id.desc())
        .limit(1)
    ).one_or_none()
    if live is None or live.expires_at <= now:
        return None
    if (live.generation or 0) != generation + 1:
        return None
    if sign_in.token_salt is not None and live.generation is not None:
        token, token_hash = derive_refresh_token(
            session_id=str(sign_in.id), salt=sign_in.token_salt, generation=live.generation
        )
        if secrets.compare_digest(token_hash, live.token_hash):
            return token
    raise AuthenticationError(
        "this sign-in cannot be renewed from here; sign in again", code="session_ended"
    )


def _newest_live_token_id(*, session: Session, sign_in: AuthSessionRow) -> uuid.UUID | None:
    """The id of the session's newest live refresh token.

    Args:
        session: Open database session.
        sign_in: The session.

    Returns:
        The newest live token's id, or ``None`` when it has none.
    """
    newest: uuid.UUID | None = session.execute(
        select(RefreshTokenRow.id)
        .where(RefreshTokenRow.session_id == sign_in.id, RefreshTokenRow.revoked_at.is_(None))
        .order_by(RefreshTokenRow.created_at.desc(), RefreshTokenRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return newest


def _end_session_on_replay(*, session: Session, sign_in: AuthSessionRow, now: datetime) -> NoReturn:
    """End the session a replayed token belongs to, and refuse.

    Only this session: the account's other sign-ins are other token families.

    Args:
        session: Open database session, holding the session's lock.
        sign_in: The replayed token's session.
        now: The refresh attempt's timestamp.

    Raises:
        AuthenticationError: Always.
    """
    _revoke_sessions(session=session, condition=AuthSessionRow.id == sign_in.id, now=now)
    # Committed here, against the usual "the caller commits": the refusal
    # below makes the request's session roll back, and a revocation that is
    # rolled back is a detection that protects nobody. Nothing else is
    # pending in this transaction, so this commits the revocation only.
    session.commit()
    # Ids only: the token, its hash, and the caller's address are not needed
    # to investigate, and a log is the wrong place for any of them.
    logger.warning(
        "auth.refresh_token_reused", user_id=str(sign_in.user_id), session_id=str(sign_in.id)
    )
    raise AuthenticationError("refresh token is not valid", code="session_ended")


def _access_token_ttl() -> int:
    from app.core.config import get_settings

    return get_settings().access_token_ttl_seconds


def _access_token(*, user: User, tenant: TenantRow, sign_in: AuthSessionRow) -> str:
    return create_access_token(
        subject=str(user.id),
        tenant_id=str(tenant.id),
        # A hint for the client only. Authorization re-reads the account's
        # roles on every request (`authenticate`), so this cannot grant
        # anything, and a revocation does not wait for it to expire.
        roles=roles_of(user),
        session_id=str(sign_in.id),
    )


def _issue_tokens(
    *,
    session: Session,
    user: User,
    tenant: TenantRow,
    sign_in: AuthSessionRow | None = None,
    generation: int = 1,
    now: datetime | None = None,
) -> TokenPair:
    """Mint an access token and the session's next refresh token.

    Args:
        session: Open database session.
        user: The authenticated user.
        tenant: The tenant they belong to.
        sign_in: The session the pair continues. ``None`` at login and signup,
            which start a new one.
        generation: The new refresh token's place in the session's chain.
        now: The issuing moment; defaults to the clock.

    Returns:
        The token pair to hand back to the client.
    """
    moment = now or datetime.now(UTC)
    if sign_in is None:
        sign_in = AuthSessionRow(tenant_id=tenant.id, user_id=user.id)
        session.add(sign_in)
    if sign_in.token_salt is None:
        sign_in.token_salt = generate_session_salt()
    session.flush()

    token, token_hash = derive_refresh_token(
        session_id=str(sign_in.id), salt=sign_in.token_salt, generation=generation
    )
    session.add(
        RefreshTokenRow(
            tenant_id=tenant.id,
            user_id=user.id,
            session_id=sign_in.id,
            token_hash=token_hash,
            generation=generation,
            expires_at=moment + REFRESH_TOKEN_TTL,
        )
    )
    session.flush()

    return TokenPair(
        access_token=_access_token(user=user, tenant=tenant, sign_in=sign_in),
        refresh_token=token,
        expires_in=_access_token_ttl(),
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
    _require_live_sign_in(session=session, caller=caller, user=user)
    return caller.model_copy(update={"roles": roles_of(user), "email": user.email})


def _require_live_sign_in(*, session: Session, caller: CurrentUser, user: User) -> None:
    """Refuse an account's access token whose sign-in has been ended.

    One primary-key read per request, alongside the account read
    ``resolve_caller`` already makes, is what makes a logout end the access
    token at once rather than at its ``exp``.

    **A token with no ``sid`` is refused.** Every account token issued since
    sign-in sessions exist carries one; one without it predates them, names
    no session a logout could end, and would otherwise be the one token a
    logout cannot reach. Refusing costs an account signed in at the deploy one
    more sign-in, at most an access-token lifetime early.

    Args:
        session: Open database session, bound to the caller's tenant.
        caller: The decoded caller.
        user: The live account the token names.

    Raises:
        AuthenticationError: If the token has no session, or the session is
            unknown, another account's, or revoked.
    """
    if caller.session_id is None:
        raise AuthenticationError(
            "token predates sign-in sessions; sign in again", code="session_ended"
        )
    try:
        sid = uuid.UUID(caller.session_id)
    except ValueError as exc:
        raise AuthenticationError("token session is not valid", code="session_ended") from exc
    sign_in = session.get(AuthSessionRow, sid, populate_existing=True)
    if sign_in is None or sign_in.user_id != user.id or sign_in.revoked_at is not None:
        raise AuthenticationError("this sign-in has ended", code="session_ended")


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


def _lock_sessions(*, session: Session, condition: ColumnElement[bool]) -> list[AuthSessionRow]:
    """Lock the sessions matching ``condition``, always in id order.

    Every path that locks session rows (refresh, logout, logout everywhere,
    reuse detection) goes through here, so all of them take locks in one
    global order and two of them can never each hold a row the other waits on.

    Args:
        session: Open database session.
        condition: Which ``auth_sessions`` rows to lock.

    Returns:
        The rows, re-read under the lock: an identity-map copy from before it
        would hide a logout that committed while this waited.
    """
    return list(
        session.execute(
            select(AuthSessionRow)
            .where(condition)
            .order_by(AuthSessionRow.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
