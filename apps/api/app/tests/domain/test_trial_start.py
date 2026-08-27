"""Tests for starting an anonymous trial.

The endpoint that makes FE-008's landing flow possible: no form, no
credentials, one POST, and the visitor can ask a question.

Everything worth testing here is a security property rather than a feature.
A trial hands an unauthenticated stranger a working access token, so the
questions are: what can that token do, can it be escalated, and can the claim
secret be replayed or recovered. The happy path is one test; the rest of this
file is the boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import DateTime, TypeDecorator, create_engine, select
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthenticationError, ValidationError
from app.core.security import decode_access_token, hash_claim_secret
from app.domain.auth import TRIAL_TTL, resolve_caller, signup, start_trial
from app.models.schemas.auth import Role

# Imported for their side effect on `Base.metadata`: `create_all` resolves
# every foreign key across the whole collection, so a table referencing
# `users` fails unless that model has been imported.
from app.models.tables import diagnostics as _diagnostics  # noqa: F401
from app.models.tables import escalation as _escalation  # noqa: F401
from app.models.tables import ingestion as _ingestion  # noqa: F401
from app.models.tables import session as _session_tables  # noqa: F401
from app.models.tables import tenant as _tenant  # noqa: F401
from app.models.tables import user as _user  # noqa: F401
from app.models.tables.base import Base
from app.models.tables.diagnostics import DiagnosticSessionRow
from app.models.tables.session import AnonymousSessionRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give the signup and login paths a configured environment.

    `_issue_tokens` reads `get_settings()` for the token lifetime and the
    signing key. Set as environment variables rather than by patching the
    accessor, because `app.domain.auth` imports `get_settings` *inside* the
    functions that use it — so it resolves the real one on every call and a
    patched module attribute never takes effect.

    Priming it here also keeps the suite independent of whatever `.env` the
    developer happens to have; the alternative is tests that pass on one
    machine and fail on another for reasons unrelated to the code.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
    monkeypatch.setenv("OPENSEARCH_URL", "http://localhost:9200")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "test-secret-at-least-32-bytes-long-ok")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    # Cached with lru_cache, so a value read before these were set would stick.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _UTCDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` that reads back UTC-aware, the way Postgres does.

    SQLite has no timezone-aware storage: it accepts an aware datetime, drops
    the offset, and returns it naive — so `row.expires_at <= datetime.now(UTC)`
    in the claim path raises `TypeError`. Postgres, which is what actually
    runs, honours `DateTime(timezone=True)`.

    Correcting the stand-in rather than the code under test: the alternative is
    making the domain defensive about a database the product never uses.
    """

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reattach UTC to a value SQLite stored without an offset.

        Args:
            value: The datetime as the driver returned it.
            dialect: The active dialect. Unused.

        Returns:
            The same instant, timezone-aware, or ``None``.
        """
        del dialect
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


@pytest.fixture(name="session")
def _session() -> Iterator[Session]:
    """An in-memory database with the real schema."""
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                column.type = _UTCDateTime(timezone=True)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


# --- what a trial actually creates --------------------------------------------


def test_a_trial_returns_everything_the_client_needs(session: Session) -> None:
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    assert trial.session_id
    assert trial.claim_secret
    assert trial.access_token
    assert trial.questions_remaining > 0


def test_the_payload_matches_the_field_names_the_client_reads(session: Session) -> None:
    # FE-008's `readStartPayload` reads `session_id` and `claim_secret` by
    # those exact snake_case names and returns `failed` if either is missing.
    # Renaming one here would be a silent break: the endpoint would answer 201
    # and the landing page would report a failure.
    payload = start_trial(session=session, access_token_ttl_seconds=3600).model_dump()

    assert "session_id" in payload
    assert "claim_secret" in payload
    assert isinstance(payload["session_id"], str)
    assert isinstance(payload["claim_secret"], str)


def test_a_trial_creates_its_own_tenant(session: Session) -> None:
    # Signup later joins the real account to *this* tenant rather than making
    # another, which is what lets the conversation survive the claim without
    # copying a row.
    start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    tenants = session.execute(select(TenantRow)).scalars().all()
    assert len(tenants) == 1
    assert tenants[0].slug.startswith("trial-")


def test_two_trials_do_not_share_a_tenant(session: Session) -> None:
    # Sharing one would put two strangers' conversations in the same isolation
    # boundary, and let either claim the other's tenant at signup.
    first = start_trial(session=session, access_token_ttl_seconds=3600)
    second = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    tenants = session.execute(select(TenantRow)).scalars().all()
    assert len({t.id for t in tenants}) == 2
    assert first.session_id != second.session_id


def test_a_trial_creates_a_diagnostic_session_to_talk_into(session: Session) -> None:
    start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    # Reached through the anonymous session, because `session_id` in the
    # payload is the anonymous session's id — that is what the claim path
    # looks up.
    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    assert session.get(DiagnosticSessionRow, row.diagnostic_session_id) is not None


def test_the_anonymous_session_points_at_that_diagnostic_session(session: Session) -> None:
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    # `session_id` is the anonymous row's own id; the diagnostic session it
    # points at is a different id and is not what signup sends back.
    assert str(row.id) == trial.session_id
    assert str(row.diagnostic_session_id) != trial.session_id


# --- the claim secret ---------------------------------------------------------


def test_only_the_hash_of_the_claim_secret_is_stored(session: Session) -> None:
    # The secret is a bearer credential for the trial's whole tenant. Storing
    # it in plaintext would make a database read a takeover of every
    # unclaimed trial at once.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    assert row.claim_secret_hash != trial.claim_secret
    assert row.claim_secret_hash == hash_claim_secret(trial.claim_secret)


def test_two_trials_get_different_secrets(session: Session) -> None:
    # A predictable or reused secret would let anyone who started one trial
    # claim another.
    secrets_seen = {
        start_trial(session=session, access_token_ttl_seconds=3600).claim_secret for _ in range(5)
    }
    session.commit()

    assert len(secrets_seen) == 5


def test_the_secret_is_long_enough_to_resist_guessing(session: Session) -> None:
    # 32 bytes, URL-safe. Short enough to fit anywhere, long enough that
    # guessing is not a strategy.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)

    assert len(trial.claim_secret) >= 32


def test_the_session_expires_rather_than_staying_claimable_forever(session: Session) -> None:
    # An abandoned trial whose secret leaks should not be claimable
    # indefinitely.
    start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    assert row.expires_at is not None
    assert row.claimed_by_user_id is None


def test_the_expiry_is_the_documented_window(session: Session) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    start_trial(session=session, now=now, access_token_ttl_seconds=3600)
    session.commit()

    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    stored = row.expires_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert stored == now + TRIAL_TTL


# --- the placeholder user, and what its token may do --------------------------


def test_the_trial_tenant_has_no_user(session: Session) -> None:
    # The invariant `_load_claimable_session` enforces: "a provisional trial
    # tenant has no users. If it has any, this is not a trial being claimed —
    # it is an attempt to join somebody's existing account."
    #
    # An earlier version of this endpoint created a placeholder user so the
    # token had a subject to resolve. It authenticated fine and made every
    # trial permanently unclaimable, which is the one thing the pair exists to
    # allow. The existing guard caught it.
    start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    assert session.execute(select(User)).scalars().all() == []


def test_the_token_subject_is_the_anonymous_session(session: Session) -> None:
    # There is no user to name, so the subject is the row that does exist.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    caller = decode_access_token(trial.access_token)
    assert caller.id == trial.session_id


def test_a_trial_token_resolves(session: Session) -> None:
    # The liveness check every diagnostics route runs. Without this a trial
    # would answer 201 and then 401 on the first question.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    caller = decode_access_token(trial.access_token)
    assert resolve_caller(session=session, caller=caller) is None


def test_the_token_is_scoped_to_the_trials_own_tenant(session: Session) -> None:
    # The isolation boundary. A token carrying another tenant would let a
    # stranger read a real customer's sessions.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    caller = decode_access_token(trial.access_token)
    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    assert caller.tenant_id == str(row.tenant_id)


def test_a_trial_token_claiming_another_tenant_is_refused(session: Session) -> None:
    # Forged or stale. Trusting it would cross the isolation boundary.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    caller = decode_access_token(trial.access_token)
    forged = caller.model_copy(update={"tenant_id": str(uuid.uuid4())})

    with pytest.raises(AuthenticationError):
        resolve_caller(session=session, caller=forged)


def test_an_expired_trial_token_is_refused(session: Session) -> None:
    # Enforced against the row, not just the JWT: the trial's expiry is the
    # only thing bounding how long an abandoned conversation stays reachable,
    # and a token could otherwise outlive it.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    caller = decode_access_token(trial.access_token)
    with pytest.raises(AuthenticationError):
        resolve_caller(session=session, caller=caller)


def test_a_claimed_trials_token_stops_working(session: Session) -> None:
    # After the claim the tenant belongs to a real user. Honouring the trial
    # token afterwards would leave a second, unrevocable credential on
    # somebody's actual account.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    signup(
        session=session,
        email="engineer@example.com",
        password="a-long-password-1",
        claim_session_id=trial.session_id,
        claim_secret=trial.claim_secret,
    )
    session.commit()

    caller = decode_access_token(trial.access_token)
    with pytest.raises(AuthenticationError):
        resolve_caller(session=session, caller=caller)


def test_an_unknown_subject_is_refused(session: Session) -> None:
    # Neither a user nor a trial. Must not fall through to "allowed".
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    caller = decode_access_token(trial.access_token)
    unknown = caller.model_copy(update={"id": str(uuid.uuid4())})

    with pytest.raises(AuthenticationError):
        resolve_caller(session=session, caller=unknown)


def test_the_trial_holds_only_the_engineer_role(session: Session) -> None:
    # Not reviewer, not admin, not ingestion. A stranger with a POST must not
    # be able to promote content or read another tenant's escalations.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)

    caller = decode_access_token(trial.access_token)
    assert caller.roles == frozenset({Role.ENGINEER})


def test_a_real_signup_can_still_use_any_address(session: Session) -> None:
    # The placeholder must not consume an address a person might want. Two
    # trials plus a real signup, with no unique-constraint collision.
    start_trial(session=session, access_token_ttl_seconds=3600)
    start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    tokens = signup(session=session, email="engineer@example.com", password="a-long-password-1")
    session.commit()

    assert tokens.access_token


# --- claiming, end to end -----------------------------------------------------


def test_a_trial_can_be_claimed_with_its_secret(session: Session) -> None:
    # The whole point of the pair: the conversation started before signup is
    # carried into the new account.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    tokens = signup(
        session=session,
        email="engineer@example.com",
        password="a-long-password-1",
        claim_session_id=trial.session_id,
        claim_secret=trial.claim_secret,
    )
    session.commit()

    assert tokens.access_token
    row = session.execute(select(AnonymousSessionRow)).scalar_one()
    assert row.claimed_by_user_id is not None


def test_the_claimed_account_lands_in_the_trials_tenant(session: Session) -> None:
    # Not a new tenant. If signup made its own, the trial's conversation would
    # be stranded under a tenant the user is not in.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()
    trial_tenant = session.execute(select(TenantRow)).scalar_one().id

    signup(
        session=session,
        email="engineer@example.com",
        password="a-long-password-1",
        claim_session_id=trial.session_id,
        claim_secret=trial.claim_secret,
    )
    session.commit()

    user = session.execute(select(User).where(User.email == "engineer@example.com")).scalar_one()
    assert user.tenant_id == trial_tenant


def test_a_wrong_secret_cannot_claim_the_trial(session: Session) -> None:
    # The session id travels in URLs and request bodies. Without the secret,
    # learning one would be a takeover of that trial's tenant.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    with pytest.raises(AuthenticationError, match="cannot be claimed"):
        signup(
            session=session,
            email="attacker@example.com",
            password="a-long-password-1",
            claim_session_id=trial.session_id,
            claim_secret="not-the-real-secret",
        )


def test_a_secret_cannot_be_replayed_on_a_second_signup(session: Session) -> None:
    # Single use. Otherwise anyone who saw the secret could join the tenant
    # after the legitimate owner already had.
    trial = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    signup(
        session=session,
        email="first@example.com",
        password="a-long-password-1",
        claim_session_id=trial.session_id,
        claim_secret=trial.claim_secret,
    )
    session.commit()

    with pytest.raises(ValidationError, match="already been claimed"):
        signup(
            session=session,
            email="second@example.com",
            password="a-long-password-1",
            claim_session_id=trial.session_id,
            claim_secret=trial.claim_secret,
        )


def test_one_trials_secret_cannot_claim_another_trial(session: Session) -> None:
    # Cross-session replay. Each secret is bound to its own row.
    first = start_trial(session=session, access_token_ttl_seconds=3600)
    second = start_trial(session=session, access_token_ttl_seconds=3600)
    session.commit()

    with pytest.raises(AuthenticationError, match="cannot be claimed"):
        signup(
            session=session,
            email="attacker@example.com",
            password="a-long-password-1",
            claim_session_id=second.session_id,
            claim_secret=first.claim_secret,
        )
