"""Authentication primitives.

Token issuing, verification, and password hashing only: this module answers
"who is this caller". Rules about *what a caller may do* are business rules and
live in ``app.domain``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
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


def generate_session_salt() -> str:
    """Mint the random per-session value refresh tokens are derived with.

    Returns:
        A URL-safe random string, stored on the session row.
    """
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


# The HKDF info label for the refresh-token key. Changing it changes every
# derived token, so it is versioned rather than edited.
_REFRESH_KEY_INFO = b"panelpilot refresh-token derivation v1"


def refresh_token_key() -> bytes:
    """Return the key refresh tokens are derived under.

    ``REFRESH_TOKEN_KEY`` when set; otherwise HKDF-SHA256 (RFC 5869) of
    ``JWT_SECRET`` with its own info label. Either way it is not the
    access-token signing key, so a use of one key cannot be mistaken for the
    other. Setting ``REFRESH_TOKEN_KEY`` lets the two be rotated separately.

    Returns:
        A 32-byte key.
    """
    settings = get_settings()
    if settings.refresh_token_key is not None:
        material = settings.refresh_token_key.get_secret_value().encode("utf-8")
    else:
        material = settings.jwt_secret.get_secret_value().encode("utf-8")
    return _hkdf_sha256(material, info=_REFRESH_KEY_INFO)


def _hkdf_sha256(material: bytes, *, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) with an all-zero salt, for one block or less."""
    prk = hmac.new(b"\x00" * hashlib.sha256().digest_size, material, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def derive_refresh_token(*, session_id: str, salt: str, generation: int) -> tuple[str, str]:
    """Derive a session's refresh token for one generation, and its hash.

    HMAC-SHA256 under the refresh-token key (`refresh_token_key`, never the
    access-token signing key itself), over the session, its random salt and
    the generation. The same inputs always give the same token, which
    is what lets the live token be handed again to a tab that raced its
    rotation without the token ever being stored: the row keeps only the hash
    and the salt, and neither derives anything without the key.

    Args:
        session_id: The sign-in session.
        salt: The session's random salt (`generate_session_salt`).
        generation: The token's place in the session's chain.

    Returns:
        ``(token, token_hash)``, shaped like ``generate_refresh_token``'s.
    """
    key = refresh_token_key()
    message = f"panelpilot-refresh-token|{session_id}|{salt}|{generation}"
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
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
    session_id: str | None = None,
    refresh_generation: int | None = None,
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
        session_id: The server-side sign-in the token belongs to, carried as
            the `sid` claim so a logout can end the token before it expires.
            Omitted for a trial, which has no sign-in.
        refresh_generation: The session's live refresh-token generation
            when this token was issued, carried as `rgen`. It binds the
            token, as proof of possession, to that one generation: see
            `auth._reissue_live_successor`.

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
    if session_id is not None:
        payload["sid"] = session_id
    if refresh_generation is not None:
        payload["rgen"] = refresh_generation
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

    try:
        roles = frozenset(Role(r) for r in payload.get("roles", []))
    except (ValueError, TypeError) as exc:
        # A role this build does not know — from a newer deploy, or a token
        # that was never ours — is a credential we cannot interpret. That is
        # a 401, not the 500 an escaped ValueError used to produce.
        raise AuthenticationError("token carries an unknown role") from exc

    return CurrentUser(
        id=str(payload["sub"]),
        email=str(payload.get("email", "")),
        tenant_id=str(tenant_id),
        roles=roles,
        session_id=str(payload["sid"]) if payload.get("sid") else None,
        refresh_generation=(
            payload["rgen"]
            if isinstance(payload.get("rgen"), int) and not isinstance(payload["rgen"], bool)
            else None
        ),
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
