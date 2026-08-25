"""Tests for `app/domain/auth.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Runs against a real Postgres. The two things BE-002 must guarantee — that the
(N+1)th free question is refused, and that a claimed anonymous session keeps
its history — are both about rows surviving a transaction, which a fake session
cannot demonstrate.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import (
    decode_access_token,
    generate_claim_secret,
    hash_refresh_token,
)
from app.domain import auth
from app.models.schemas.auth_flows import TokenPair
from app.models.tables import calculations, diagnostics, escalation, ingestion  # noqa: F401
from app.models.tables.session import AnonymousSessionRow, RefreshTokenRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

PASSWORD = "correct horse battery"

# Prefixes every tenant slug this module creates, so teardown can delete
# exactly its own rows and nothing another test module relies on.
_SLUG_PREFIX = "authtest-"


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture
def db() -> Iterator[Session]:
    """A real session, cleaned of anything this module created."""
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        # Delete only what THIS module created. An earlier version cleared
        # every tenant whose slug contained a hyphen, which also removed the
        # promotion tests' fixture data and made the two modules fail when run
        # together but pass in isolation.
        session.execute(
            text(
                "DELETE FROM refresh_tokens WHERE tenant_id IN "
                "(SELECT id FROM tenants WHERE slug LIKE :p)"
            ),
            {"p": f"{_SLUG_PREFIX}%"},
        )
        session.execute(
            text(
                "DELETE FROM diagnostic_turns WHERE session_id IN (SELECT id FROM "
                "diagnostic_sessions WHERE tenant_id IN "
                "(SELECT id FROM tenants WHERE slug LIKE :p))"
            ),
            {"p": f"{_SLUG_PREFIX}%"},
        )
        for table in ("anonymous_sessions", "diagnostic_sessions", "calculation_records"):
            session.execute(
                text(
                    f"DELETE FROM {table} WHERE tenant_id IN "
                    "(SELECT id FROM tenants WHERE slug LIKE :p)"
                ),
                {"p": f"{_SLUG_PREFIX}%"},
            )
        session.execute(text("DELETE FROM users WHERE email LIKE '%@test.invalid'"))
        session.execute(text("DELETE FROM tenants WHERE slug LIKE :p"), {"p": f"{_SLUG_PREFIX}%"})
        session.commit()
        session.close()


def _email() -> str:
    # The slug signup() derives starts with this, so teardown matches it.
    return f"{_SLUG_PREFIX}{uuid.uuid4().hex[:8]}@test.invalid"


# --- signup and the implicit tenant ----------------------------------------


@requires_db
def test_signup_creates_an_implicit_single_user_tenant(db: Session) -> None:
    """The schema never special-cases "no tenant", so signup must create one."""
    email = _email()
    tokens = auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()

    caller = decode_access_token(tokens.access_token)
    user = db.execute(text("SELECT tenant_id FROM users WHERE email = :e"), {"e": email}).one()
    assert str(user.tenant_id) == caller.tenant_id
    assert db.get(TenantRow, uuid.UUID(caller.tenant_id)) is not None


@requires_db
def test_the_access_token_carries_the_tenant(db: Session) -> None:
    """Isolation travels with the request rather than being looked up per query."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    assert decode_access_token(tokens.access_token).tenant_id


@requires_db
def test_two_signups_get_separate_tenants(db: Session) -> None:
    """The whole point of the boundary."""
    a = auth.signup(session=db, email=_email(), password=PASSWORD)
    b = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    assert (
        decode_access_token(a.access_token).tenant_id
        != decode_access_token(b.access_token).tenant_id
    )


@requires_db
def test_duplicate_email_is_refused(db: Session) -> None:
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()
    with pytest.raises(ValidationError, match="already registered"):
        auth.signup(session=db, email=email, password=PASSWORD)


@requires_db
def test_email_is_normalised_before_uniqueness_is_checked(db: Session) -> None:
    """Otherwise Alice@x and alice@x are two accounts for one address."""
    email = _email()
    auth.signup(session=db, email=email.upper(), password=PASSWORD)
    db.commit()
    with pytest.raises(ValidationError, match="already registered"):
        auth.signup(session=db, email=email, password=PASSWORD)


# --- login ------------------------------------------------------------------


@requires_db
def test_login_returns_tokens_for_correct_credentials(db: Session) -> None:
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()
    tokens = auth.login(session=db, email=email, password=PASSWORD)
    db.commit()
    assert decode_access_token(tokens.access_token).id


@requires_db
def test_wrong_password_and_unknown_email_are_indistinguishable(db: Session) -> None:
    """The error must not reveal which addresses have accounts."""
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()

    with pytest.raises(AuthenticationError) as wrong:
        auth.login(session=db, email=email, password="not the password")
    with pytest.raises(AuthenticationError) as unknown:
        auth.login(session=db, email=_email(), password=PASSWORD)
    assert str(wrong.value) == str(unknown.value)


@requires_db
def test_inactive_account_cannot_log_in(db: Session) -> None:
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()
    db.execute(text("UPDATE users SET is_active = false WHERE email = :e"), {"e": email})
    db.commit()
    with pytest.raises(AuthenticationError):
        auth.login(session=db, email=email, password=PASSWORD)


# --- refresh rotation -------------------------------------------------------


@requires_db
def test_refresh_rotates_and_revokes_the_presented_token(db: Session) -> None:
    """A refresh token is single-use; reuse is a replay."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()

    rotated = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    assert rotated.refresh_token != tokens.refresh_token

    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)


@requires_db
def test_refresh_tokens_are_never_stored_in_the_clear(db: Session) -> None:
    """A leaked dump must not hand over working credentials."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    rows = db.execute(text("SELECT token_hash FROM refresh_tokens")).scalars().all()
    assert tokens.refresh_token not in rows
    assert hash_refresh_token(tokens.refresh_token) in rows


@requires_db
def test_expired_refresh_token_is_refused(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    row = db.execute(
        text("SELECT id FROM refresh_tokens WHERE token_hash = :h"),
        {"h": hash_refresh_token(tokens.refresh_token)},
    ).one()
    db.execute(
        text("UPDATE refresh_tokens SET expires_at = :e WHERE id = :i"),
        {"e": datetime.now(UTC) - timedelta(days=1), "i": row.id},
    )
    db.commit()
    with pytest.raises(AuthenticationError, match="expired"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)


# --- the free-tier quota, which the spec names explicitly -------------------


def _tenant_of(tokens: TokenPair) -> str:
    return decode_access_token(tokens.access_token).tenant_id


@requires_db
def test_the_n_plus_first_free_question_is_rejected(db: Session) -> None:
    """The acceptance criterion, asserted directly.

    Consumes exactly the allowance, then checks the next one is refused — and
    that the refusal happens on the check, not by the counter running past the
    limit.
    """
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    tenant_id = _tenant_of(tokens)
    limit = auth.get_quota(session=db, tenant_id=tenant_id).question_limit

    for _ in range(limit):
        auth.consume_free_question(session=db, tenant_id=tenant_id)
    db.commit()

    quota = auth.get_quota(session=db, tenant_id=tenant_id)
    assert quota.questions_used == limit
    assert quota.questions_remaining == 0

    # The enforcement point is consume, not the advisory check.
    with pytest.raises(ValidationError, match="free question limit"):
        auth.consume_free_question(session=db, tenant_id=tenant_id)
    db.rollback()
    with pytest.raises(ValidationError, match="free question limit"):
        auth.check_free_question_allowed(session=db, tenant_id=tenant_id)


@requires_db
def test_the_counter_is_server_side_not_client_reported(db: Session) -> None:
    """Nothing in the interface accepts a count from the caller.

    A client-supplied figure is a number the client chooses, so the only way to
    move the counter must be to actually consume a question.
    """
    import inspect

    for fn in (auth.check_free_question_allowed, auth.consume_free_question):
        params = set(inspect.signature(fn).parameters)
        assert params <= {
            "session",
            "tenant_id",
        }, f"{fn.__name__} accepts {params}; a caller-supplied count would be trusted"


@requires_db
def test_usage_is_counted_per_tenant_not_globally(db: Session) -> None:
    """One tenant exhausting the tier must not block another."""
    a = auth.signup(session=db, email=_email(), password=PASSWORD)
    b = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    tenant_a, tenant_b = _tenant_of(a), _tenant_of(b)

    limit = auth.get_quota(session=db, tenant_id=tenant_a).question_limit
    for _ in range(limit):
        auth.consume_free_question(session=db, tenant_id=tenant_a)
    db.commit()

    with pytest.raises(ValidationError):
        auth.consume_free_question(session=db, tenant_id=tenant_a)
    db.rollback()
    # B is untouched.
    auth.check_free_question_allowed(session=db, tenant_id=tenant_b)
    assert auth.get_quota(session=db, tenant_id=tenant_b).questions_used == 0


# --- claiming an anonymous trial session ------------------------------------


def _start_anonymous_session(db: Session) -> tuple[str, str, str]:
    """Create a provisional tenant, a diagnostic session, and one turn.

    Returns:
        ``(anonymous_session_id, claim_secret, question_text)``.
    """
    tenant = TenantRow(slug=f"{_SLUG_PREFIX}trial-{uuid.uuid4().hex[:8]}", name="Trial")
    db.add(tenant)
    db.flush()
    session_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO diagnostic_sessions (id, tenant_id, user_id, created_at, updated_at) "
            "VALUES (:i, :t, NULL, now(), now())"
        ),
        {"i": session_id, "t": tenant.id},
    )
    question = "drive trips on overcurrent at start"
    db.execute(
        text(
            "INSERT INTO diagnostic_turns (id, session_id, position, question, answer, "
            "created_at, updated_at) VALUES (:i, :s, 1, :q, 'a', now(), now())"
        ),
        {"i": uuid.uuid4(), "s": session_id, "q": question},
    )
    secret, secret_hash = generate_claim_secret()
    anon = AnonymousSessionRow(
        tenant_id=tenant.id,
        diagnostic_session_id=session_id,
        claim_secret_hash=secret_hash,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(anon)
    db.flush()
    return str(anon.id), secret, question


@requires_db
def test_a_claimed_session_keeps_its_history(db: Session) -> None:
    """The acceptance criterion's second half.

    The conversation started before signup must still be readable after, under
    the new account's tenant — not restarted.
    """
    anon_id, secret, question = _start_anonymous_session(db)
    db.commit()

    tokens = auth.signup(
        session=db,
        email=_email(),
        password=PASSWORD,
        claim_session_id=anon_id,
        claim_secret=secret,
    )
    db.commit()

    tenant_id = _tenant_of(tokens)
    rows = (
        db.execute(
            text(
                "SELECT t.question FROM diagnostic_turns t "
                "JOIN diagnostic_sessions s ON s.id = t.session_id "
                "WHERE s.tenant_id = :t"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
        .scalars()
        .all()
    )
    assert question in rows, "the trial conversation was lost at signup"


@requires_db
def test_claiming_joins_the_sessions_existing_tenant(db: Session) -> None:
    """Nothing is copied or re-pointed, so there is no half-migrated state."""
    anon_id, secret, _ = _start_anonymous_session(db)
    anon = db.get(AnonymousSessionRow, uuid.UUID(anon_id))
    assert anon is not None
    provisional_tenant = str(anon.tenant_id)
    db.commit()

    tokens = auth.signup(
        session=db,
        email=_email(),
        password=PASSWORD,
        claim_session_id=anon_id,
        claim_secret=secret,
    )
    db.commit()
    assert _tenant_of(tokens) == provisional_tenant


@requires_db
def test_a_session_cannot_be_claimed_twice(db: Session) -> None:
    """Otherwise history moves between accounts."""
    anon_id, secret, _ = _start_anonymous_session(db)
    db.commit()
    auth.signup(
        session=db,
        email=_email(),
        password=PASSWORD,
        claim_session_id=anon_id,
        claim_secret=secret,
    )
    db.commit()

    with pytest.raises(ValidationError, match="already been claimed"):
        auth.signup(
            session=db,
            email=_email(),
            password=PASSWORD,
            claim_session_id=anon_id,
            claim_secret=secret,
        )


@requires_db
def test_claiming_an_unknown_session_is_not_found(db: Session) -> None:
    with pytest.raises(NotFoundError):
        auth.signup(
            session=db,
            email=_email(),
            password=PASSWORD,
            claim_session_id=str(uuid.uuid4()),
            claim_secret="anything",
        )


@requires_db
def test_an_expired_trial_session_cannot_be_claimed(db: Session) -> None:
    anon_id, secret, _ = _start_anonymous_session(db)
    anon = db.get(AnonymousSessionRow, uuid.UUID(anon_id))
    assert anon is not None
    # Expiry is derived from expires_at, not a flag: a stored boolean nothing
    # ever set meant trial sessions stayed claimable forever.
    anon.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    with pytest.raises(ValidationError, match="expired"):
        auth.signup(
            session=db,
            email=_email(),
            password=PASSWORD,
            claim_session_id=anon_id,
            claim_secret=secret,
        )


# --- caller resolution ------------------------------------------------------


@requires_db
def test_a_token_whose_tenant_no_longer_matches_is_rejected(db: Session) -> None:
    """A stale or forged tenant claim must not grant access to old data."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    caller = decode_access_token(tokens.access_token)

    other = TenantRow(slug=f"{_SLUG_PREFIX}other-{uuid.uuid4().hex[:8]}", name="Other")
    db.add(other)
    db.commit()
    db.execute(
        text("UPDATE users SET tenant_id = :t WHERE id = :i"),
        {"t": other.id, "i": uuid.UUID(caller.id)},
    )
    db.commit()

    with pytest.raises(AuthenticationError, match="tenant does not match"):
        auth.resolve_caller(session=db, caller=caller)


@requires_db
def test_a_deactivated_user_is_rejected_before_their_token_expires(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    caller = decode_access_token(tokens.access_token)
    db.execute(
        text("UPDATE users SET is_active = false WHERE id = :i"), {"i": uuid.UUID(caller.id)}
    )
    db.commit()

    with pytest.raises(AuthenticationError, match="not active"):
        auth.resolve_caller(session=db, caller=caller)


@requires_db
def test_resolve_caller_returns_the_live_user(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    caller = decode_access_token(tokens.access_token)
    user = auth.resolve_caller(session=db, caller=caller)
    assert isinstance(user, User)
    assert str(user.id) == caller.id


@requires_db
def test_refresh_token_rows_are_tenant_scoped(db: Session) -> None:
    """Every issued token is attributable to a tenant."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    # Scoped to THIS signup: other tests in this module leave rows behind, so
    # an unqualified query is order-dependent.
    row = db.execute(
        text("SELECT tenant_id FROM refresh_tokens WHERE token_hash = :h"),
        {"h": hash_refresh_token(tokens.refresh_token)},
    ).one()
    assert row.tenant_id is not None
    assert str(row.tenant_id) == _tenant_of(tokens)
    assert db.query(RefreshTokenRow).count() >= 1


# --- the defects review demonstrated live -----------------------------------


@requires_db
def test_a_session_id_alone_cannot_claim_a_tenant(db: Session) -> None:
    """Regression: this was a cross-tenant account takeover.

    The session id travels in URLs and is not secret. Accepting it as the sole
    credential let anyone who learned one sign up straight into that session's
    tenant as a full engineer — reading its diagnostic history and spending its
    quota. The claim now requires the secret issued when the trial started.
    """
    anon_id, _secret, _ = _start_anonymous_session(db)
    db.commit()

    with pytest.raises(AuthenticationError, match="cannot be claimed"):
        auth.signup(session=db, email=_email(), password=PASSWORD, claim_session_id=anon_id)
    db.rollback()

    with pytest.raises(AuthenticationError, match="cannot be claimed"):
        auth.signup(
            session=db,
            email=_email(),
            password=PASSWORD,
            claim_session_id=anon_id,
            claim_secret="not-the-secret",
        )


@requires_db
def test_a_tenant_that_already_has_users_cannot_be_claimed(db: Session) -> None:
    """Defence in depth behind the secret.

    Even with a valid secret, a tenant holding real accounts is not a trial
    being claimed — it is an attempt to join somebody's existing account.
    """
    anon_id, secret, _ = _start_anonymous_session(db)
    anon = db.get(AnonymousSessionRow, uuid.UUID(anon_id))
    assert anon is not None
    db.add(
        User(
            tenant_id=anon.tenant_id,
            email=f"{_SLUG_PREFIX}incumbent@test.invalid",
            is_active=True,
        )
    )
    db.commit()

    with pytest.raises(ValidationError, match="existing account"):
        auth.signup(
            session=db,
            email=_email(),
            password=PASSWORD,
            claim_session_id=anon_id,
            claim_secret=secret,
        )


@requires_db
def test_the_quota_holds_under_concurrency(db: Session) -> None:
    """Regression: the limit was advisory, not real.

    check_free_question_allowed read without a lock in a separate call from the
    increment, so concurrent requests all saw "allowed" and all incremented.
    Review measured 15 questions served against a limit of 5. The check now
    happens inside the same locked transaction as the increment.
    """
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings

    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    tenant_id = _tenant_of(tokens)
    limit = 5
    db.execute(
        text("UPDATE tenants SET free_question_limit = :l WHERE id = :i"),
        {"l": limit, "i": uuid.UUID(tenant_id)},
    )
    db.commit()

    engine = create_engine(get_settings().database_url.get_secret_value())
    factory = sessionmaker(bind=engine)

    def attempt() -> bool:
        own = factory()
        try:
            auth.consume_free_question(session=own, tenant_id=tenant_id)
            own.commit()
            return True
        except ValidationError:
            own.rollback()
            return False
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=20) as pool:
        granted = sum(pool.map(lambda _: attempt(), range(20)))

    db.expire_all()
    used = db.execute(
        text("SELECT free_questions_used FROM tenants WHERE id = :i"),
        {"i": uuid.UUID(tenant_id)},
    ).scalar_one()
    assert granted == limit, f"{granted} questions granted against a limit of {limit}"
    assert used == limit, f"counter reached {used}, past the limit of {limit}"


@requires_db
def test_concurrent_claims_cannot_all_succeed(db: Session) -> None:
    """Regression: six concurrent signups all joined the same tenant.

    The claimed check was an unlocked read, so every racer saw
    claimed_by_user_id IS NULL. The row is now locked FOR UPDATE.
    """
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings

    anon_id, secret, _ = _start_anonymous_session(db)
    db.commit()

    engine = create_engine(get_settings().database_url.get_secret_value())
    factory = sessionmaker(bind=engine)

    def attempt(_n: int) -> bool:
        own = factory()
        try:
            auth.signup(
                session=own,
                email=_email(),
                password=PASSWORD,
                claim_session_id=anon_id,
                claim_secret=secret,
            )
            own.commit()
            return True
        except Exception:
            own.rollback()
            return False
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        claimed = sum(pool.map(attempt, range(6)))

    assert claimed == 1, f"{claimed} accounts claimed the same trial session"


@requires_db
def test_expiry_is_derived_rather_than_a_flag(db: Session) -> None:
    """A stored boolean nothing ever set meant trials never expired."""
    anon_id, _secret, _ = _start_anonymous_session(db)
    anon = db.get(AnonymousSessionRow, uuid.UUID(anon_id))
    assert anon is not None
    assert not anon.is_expired
    anon.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert anon.is_expired, "expiry must follow expires_at, not a stored flag"
