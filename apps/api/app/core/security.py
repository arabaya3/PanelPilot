"""Authentication primitives.

Token issuing, verification, and password hashing only: this module answers
"who is this caller". Rules about *what a caller may do* are business rules and
live in ``app.domain``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ValidationError
from app.models.schemas.auth import CurrentUser, Role

# bcrypt hashes at most 72 bytes and raises beyond that rather than truncating.
# Truncating silently would mean two different long passwords authenticating
# each other, so the limit is surfaced to the caller as a validation error.
MAX_PASSWORD_BYTES = 72

# Refresh tokens are compared by hash, so the stored value is useless if the
# database leaks. SHA-256 is right here and bcrypt is not: these are
# high-entropy random tokens, not user-chosen secrets, so there is nothing to
# brute-force and no reason to pay a slow KDF on every refresh.
_REFRESH_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Hash a password for storage.

    Args:
        password: The plaintext password.

    Returns:
        The bcrypt hash, safe to store.

    Raises:
        ValidationError: If the password exceeds bcrypt's 72-byte limit. Longer
            input is rejected rather than truncated, because truncation would
            make two different passwords interchangeable.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValidationError(
            f"password is {len(encoded)} bytes; the maximum is {MAX_PASSWORD_BYTES}"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash.

    Args:
        password: The plaintext password supplied by the caller.
        password_hash: The stored bcrypt hash.

    Returns:
        ``True`` when they match. Never raises on a malformed hash — a
        corrupted row must read as "wrong password", not as a server error that
        distinguishes it from a valid account.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_refresh_token() -> tuple[str, str]:
    """Mint a refresh token and the hash to store alongside it.

    Returns:
        ``(token, token_hash)``. The caller returns the token to the client and
        persists only the hash.
    """
    token = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage and lookup.

    Args:
        token: The refresh token as presented by the client.

    Returns:
        Its hex SHA-256 digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    *,
    subject: str,
    tenant_id: str,
    roles: frozenset[Role] | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Issue a signed access token for a subject.

    The tenant is embedded in the token so every request carries its own
    isolation boundary — a route cannot forget to scope a query to a tenant it
    was never told about.

    Args:
        subject: Stable user identifier to embed as the token subject.
        tenant_id: The tenant this user belongs to.
        roles: Roles held by the user.
        ttl_seconds: Override for the configured token lifetime.

    Returns:
        The encoded JWT.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    lifetime = ttl_seconds if ttl_seconds is not None else settings.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": subject,
        "tid": tenant_id,
        "roles": sorted(r.value for r in (roles or frozenset())),
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> CurrentUser:
    """Verify a token and return the caller it identifies.

    Args:
        token: Encoded JWT taken from the ``Authorization`` header.

    Returns:
        The authenticated caller, including their tenant.

    Raises:
        AuthenticationError: If the token is missing, expired, malformed, or
            signed with the wrong key or algorithm.
    """
    settings = get_settings()
    if not token:
        raise AuthenticationError("no credentials supplied")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pinned: accepting the token's own alg header is how "none" and
            # HS/RS confusion attacks get in.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("token is not valid") from exc

    tenant_id = payload.get("tid")
    if not tenant_id:
        # A token without a tenant cannot be scoped, so it is not usable.
        raise AuthenticationError("token carries no tenant")

    return CurrentUser(
        id=str(payload["sub"]),
        email=str(payload.get("email", "")),
        tenant_id=str(tenant_id),
        roles=frozenset(Role(r) for r in payload.get("roles", [])),
    )


def generate_claim_secret() -> tuple[str, str]:
    """Mint the secret that proves ownership of an anonymous trial session.

    The session id is not a credential — it appears in URLs and request
    bodies. This secret is what the browser that started the trial holds, and
    what it must present to claim that session at signup.

    Returns:
        ``(secret, secret_hash)``. Return the secret to the browser; persist
        only the hash.
    """
    secret = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return secret, hash_claim_secret(secret)


def hash_claim_secret(secret: str) -> str:
    """Hash a claim secret for storage and comparison.

    Args:
        secret: The claim secret as presented by the client.

    Returns:
        Its hex SHA-256 digest.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
