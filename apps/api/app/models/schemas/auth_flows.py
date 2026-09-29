"""Request and response schemas for the authentication endpoints."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

# Claim secrets and refresh tokens are 43-character URL-safe strings. The bound
# leaves room for a format change while refusing a megabyte "token" that would
# be hashed in full before being found not to exist.
_MAX_SECRET_LENGTH = 256


class SignupRequest(BaseModel):
    """Create an account, optionally claiming an anonymous trial session."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    # Bounded to the column it is stored in (users.full_name, String(200)).
    # Unbounded, an oversized name reached the INSERT and came back as a
    # database DataError — a 500 for what is plainly a client mistake.
    full_name: str | None = Field(default=None, max_length=200)
    # When present, the trial conversation started before signup is carried
    # into the new account instead of being abandoned. A UUID; the bound only
    # stops an arbitrary string reaching the lookup.
    claim_session_id: str | None = Field(default=None, max_length=64)
    # The session id is not a credential -- it appears in URLs. This secret,
    # issued when the trial started and held only by that browser, is what
    # proves the claimer owns the session. Without it, any leaked session id
    # was a takeover of that session's tenant.
    claim_secret: str | None = Field(default=None, max_length=_MAX_SECRET_LENGTH)


class TrialStart(BaseModel):
    """A newly started anonymous trial.

    ``session_id`` and ``claim_secret`` are the pair FE-008's client already
    expects; the field names are snake_case because that client reads them by
    those exact names.

    ``access_token`` is carried alongside them because a trial has to be able
    to *ask something* — every diagnostics route authenticates, and a trial
    that cannot call one is a landing page that collects a question and does
    nothing with it. The token is scoped to the provisional tenant the trial
    created, so it can do no more than the trial itself may.

    The secret is returned exactly once, here. Only its hash is stored, so it
    cannot be re-read or recovered later — losing it means starting a new
    trial, which is the safe direction.
    """

    session_id: str
    claim_secret: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    questions_remaining: int


class TrialResumeRequest(BaseModel):
    """Get a fresh access token for a trial this browser already started.

    The pair ``POST /auth/trial`` returned. The secret is what proves the
    caller is that browser: the session id alone is not a credential.
    """

    session_id: str = Field(max_length=64)
    claim_secret: str = Field(max_length=_MAX_SECRET_LENGTH)


class LoginRequest(BaseModel):
    """Exchange credentials for a token pair."""

    email: EmailStr
    # No minimum: login must answer "incorrect" for any password, including
    # ones set before a policy change. The maximum only bounds the payload;
    # anything over bcrypt's 72 bytes already fails verification.
    password: str = Field(max_length=1024)


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a new pair."""

    refresh_token: str = Field(max_length=_MAX_SECRET_LENGTH)


class TokenPair(BaseModel):
    """A short-lived access token and the refresh token that renews it."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class QuotaStatus(BaseModel):
    """Free-tier usage, as counted on the server."""

    questions_used: int
    question_limit: int
    questions_remaining: int
