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
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core import security as auth_security
from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import (
    decode_access_token,
    generate_claim_secret,
    hash_refresh_token,
)
from app.core.tenancy import cross_tenant_info
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


@pytest.fixture(autouse=True)
def _rotate_on_every_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests here are about rotation itself, so every refresh rotates.

    Production rotates only a live token older than ``auth.ROTATE_AFTER``;
    the tests of that rule ask for it with the ``real_rotation`` fixture.
    """
    from app.domain import auth as auth_domain

    monkeypatch.setattr(auth_domain, "ROTATE_AFTER", timedelta(0))


@pytest.fixture
def db() -> Iterator[Session]:
    """A real session, cleaned of anything this module created."""
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(
        bind=engine, info=cross_tenant_info("tests set up and inspect rows across tenants")
    )()
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

    # Past the window a concurrent refresh is allowed (see the grace tests).
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token, now=_after_grace())


@requires_db
def test_refresh_tokens_are_never_stored_in_the_clear(db: Session) -> None:
    """A leaked dump must not hand over working credentials."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    rows: Sequence[str] = db.execute(text("SELECT token_hash FROM refresh_tokens")).scalars().all()
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


def _after_grace() -> datetime:
    """A moment past the reuse window of a token rotated just now."""
    return datetime.now(UTC) + auth.REFRESH_REUSE_GRACE + timedelta(seconds=1)


def _live_refresh_tokens(db: Session, email: str) -> int:
    count: int = db.execute(
        text(
            "SELECT count(*) FROM refresh_tokens r JOIN users u ON u.id = r.user_id "
            "WHERE u.email = :e AND r.revoked_at IS NULL"
        ),
        {"e": email},
    ).scalar_one()
    return count


@requires_db
def test_replaying_a_stale_token_ends_only_its_own_session(db: Session) -> None:
    """A stale token only comes back if it was copied.

    Either the client or a thief already rotated it, and there is no telling
    which, so that sign-in is ended: the thief's copy and the client's alike.
    The account's other sign-ins are separate token families the copy never
    touched; they stay, and "log out everywhere" ends them on purpose.
    """
    email = _email()
    first = auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()
    # A second device's session, also live.
    phone = auth.login(session=db, email=email, password=PASSWORD)
    db.commit()
    rotated = auth.refresh(session=db, refresh_token=first.refresh_token)
    db.commit()
    assert _live_refresh_tokens(db, email) == 2

    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=first.refresh_token, now=_after_grace())
    # Rolled back, as the request's session would be on a 401. The revocation
    # must survive that: a detection that is rolled back protects nobody.
    db.rollback()

    with pytest.raises(AuthenticationError):
        auth.refresh(session=db, refresh_token=rotated.refresh_token)
    db.rollback()
    assert not _authenticates(db, rotated.access_token)
    # The phone's sign-in is untouched.
    assert _live_refresh_tokens(db, email) == 1
    assert _authenticates(db, phone.access_token)
    assert auth.refresh(session=db, refresh_token=phone.refresh_token).access_token


@requires_db
def test_an_unknown_refresh_token_revokes_nothing(db: Session) -> None:
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()

    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token="never-issued")
    db.rollback()

    assert _live_refresh_tokens(db, email) == 1


def _own_sessions() -> sessionmaker[Session]:
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    return sessionmaker(
        bind=engine, info=cross_tenant_info("tests set up and inspect rows across tenants")
    )


def _session_of(db: Session, refresh_token: str) -> uuid.UUID:
    sid: uuid.UUID = db.execute(
        text("SELECT session_id FROM refresh_tokens WHERE token_hash = :h"),
        {"h": hash_refresh_token(refresh_token)},
    ).scalar_one()
    return sid


def _forget_trial(db: Session, session_id: str) -> None:
    """Delete a trial this module started: its session, conversation and tenant."""
    db.rollback()
    tenant: uuid.UUID = db.execute(
        text("SELECT tenant_id FROM anonymous_sessions WHERE id = :i"), {"i": uuid.UUID(session_id)}
    ).scalar_one()
    for table in ("anonymous_sessions", "diagnostic_sessions"):
        db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})
    db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant})
    db.commit()


def _authenticates(db: Session, access_token: str) -> bool:
    """Whether a request carrying this access token would be let in."""
    with sessionmaker(bind=db.get_bind())() as request_session:
        try:
            auth.authenticate(session=request_session, caller=decode_access_token(access_token))
        except AuthenticationError:
            return False
        return True


def _live_in_session(db: Session, sid: uuid.UUID) -> int:
    count: int = db.execute(
        text("SELECT count(*) FROM refresh_tokens WHERE session_id = :s AND revoked_at IS NULL"),
        {"s": sid},
    ).scalar_one()
    return count


def _session_revoked(db: Session, sid: uuid.UUID) -> bool:
    revoked: datetime | None = db.execute(
        text("SELECT revoked_at FROM auth_sessions WHERE id = :s"), {"s": sid}
    ).scalar_one()
    return revoked is not None


@requires_db
@pytest.mark.parametrize("tabs", [2, 3, 5])
def test_concurrent_tabs_all_stay_signed_in_with_one_live_token(db: Session, tabs: int) -> None:
    """Tabs share the cookie, so several can present the same token at once.

    One rotates it. Every other one, finding it spent a moment ago, is handed
    that same successor: the session keeps exactly one live token, every tab
    stays signed in, and every Set-Cookie carries the same value, so the order
    the answers land in cannot leave the jar holding the wrong one.
    """
    from concurrent.futures import ThreadPoolExecutor

    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    factory = _own_sessions()

    def attempt(_n: int) -> str | None:
        own = factory()
        try:
            pair = auth.refresh(session=own, refresh_token=tokens.refresh_token)
            own.commit()
            return pair.refresh_token
        except AuthenticationError:
            own.rollback()
            return None
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=tabs) as pool:
        minted = list(pool.map(attempt, range(tabs)))

    assert all(token is not None for token in minted), "a concurrent tab was signed out"
    assert len(set(minted)) == 1, "the tabs were handed different successors"
    assert _live_in_session(db, sid) == 1
    assert not _session_revoked(db, sid)
    successor = minted[0]
    assert successor is not None
    assert _session_of(db, successor) == sid
    # And the session goes on: much later, the successor rotates normally.
    later = datetime.now(UTC) + timedelta(hours=1)
    assert auth.refresh(session=db, refresh_token=successor, now=later).access_token


@requires_db
def test_an_answer_lost_or_landing_late_leaves_a_working_cookie(db: Session) -> None:
    """A reload during a refresh: the rotation happened, its answer never arrived.

    The jar still holds the spent token. Presented again inside the window it
    gets the same successor the lost answer carried, so whichever answer the
    jar ends up with, the next refresh works.
    """
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    lost = auth.refresh(session=db, refresh_token=tokens.refresh_token)  # answer never seen
    db.commit()

    reloaded = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    assert reloaded.refresh_token == lost.refresh_token
    assert _live_in_session(db, sid) == 1

    later = datetime.now(UTC) + timedelta(hours=1)
    assert auth.refresh(session=db, refresh_token=reloaded.refresh_token, now=later).access_token


@requires_db
def test_the_window_is_ten_seconds_and_a_stale_token_after_it_is_a_replay(db: Session) -> None:
    assert timedelta(seconds=10) == auth.REFRESH_REUSE_GRACE
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    late = datetime.now(UTC) + timedelta(seconds=11)
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token, now=late)
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_token_two_rotations_behind_is_a_replay_even_inside_the_window(db: Session) -> None:
    """Only the immediate predecessor of the live token is tolerated."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    second = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    auth.refresh(session=db, refresh_token=second.refresh_token)
    db.commit()
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_thief_racing_the_victim_shares_one_chain_that_the_next_rotation_ends(
    db: Session,
) -> None:
    """The bound, stated by the test.

    A thief holding the spent token P can present it only within the window
    after the rotation that spent it, and then gets the session's one live
    token L, the very value the victim holds: never a second live token. The
    next rotation by either party leaves the other holding a spent token; the
    other's next presentation, outside the window, is a replay and ends the
    session for both.
    """
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    victim = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()

    thief = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    assert thief.refresh_token == victim.refresh_token
    assert _live_in_session(db, sid) == 1

    # The victim's next rotation, an hour on.
    hour = datetime.now(UTC) + timedelta(hours=1)
    victim_next = auth.refresh(session=db, refresh_token=victim.refresh_token, now=hour)
    db.commit()
    assert _live_in_session(db, sid) == 1

    # The thief comes back with its copy later than the window allows.
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db, refresh_token=thief.refresh_token, now=hour + timedelta(seconds=30)
        )
    db.rollback()
    assert _session_revoked(db, sid)
    assert _live_in_session(db, sid) == 0
    with pytest.raises(AuthenticationError):
        auth.refresh(session=db, refresh_token=victim_next.refresh_token)


@requires_db
def test_the_chain_is_ended_the_other_way_round_too(db: Session) -> None:
    """The thief rotates first; the victim's next presentation ends the session."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    hour = datetime.now(UTC) + timedelta(hours=1)
    auth.refresh(session=db, refresh_token=tokens.refresh_token, now=hour)  # the thief
    db.commit()
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db, refresh_token=tokens.refresh_token, now=hour + timedelta(minutes=5)
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_refresh_racing_logout_everywhere_leaves_nothing_alive(db: Session) -> None:
    from concurrent.futures import ThreadPoolExecutor

    factory = _own_sessions()
    for _ in range(5):
        email = _email()
        tokens = auth.signup(session=db, email=email, password=PASSWORD)
        other = auth.login(session=db, email=email, password=PASSWORD)
        db.commit()

        def do_refresh(token: str = tokens.refresh_token) -> str | None:
            own = factory()
            try:
                pair = auth.refresh(session=own, refresh_token=token)
                own.commit()
                return pair.access_token
            except AuthenticationError:
                own.rollback()
                return None
            finally:
                own.close()

        def do_everywhere(access: str = other.access_token) -> None:
            with sessionmaker(bind=db.get_bind())() as request_session:
                caller = auth.authenticate(
                    session=request_session, caller=decode_access_token(access)
                )
                auth.logout_everywhere(session=request_session, caller=caller)
                request_session.commit()

        with ThreadPoolExecutor(max_workers=2) as pool:
            refreshed = pool.submit(do_refresh)
            ended = pool.submit(do_everywhere)
            minted = refreshed.result()
            ended.result()

        assert _live_refresh_tokens(db, email) == 0
        if minted is not None:
            assert not _authenticates(db, minted)


@requires_db
def test_concurrent_replays_on_two_sessions_of_one_account_do_not_deadlock(
    db: Session,
) -> None:
    """Each replay held its own session's lock, then wanted the other's."""
    from concurrent.futures import ThreadPoolExecutor

    email = _email()
    laptop = auth.signup(session=db, email=email, password=PASSWORD)
    phone = auth.login(session=db, email=email, password=PASSWORD)
    db.commit()
    for pair in (laptop, phone):
        auth.refresh(session=db, refresh_token=pair.refresh_token)
        db.commit()
    factory = _own_sessions()
    late = datetime.now(UTC) + auth.REFRESH_REUSE_GRACE + timedelta(seconds=1)

    def replay(token: str) -> str:
        own = factory()
        try:
            auth.refresh(session=own, refresh_token=token, now=late)
            own.commit()
            return "minted"
        except AuthenticationError:
            own.rollback()
            return "refused"
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replay, [laptop.refresh_token, phone.refresh_token]))

    # A deadlock surfaces as a database error, not an AuthenticationError.
    assert results == ["refused", "refused"]
    assert _live_refresh_tokens(db, email) == 0


@requires_db
def test_reuse_inside_the_window_is_refused_once_the_session_has_ended(db: Session) -> None:
    """The window serves a racing tab, never a token whose sign-in was ended."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    rotated = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    auth.logout(session=db, caller=None, refresh_token=rotated.refresh_token)
    db.commit()

    with pytest.raises(AuthenticationError, match="ended"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)


# --- sign-in sessions and logout ------------------------------------------------


@requires_db
def test_login_and_signup_each_start_a_session_named_in_the_access_token(db: Session) -> None:
    email = _email()
    first = auth.signup(session=db, email=email, password=PASSWORD)
    second = auth.login(session=db, email=email, password=PASSWORD)
    db.commit()

    first_sid = decode_access_token(first.access_token).session_id
    second_sid = decode_access_token(second.access_token).session_id
    assert first_sid == str(_session_of(db, first.refresh_token))
    assert second_sid == str(_session_of(db, second.refresh_token))
    assert first_sid != second_sid


@requires_db
def test_a_refresh_continues_the_same_session(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    rotated = auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.commit()
    assert (
        decode_access_token(rotated.access_token).session_id
        == decode_access_token(tokens.access_token).session_id
    )


@requires_db
def test_after_logout_neither_the_access_nor_the_refresh_token_works(db: Session) -> None:
    """Replay of either credential after sign-out is refused, the access token at once."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    assert _authenticates(db, tokens.access_token)

    caller = decode_access_token(tokens.access_token)
    auth.logout(session=db, caller=caller, refresh_token=None)
    db.commit()

    assert not _authenticates(db, tokens.access_token)
    with pytest.raises(AuthenticationError, match="ended"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)


@requires_db
def test_logout_by_the_cookie_alone_ends_the_session(db: Session) -> None:
    """The access token may have expired; the cookie is still proof of the sign-in."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    auth.logout(session=db, caller=None, refresh_token=tokens.refresh_token)
    db.commit()
    assert not _authenticates(db, tokens.access_token)


@requires_db
def test_logout_of_one_session_leaves_the_others_alive(db: Session) -> None:
    email = _email()
    laptop = auth.signup(session=db, email=email, password=PASSWORD)
    phone = auth.login(session=db, email=email, password=PASSWORD)
    db.commit()

    auth.logout(session=db, caller=decode_access_token(laptop.access_token), refresh_token=None)
    db.commit()

    assert not _authenticates(db, laptop.access_token)
    assert _authenticates(db, phone.access_token)
    assert auth.refresh(session=db, refresh_token=phone.refresh_token).access_token


@requires_db
def test_logout_is_idempotent_and_silent(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    for _ in range(2):
        auth.logout(session=db, caller=None, refresh_token=tokens.refresh_token)
        db.commit()
    auth.logout(session=db, caller=None, refresh_token="never-issued")
    auth.logout(session=db, caller=None, refresh_token=None)
    db.commit()


@requires_db
def test_logout_everywhere_ends_every_session_of_the_account_only(db: Session) -> None:
    email = _email()
    laptop = auth.signup(session=db, email=email, password=PASSWORD)
    phone = auth.login(session=db, email=email, password=PASSWORD)
    someone_else = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()

    with sessionmaker(bind=db.get_bind())() as request_session:
        caller = auth.authenticate(
            session=request_session, caller=decode_access_token(laptop.access_token)
        )
        auth.logout_everywhere(session=request_session, caller=caller)
        request_session.commit()

    for pair in (laptop, phone):
        assert not _authenticates(db, pair.access_token)
        with pytest.raises(AuthenticationError):
            auth.refresh(session=db, refresh_token=pair.refresh_token)
        db.rollback()
    assert _authenticates(db, someone_else.access_token)
    assert _live_refresh_tokens(db, email) == 0


@requires_db
def test_a_trial_cannot_log_out_everywhere(db: Session) -> None:
    trial = auth.start_trial(session=db, access_token_ttl_seconds=60)
    db.commit()
    with sessionmaker(bind=db.get_bind())() as request_session:
        caller = auth.authenticate(
            session=request_session, caller=decode_access_token(trial.access_token)
        )
        with pytest.raises(AuthenticationError):
            auth.logout_everywhere(session=request_session, caller=caller)
    _forget_trial(db, trial.session_id)


@requires_db
def test_a_refresh_racing_a_logout_leaves_no_session_alive(db: Session) -> None:
    """Whichever wins the session lock, nothing of the session survives the logout.

    A refresh that loses is refused; a logout that loses revokes the token the
    refresh minted. Repeated, because a race shows itself only sometimes.
    """
    from concurrent.futures import ThreadPoolExecutor

    factory = _own_sessions()
    for _ in range(10):
        tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
        db.commit()
        sid = _session_of(db, tokens.refresh_token)

        def do_refresh(token: str = tokens.refresh_token) -> str | None:
            own = factory()
            try:
                pair = auth.refresh(session=own, refresh_token=token)
                own.commit()
                return pair.access_token
            except AuthenticationError:
                own.rollback()
                return None
            finally:
                own.close()

        def do_logout(token: str = tokens.refresh_token) -> None:
            own = factory()
            try:
                auth.logout(session=own, caller=None, refresh_token=token)
                own.commit()
            finally:
                own.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            refreshed = pool.submit(do_refresh)
            ended = pool.submit(do_logout)
            minted = refreshed.result()
            ended.result()

        live: int = db.execute(
            text(
                "SELECT count(*) FROM refresh_tokens WHERE session_id = :s AND revoked_at IS NULL"
            ),
            {"s": sid},
        ).scalar_one()
        assert live == 0, "a refresh token outlived the logout it raced"
        assert not _authenticates(db, tokens.access_token)
        if minted is not None:
            assert not _authenticates(db, minted), "an access token outlived the logout"


@requires_db
def test_an_account_token_without_a_session_is_refused(db: Session) -> None:
    """Issued before sessions existed: no logout could reach it, so it is not accepted."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    caller = decode_access_token(tokens.access_token)
    legacy = auth_security.create_access_token(subject=caller.id, tenant_id=caller.tenant_id)
    assert decode_access_token(legacy).session_id is None
    assert not _authenticates(db, legacy)


@requires_db
def test_an_access_token_naming_another_accounts_session_is_refused(db: Session) -> None:
    mine = auth.signup(session=db, email=_email(), password=PASSWORD)
    theirs = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    me = decode_access_token(mine.access_token)
    borrowed = auth_security.create_access_token(
        subject=me.id,
        tenant_id=me.tenant_id,
        session_id=decode_access_token(theirs.access_token).session_id,
    )
    assert not _authenticates(db, borrowed)


@requires_db
def test_a_refresh_token_without_a_session_is_refused(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    db.execute(
        text("UPDATE refresh_tokens SET session_id = NULL WHERE token_hash = :h"),
        {"h": hash_refresh_token(tokens.refresh_token)},
    )
    db.commit()
    with pytest.raises(AuthenticationError, match="predates"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)


@requires_db
def test_a_trial_token_needs_no_session(db: Session) -> None:
    """A trial has no sign-in; it is bounded by its own expiry and claim instead."""
    trial = auth.start_trial(session=db, access_token_ttl_seconds=60)
    db.commit()
    assert decode_access_token(trial.access_token).session_id is None
    assert _authenticates(db, trial.access_token)
    _forget_trial(db, trial.session_id)


# --- signup races and timing ------------------------------------------------


@requires_db
def test_concurrent_duplicate_signups_are_a_validation_error(db: Session) -> None:
    """Regression: a concurrent duplicate signup was a 500.

    Both racers passed the existence check, and the loser's IntegrityError
    escaped the domain.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.core.config import get_settings

    email = _email()
    engine = create_engine(get_settings().database_url.get_secret_value())
    factory = sessionmaker(
        bind=engine, info=cross_tenant_info("tests set up and inspect rows across tenants")
    )

    def attempt(_n: int) -> str:
        own = factory()
        try:
            auth.signup(session=own, email=email, password=PASSWORD)
            own.commit()
            return "created"
        except ValidationError as exc:
            own.rollback()
            return str(exc)
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(6)))

    assert outcomes.count("created") == 1
    assert set(outcomes) == {"created", "that email is already registered"}


@requires_db
def test_a_duplicate_signup_still_pays_for_the_hash(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered address costs the same bcrypt hash as a new one.

    Skipping it made signup answer measurably faster for a registered address —
    a timing oracle for which emails have accounts.
    """
    email = _email()
    auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()

    hashed: list[str] = []
    real = auth_security.hash_password

    def _counting(password: str) -> str:
        hashed.append(password)
        return real(password)

    monkeypatch.setattr(auth, "hash_password", _counting)
    with pytest.raises(ValidationError, match="already registered"):
        auth.signup(session=db, email=email, password=PASSWORD)
    assert hashed == [PASSWORD]


# --- known_user_id ------------------------------------------------------------


@requires_db
def test_known_user_id_recognises_a_real_user(db: Session) -> None:
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    subject = decode_access_token(tokens.access_token).id
    assert auth.known_user_id(session=db, subject=subject) == uuid.UUID(subject)


@requires_db
@pytest.mark.parametrize("subject", [str(uuid.uuid4()), "not-a-uuid", uuid.uuid4()])
def test_known_user_id_refuses_anything_else(db: Session, subject: str | uuid.UUID) -> None:
    """A trial's subject is its anonymous session; so is any other stranger."""
    assert auth.known_user_id(session=db, subject=subject) is None


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
            "INSERT INTO diagnostic_turns (id, session_id, tenant_id, position, question, "
            "answer, created_at, updated_at) VALUES (:i, :s, :t, 1, :q, 'a', now(), now())"
        ),
        {"i": uuid.uuid4(), "s": session_id, "t": tenant.id, "q": question},
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
    rows: Sequence[str] = (
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
    factory = sessionmaker(
        bind=engine, info=cross_tenant_info("tests set up and inspect rows across tenants")
    )

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
    used: int = db.execute(
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
    factory = sessionmaker(
        bind=engine, info=cross_tenant_info("tests set up and inspect rows across tenants")
    )

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


# --- the request's session gets its tenant here (ADR 0003) ---------------------


@requires_db
def test_resolving_the_caller_binds_the_session_to_their_tenant(db: Session) -> None:
    """The one place a request's session is scoped; everything after is filtered."""
    from app.core.tenancy import bound_tenant

    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    caller = decode_access_token(tokens.access_token)

    with sessionmaker(bind=db.get_bind())() as request_session:
        auth.resolve_caller(session=request_session, caller=caller)
        assert str(bound_tenant(request_session)) == caller.tenant_id


@requires_db
def test_a_token_claiming_another_tenant_finds_no_account(db: Session) -> None:
    """Bound before the lookup, so the account is not even visible from there."""
    mine = decode_access_token(
        auth.signup(session=db, email=_email(), password=PASSWORD).access_token
    )
    theirs = decode_access_token(
        auth.signup(session=db, email=_email(), password=PASSWORD).access_token
    )
    db.commit()
    forged = mine.model_copy(update={"tenant_id": theirs.tenant_id})

    with sessionmaker(bind=db.get_bind())() as request_session, pytest.raises(AuthenticationError):
        auth.resolve_caller(session=request_session, caller=forged)


@requires_db
def test_a_malformed_tenant_claim_is_an_authentication_failure(db: Session) -> None:
    """A 401 for a bad token, not a 500 for a programming error."""
    caller = decode_access_token(
        auth.signup(session=db, email=_email(), password=PASSWORD).access_token
    )
    db.commit()
    malformed = caller.model_copy(update={"tenant_id": "not-a-uuid"})

    with sessionmaker(bind=db.get_bind())() as request_session, pytest.raises(AuthenticationError):
        auth.resolve_caller(session=request_session, caller=malformed)


# --- proof of possession: a jar moved back by an out-of-order answer ------------


def _d1(db: Session) -> tuple[TokenPair, TokenPair, uuid.UUID]:
    """D1's sequence: T0 -> T1 by two requests, then T1 -> T2.

    Returns the T1 pair, the T2 pair, and the session.
    """
    first = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, first.refresh_token)
    t1 = auth.refresh(session=db, refresh_token=first.refresh_token)
    db.commit()
    again = auth.refresh(session=db, refresh_token=first.refresh_token)
    db.commit()
    assert again.refresh_token == t1.refresh_token
    t2 = auth.refresh(session=db, refresh_token=t1.refresh_token)
    db.commit()
    return t1, t2, sid


@requires_db
def test_d1_the_jar_holding_the_predecessor_recovers_with_the_tabs_access_token(
    db: Session,
) -> None:
    """A late Set-Cookie left T1 in the jar after T1 -> T2; 15 s on, the tab still works.

    The tab proves it is the session's own holder with its access token
    (same sid, same user, unexpired), so T1 gets the live T2 again: no mint,
    no second live token.
    """
    t1, t2, sid = _d1(db)
    later = datetime.now(UTC) + timedelta(seconds=15)
    bearer = decode_access_token(t2.access_token)

    again = auth.refresh(session=db, refresh_token=t1.refresh_token, now=later, bearer=bearer)
    db.commit()
    assert again.refresh_token == t2.refresh_token
    assert _live_in_session(db, sid) == 1
    assert not _session_revoked(db, sid)


@requires_db
def test_d1_without_an_access_token_is_still_a_replay(db: Session) -> None:
    """A cookie-only holder gets the 10 s window and no more."""
    t1, _t2, sid = _d1(db)
    later = datetime.now(UTC) + timedelta(seconds=15)
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=t1.refresh_token, now=later)
    db.rollback()
    assert _session_revoked(db, sid)


def _email_of(db: Session, user_id: str) -> str:
    email: str = db.execute(
        text("SELECT email FROM users WHERE id = :i"), {"i": uuid.UUID(user_id)}
    ).scalar_one()
    return email


@requires_db
@pytest.mark.parametrize("whose", ["other-session", "other-user"])
def test_a_bearer_of_another_session_or_user_proves_nothing(db: Session, whose: str) -> None:
    t1, t2, sid = _d1(db)
    if whose == "other-session":
        me = decode_access_token(t2.access_token)
        other = auth.login(session=db, email=_email_of(db, me.id), password=PASSWORD)
    else:
        other = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    later = datetime.now(UTC) + timedelta(seconds=15)
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=t1.refresh_token,
            now=later,
            bearer=decode_access_token(other.access_token),
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_bearer_does_not_reach_back_two_generations(db: Session) -> None:
    first = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, first.refresh_token)
    t1 = auth.refresh(session=db, refresh_token=first.refresh_token)
    db.commit()
    t2 = auth.refresh(session=db, refresh_token=t1.refresh_token)
    db.commit()
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=first.refresh_token,
            now=datetime.now(UTC) + timedelta(seconds=15),
            bearer=decode_access_token(t2.access_token),
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_bearer_reaches_back_no_further_than_an_access_token_lives(db: Session) -> None:
    from app.core.config import get_settings

    t1, t2, sid = _d1(db)
    ttl = get_settings().access_token_ttl_seconds
    beyond = datetime.now(UTC) + timedelta(seconds=ttl + 60)
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=t1.refresh_token,
            now=beyond,
            bearer=decode_access_token(t2.access_token),
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_legacy_session_with_two_live_tokens_refuses_the_older_never_a_500(
    db: Session,
) -> None:
    """The old grace model could leave two live tokens; only the newest may rotate."""
    tokens = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, tokens.refresh_token)
    row = db.execute(
        text("SELECT tenant_id, user_id FROM refresh_tokens WHERE session_id = :s"), {"s": sid}
    ).one()
    sibling, sibling_hash = auth_security.generate_refresh_token()
    db.execute(
        text("UPDATE refresh_tokens SET generation = NULL WHERE session_id = :s"), {"s": sid}
    )
    db.execute(text("UPDATE auth_sessions SET token_salt = NULL WHERE id = :s"), {"s": sid})
    db.execute(
        text(
            "INSERT INTO refresh_tokens (id, tenant_id, user_id, session_id, token_hash, "
            "expires_at, created_at, updated_at) VALUES (:i, :t, :u, :s, :h, :e, :c, :c)"
        ),
        {
            "i": uuid.uuid4(),
            "t": row.tenant_id,
            "u": row.user_id,
            "s": sid,
            "h": sibling_hash,
            "e": datetime.now(UTC) + timedelta(days=1),
            "c": datetime.now(UTC) + timedelta(seconds=1),
        },
    )
    db.commit()
    assert _live_in_session(db, sid) == 2

    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(session=db, refresh_token=tokens.refresh_token)
    db.rollback()
    assert _session_revoked(db, sid)
    with pytest.raises(AuthenticationError):
        auth.refresh(session=db, refresh_token=sibling)


# --- rotation bound to its generation; no rotation inside a burst ----------------


@pytest.fixture
def real_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production rule: a live token younger than ROTATE_AFTER is not rotated."""
    monkeypatch.setattr(auth, "ROTATE_AFTER", timedelta(minutes=5))


_SIX_MINUTES = timedelta(minutes=6)


@requires_db
def test_a_follower_with_an_older_generation_bearer_is_a_replay(db: Session) -> None:
    """Cookie and access token held once do not follow the chain past a rotation.

    The thief copied T1 and its access token (rgen 1). The victim rotates
    T1 -> T2. The thief's later T1, with that bearer, is not proof: the
    bearer was issued for generation 1, the live token is generation 2.
    """
    t1 = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, t1.refresh_token)
    t2 = auth.refresh(session=db, refresh_token=t1.refresh_token)
    db.commit()
    assert decode_access_token(t2.access_token).refresh_generation == 2

    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=t1.refresh_token,
            now=datetime.now(UTC) + timedelta(seconds=20),
            bearer=decode_access_token(t1.access_token),
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_a_bearer_without_a_generation_proves_nothing(db: Session) -> None:
    t1, t2, sid = _d1(db)
    legacy = decode_access_token(t2.access_token).model_copy(update={"refresh_generation": None})
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=t1.refresh_token,
            now=datetime.now(UTC) + timedelta(seconds=15),
            bearer=legacy,
        )
    db.rollback()
    assert _session_revoked(db, sid)


@requires_db
def test_every_access_token_names_the_generation_it_was_issued_with(db: Session) -> None:
    first = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    assert decode_access_token(first.access_token).refresh_generation == 1
    rotated = auth.refresh(session=db, refresh_token=first.refresh_token)
    db.commit()
    again = auth.refresh(session=db, refresh_token=first.refresh_token)  # re-issue
    db.commit()
    assert decode_access_token(rotated.access_token).refresh_generation == 2
    assert decode_access_token(again.access_token).refresh_generation == 2


@requires_db
@pytest.mark.usefixtures("real_rotation")
def test_d1_cannot_happen_a_burst_mints_nothing_and_any_late_answer_still_works(
    db: Session,
) -> None:
    """D1 at the source: inside ROTATE_AFTER nothing rotates, so no answer is stale.

    Five refreshes in a burst all get the token the jar already holds, whatever
    order their answers land in; a cookie-only refresh 15 s later works.
    """
    first = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, first.refresh_token)
    answers = []
    for _ in range(5):
        answers.append(auth.refresh(session=db, refresh_token=first.refresh_token).refresh_token)
        db.commit()
    assert set(answers) == {first.refresh_token}
    assert _live_in_session(db, sid) == 1

    late = answers[0]  # whichever landed last
    later = auth.refresh(
        session=db, refresh_token=late, now=datetime.now(UTC) + timedelta(seconds=15)
    )
    db.commit()
    assert later.refresh_token == first.refresh_token
    assert not _session_revoked(db, sid)


@requires_db
@pytest.mark.usefixtures("real_rotation")
def test_a_burst_straddling_a_rotation_ends_on_one_live_token(db: Session) -> None:
    first = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, first.refresh_token)
    at = datetime.now(UTC) + _SIX_MINUTES
    rotated = auth.refresh(session=db, refresh_token=first.refresh_token, now=at)
    db.commit()
    racing = auth.refresh(
        session=db, refresh_token=first.refresh_token, now=at + timedelta(seconds=1)
    )
    db.commit()
    after = auth.refresh(
        session=db, refresh_token=rotated.refresh_token, now=at + timedelta(seconds=2)
    )
    db.commit()
    assert rotated.refresh_token != first.refresh_token
    assert racing.refresh_token == rotated.refresh_token == after.refresh_token
    assert _live_in_session(db, sid) == 1


@requires_db
@pytest.mark.usefixtures("real_rotation")
@pytest.mark.parametrize("first_mover", ["victim", "thief"])
def test_replay_fires_when_victim_and_thief_diverge_across_a_rotation(
    db: Session, first_mover: str
) -> None:
    """Both hold the live T0; whoever rotates first leaves the other a spent copy."""
    t0 = auth.signup(session=db, email=_email(), password=PASSWORD)
    db.commit()
    sid = _session_of(db, t0.refresh_token)
    at = datetime.now(UTC) + _SIX_MINUTES
    auth.refresh(session=db, refresh_token=t0.refresh_token, now=at)  # the first mover
    db.commit()
    # The other comes back with T0 well after the 10 s window, and with no
    # bearer of the live generation.
    with pytest.raises(AuthenticationError, match="not valid"):
        auth.refresh(
            session=db,
            refresh_token=t0.refresh_token,
            now=at + timedelta(minutes=10),
            bearer=decode_access_token(t0.access_token),
        )
    db.rollback()
    assert _session_revoked(db, sid), first_mover
