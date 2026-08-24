"""Authentication primitives.

Token issuing and verification only: this module answers "who is this caller".
Rules about *what a caller may do* are business rules and live in ``app.domain``.
"""

from __future__ import annotations

from app.models.schemas.auth import CurrentUser


def create_access_token(*, subject: str, ttl_seconds: int | None = None) -> str:
    """Issue a signed access token for a subject.

    Args:
        subject: Stable user identifier to embed as the token subject.
        ttl_seconds: Override for the configured token lifetime.

    Returns:
        The encoded JWT.
    """
    raise NotImplementedError


def decode_access_token(token: str) -> CurrentUser:
    """Verify a token and return the caller it identifies.

    Args:
        token: Encoded JWT taken from the ``Authorization`` header.

    Returns:
        The authenticated caller.

    Raises:
        AuthenticationError: If the token is missing, expired, or malformed.
    """
    raise NotImplementedError
