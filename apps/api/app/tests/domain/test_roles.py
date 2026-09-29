"""Tests for `app/domain/roles.py` — roles read from the database.

The property that matters: what an account may do is what the database says
*now*, whatever its token claims. Tested against a real database, because the
grants live in a join table and the question is answered through it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthenticationError, NotFoundError, ValidationError
from app.core.security import decode_access_token
from app.core.tenancy import cross_tenant_info
from app.domain import auth, roles
from app.models.schemas.auth import Role
from app.models.tables.user import Role as RoleRow
from app.models.tables.user import User

_PREFIX = "roles-tests-"
PASSWORD = "correct horse battery staple"


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM user_roles LIMIT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)


@pytest.fixture
def db() -> Iterator[Session]:
    """A session for setting up and inspecting accounts across tenants."""
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine, info=cross_tenant_info("tests manage accounts"))()
    try:
        yield session
    finally:
        session.rollback()
        tenants = f"(SELECT id FROM tenants WHERE slug LIKE '{_PREFIX}%')"
        session.execute(
            text(
                "DELETE FROM user_roles WHERE user_id IN "
                f"(SELECT id FROM users WHERE tenant_id IN {tenants})"
            )
        )
        session.execute(text(f"DELETE FROM refresh_tokens WHERE tenant_id IN {tenants}"))
        session.execute(text(f"DELETE FROM users WHERE tenant_id IN {tenants}"))
        session.execute(text(f"DELETE FROM tenants WHERE slug LIKE '{_PREFIX}%'"))
        session.commit()
        session.close()
        engine.dispose()


def _email() -> str:
    # signup() derives the tenant slug from the local part, so cleanup matches.
    return f"{_PREFIX}{uuid.uuid4().hex[:8]}@test.invalid"


def _account(db: Session) -> tuple[str, str]:
    """Sign up an account; return its email and a freshly minted access token."""
    email = _email()
    tokens = auth.signup(session=db, email=email, password=PASSWORD)
    db.commit()
    return email, tokens.access_token


def _as_request(db: Session, token: str) -> Session:
    """A fresh, unbound session, as the next request would get."""
    del token
    return sessionmaker(bind=db.get_bind())()


# --- grants live in the database ----------------------------------------------


@requires_db
def test_a_new_account_is_an_engineer_and_nothing_more(db: Session) -> None:
    email, _ = _account(db)
    user = db.query(User).filter(User.email == email).one()

    assert roles.roles_of(user) == frozenset({Role.ENGINEER})


@requires_db
def test_a_grant_is_stored_and_idempotent(db: Session) -> None:
    email, _ = _account(db)

    assert roles.grant_role(session=db, email=email, role=Role.REVIEWER) is True
    assert roles.grant_role(session=db, email=email, role=Role.REVIEWER) is False
    db.commit()

    user = db.query(User).filter(User.email == email).one()
    assert roles.roles_of(user) == frozenset({Role.ENGINEER, Role.REVIEWER})


@requires_db
def test_the_role_row_is_created_once_and_shared(db: Session) -> None:
    first, _ = _account(db)
    second, _ = _account(db)
    roles.grant_role(session=db, email=first, role=Role.INGESTION)
    roles.grant_role(session=db, email=second, role=Role.INGESTION)
    db.commit()

    assert db.query(RoleRow).filter(RoleRow.name == Role.INGESTION.value).count() == 1


@requires_db
def test_a_revocation_removes_only_that_role(db: Session) -> None:
    email, _ = _account(db)
    roles.grant_role(session=db, email=email, role=Role.REVIEWER)
    roles.grant_role(session=db, email=email, role=Role.INGESTION)

    assert roles.revoke_role(session=db, email=email, role=Role.REVIEWER) is True
    assert roles.revoke_role(session=db, email=email, role=Role.REVIEWER) is False
    db.commit()

    user = db.query(User).filter(User.email == email).one()
    assert roles.roles_of(user) == frozenset({Role.ENGINEER, Role.INGESTION})


@requires_db
@pytest.mark.parametrize("change", [roles.grant_role, roles.revoke_role])
def test_the_implicit_role_is_neither_granted_nor_revoked(db: Session, change: object) -> None:
    """Revoking ENGINEER would leave an account that cannot ask a question."""
    email, _ = _account(db)
    with pytest.raises(ValidationError):
        change(session=db, email=email, role=Role.ENGINEER)  # type: ignore[operator]


@requires_db
def test_an_unknown_account_is_not_found(db: Session) -> None:
    with pytest.raises(NotFoundError):
        roles.grant_role(session=db, email=_email(), role=Role.REVIEWER)


@requires_db
def test_email_is_matched_the_way_signup_stores_it(db: Session) -> None:
    email, _ = _account(db)

    assert roles.grant_role(session=db, email=f"  {email.upper()} ", role=Role.REVIEWER)


@requires_db
def test_a_stored_role_the_code_no_longer_knows_is_skipped(db: Session) -> None:
    """A stale grant must not lock someone out of everything."""
    email, _ = _account(db)
    user = db.query(User).filter(User.email == email).one()
    stale = db.query(RoleRow).filter(RoleRow.name == "retired-capability").one_or_none()
    user.roles.append(stale or RoleRow(name="retired-capability"))
    db.flush()

    assert roles.roles_of(user) == frozenset({Role.ENGINEER})
    db.rollback()


# --- authorization reads the database, not the token ---------------------------


@requires_db
def test_a_grant_applies_to_a_token_minted_before_it(db: Session) -> None:
    email, token = _account(db)
    roles.grant_role(session=db, email=email, role=Role.REVIEWER)
    db.commit()

    with _as_request(db, token) as request:
        caller = auth.authenticate(session=request, caller=decode_access_token(token))

    assert caller.has_role(Role.REVIEWER)


@requires_db
def test_a_revocation_is_not_outlived_by_the_token(db: Session) -> None:
    """The failure this fixes: a revoked role kept working until expiry."""
    email, _ = _account(db)
    roles.grant_role(session=db, email=email, role=Role.REVIEWER)
    db.commit()
    token = auth.login(session=db, email=email, password=PASSWORD).access_token
    db.commit()
    assert Role.REVIEWER in decode_access_token(token).roles  # the hint says so

    roles.revoke_role(session=db, email=email, role=Role.REVIEWER)
    db.commit()

    with _as_request(db, token) as request:
        caller = auth.authenticate(session=request, caller=decode_access_token(token))
    assert not caller.has_role(Role.REVIEWER)


@requires_db
def test_a_role_claimed_by_the_token_alone_is_ignored(db: Session) -> None:
    """A forged or stale claim grants nothing the database does not."""
    _, token = _account(db)
    claimed = decode_access_token(token).model_copy(
        update={"roles": frozenset({Role.ENGINEER, Role.REVIEWER, Role.ADMIN})}
    )

    with _as_request(db, token) as request:
        caller = auth.authenticate(session=request, caller=claimed)

    assert caller.roles == frozenset({Role.ENGINEER})


@requires_db
def test_the_caller_carries_their_email(db: Session) -> None:
    """Refusal messages named an empty string: tokens carry no email."""
    email, token = _account(db)

    with _as_request(db, token) as request:
        caller = auth.authenticate(session=request, caller=decode_access_token(token))

    assert caller.email == email


@requires_db
def test_a_trial_is_an_engineer_whatever_its_token_says(db: Session) -> None:
    trial = auth.start_trial(session=db)
    db.commit()
    decoded = decode_access_token(trial.access_token)
    claimed = decoded.model_copy(update={"roles": frozenset({Role.REVIEWER})})
    try:
        with _as_request(db, trial.access_token) as request:
            caller = auth.authenticate(session=request, caller=claimed)
        assert caller.roles == frozenset({Role.ENGINEER})
    finally:
        # A trial's tenant slug is not this module's prefix; remove it by id.
        for table in ("anonymous_sessions", "diagnostic_sessions"):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": decoded.tenant_id})
        db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": decoded.tenant_id})
        db.commit()


@requires_db
def test_an_inactive_account_is_still_refused(db: Session) -> None:
    email, token = _account(db)
    db.query(User).filter(User.email == email).update({"is_active": False})
    db.commit()

    with _as_request(db, token) as request, pytest.raises(AuthenticationError):
        auth.authenticate(session=request, caller=decode_access_token(token))


# --- end to end: the reviewer routes were unreachable by anyone ---------------


@requires_db
def test_a_granted_reviewer_reaches_the_reviewer_routes(db: Session) -> None:
    """The gap as it showed in production: every reviewer route was a 403.

    Through the real app and the real session dependency, with no overrides:
    the same token is refused before the grant and admitted after it.
    """
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    email, token = _account(db)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(create_app(get_settings())) as client:
        assert client.get("/api/v1/verification/escalations", headers=headers).status_code == 403

        roles.grant_role(session=db, email=email, role=Role.REVIEWER)
        db.commit()

        assert client.get("/api/v1/verification/escalations", headers=headers).status_code == 200


# --- who holds a role --------------------------------------------------------


@requires_db
def test_holders_are_the_active_accounts_granted_the_role(db: Session) -> None:
    """The reviewer pool the daily assignment hands work to."""
    granted, _ = _account(db)
    revoked, _ = _account(db)
    inactive, _ = _account(db)
    never, _ = _account(db)
    for email in (granted, revoked, inactive):
        roles.grant_role(session=db, email=email, role=Role.REVIEWER)
    roles.revoke_role(session=db, email=revoked, role=Role.REVIEWER)
    db.query(User).filter(User.email == inactive).update({"is_active": False})
    db.commit()

    holders = set(roles.holders_of(session=db, role=Role.REVIEWER))
    ids = {u.email: u.id for u in db.query(User).filter(User.email.like(f"{_PREFIX}%"))}

    assert ids[granted] in holders
    assert ids[revoked] not in holders
    assert ids[inactive] not in holders
    assert ids[never] not in holders


def test_asking_who_holds_the_implicit_role_is_refused() -> None:
    with pytest.raises(ValidationError):
        roles.holders_of(session=cast(Session, None), role=Role.ENGINEER)
