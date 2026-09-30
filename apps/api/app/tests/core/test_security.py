"""Tests for `app/core/security.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

`decode_access_token` is the door every authenticated request walks through,
so what is tested here is what it refuses: every malformed, forged, or stale
token must come out as an AuthenticationError (401), never as a 500 and never
as a caller.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ValidationError
from app.core.security import (
    MAX_PASSWORD_BYTES,
    create_access_token,
    decode_access_token,
    generate_claim_secret,
    generate_refresh_token,
    hash_claim_secret,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.schemas.auth import Role

# 64 bytes, so even the HS512 forgery below is signed with a key PyJWT accepts
# without warning — the refusal must come from the pin, not from the key.
_SECRET = "s" * 64


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[None]:
    """A configured environment, independent of the developer's .env."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for name, value in {
        "ENVIRONMENT": "dev",
        "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
        "OPENSEARCH_URL": "http://localhost:9200",
        "OPENAI_API_KEY": "test-key",
        "JWT_SECRET": _SECRET,
        "REDIS_URL": "redis://localhost:6379/0",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": "user-1",
        "tid": "tenant-1",
        "roles": ["engineer"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _sign(claims: dict[str, Any], *, key: str = _SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


# --- the round trip ------------------------------------------------------------


def test_an_issued_token_decodes_to_its_caller() -> None:
    token = create_access_token(
        subject="user-1", tenant_id="tenant-1", roles=frozenset({Role.REVIEWER})
    )
    caller = decode_access_token(token)
    assert caller.id == "user-1"
    assert caller.tenant_id == "tenant-1"
    assert caller.roles == frozenset({Role.REVIEWER})


# --- what is refused -----------------------------------------------------------


def test_no_token_is_refused() -> None:
    with pytest.raises(AuthenticationError, match="no credentials"):
        decode_access_token("")


def test_a_token_signed_with_another_key_is_refused() -> None:
    with pytest.raises(AuthenticationError, match="not valid"):
        decode_access_token(_sign(_claims(), key="k" * 64))


def test_the_algorithm_is_pinned() -> None:
    """A token's own alg header is not trusted; only the configured one is."""
    with pytest.raises(AuthenticationError, match="not valid"):
        decode_access_token(_sign(_claims(), algorithm="HS512"))


def test_an_unsigned_token_is_refused() -> None:
    """alg=none: the classic forgery. Built by hand so no library helps."""

    def _segment(value: dict[str, Any]) -> str:
        raw = json.dumps(value, default=str).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    claims = _claims()
    claims["exp"] = int(claims["exp"].timestamp())
    claims["iat"] = int(claims["iat"].timestamp())
    token = f"{_segment({'alg': 'none', 'typ': 'JWT'})}.{_segment(claims)}."
    with pytest.raises(AuthenticationError, match="not valid"):
        decode_access_token(token)


def test_an_expired_token_is_refused() -> None:
    past = datetime.now(UTC) - timedelta(minutes=10)
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(_sign(_claims(iat=past, exp=past + timedelta(minutes=1))))


@pytest.mark.parametrize("claim", ["sub", "exp"])
def test_a_token_missing_a_required_claim_is_refused(claim: str) -> None:
    claims = _claims()
    del claims[claim]
    with pytest.raises(AuthenticationError, match="not valid"):
        decode_access_token(_sign(claims))


def test_a_token_without_a_tenant_is_refused() -> None:
    """A token that cannot be scoped to a tenant cannot be used at all."""
    with pytest.raises(AuthenticationError, match="tenant"):
        decode_access_token(_sign(_claims(tid=None)))


@pytest.mark.parametrize("roles", [["superuser"], ["engineer", "root"], "admin", 7])
def test_an_unknown_role_is_a_401_not_a_500(roles: object) -> None:
    """A role this build cannot interpret used to escape as a ValueError."""
    with pytest.raises(AuthenticationError, match="role"):
        decode_access_token(_sign(_claims(roles=roles)))


def test_a_garbage_token_is_refused() -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token("not.a.token")


# --- passwords -----------------------------------------------------------------


def test_a_password_round_trips() -> None:
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)


def test_an_overlong_password_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValidationError):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_an_overlong_password_never_verifies() -> None:
    stored = hash_password("x" * MAX_PASSWORD_BYTES)
    assert not verify_password("x" * (MAX_PASSWORD_BYTES + 1), stored)


def test_a_malformed_hash_reads_as_a_wrong_password() -> None:
    assert not verify_password("anything", "not-a-bcrypt-hash")


# --- refresh tokens and claim secrets ----------------------------------------


def test_refresh_tokens_are_stored_only_as_their_hash() -> None:
    token, token_hash = generate_refresh_token()
    assert token_hash == hash_refresh_token(token)
    assert token not in token_hash


def test_claim_secrets_are_stored_only_as_their_hash() -> None:
    secret, secret_hash = generate_claim_secret()
    assert secret_hash == hash_claim_secret(secret)
    assert len(secret_hash) == 64
