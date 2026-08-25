"""Request and response schemas for the authentication endpoints."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    """Create an account, optionally claiming an anonymous trial session."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str | None = None
    # When present, the trial conversation started before signup is carried
    # into the new account instead of being abandoned.
    claim_session_id: str | None = None
    # The session id is not a credential -- it appears in URLs. This secret,
    # issued when the trial started and held only by that browser, is what
    # proves the claimer owns the session. Without it, any leaked session id
    # was a takeover of that session's tenant.
    claim_secret: str | None = None


class LoginRequest(BaseModel):
    """Exchange credentials for a token pair."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a new pair."""

    refresh_token: str


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
