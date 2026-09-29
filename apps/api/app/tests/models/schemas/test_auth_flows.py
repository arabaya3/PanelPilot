"""Tests for `app/models/schemas/auth_flows.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.auth_flows import (
    LoginRequest,
    QuotaStatus,
    RefreshRequest,
    SignupRequest,
    TrialResumeRequest,
)


def test_signup_rejects_a_short_password() -> None:
    """A 12-character floor is the cheapest real protection here."""
    with pytest.raises(ValidationError):
        SignupRequest(email="a@example.com", password="short")


def test_signup_rejects_a_password_bcrypt_cannot_hash() -> None:
    """Bcrypt raises past 72 bytes; the cap stops that reaching the hasher."""
    with pytest.raises(ValidationError):
        SignupRequest(email="a@example.com", password="x" * 129)


def test_signup_rejects_a_malformed_email() -> None:
    # Note: EmailStr also rejects reserved TLDs like .invalid, so test
    # addresses here use example.com rather than the .invalid convention
    # used elsewhere in the suite.
    with pytest.raises(ValidationError):
        SignupRequest(email="not-an-email", password="a" * 20)


def test_claim_session_is_optional() -> None:
    """Signing up without a trial session is the normal path."""
    assert SignupRequest(email="a@example.com", password="a" * 20).claim_session_id is None


def test_signup_bounds_the_full_name_to_its_column() -> None:
    """users.full_name is String(200); longer was a DataError, i.e. a 500."""
    assert SignupRequest(email="a@example.com", password="a" * 20, full_name="x" * 200)
    with pytest.raises(ValidationError):
        SignupRequest(email="a@example.com", password="a" * 20, full_name="x" * 201)


def test_signup_bounds_the_claim_secret() -> None:
    with pytest.raises(ValidationError):
        SignupRequest(email="a@example.com", password="a" * 20, claim_secret="x" * 257)


def test_login_accepts_any_password_up_to_its_bound() -> None:
    """No minimum: login answers "incorrect", it does not enforce policy."""
    assert LoginRequest(email="a@example.com", password="x").password == "x"
    assert LoginRequest(email="a@example.com", password="x" * 1024)
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com", password="x" * 1025)


def test_the_refresh_token_is_bounded() -> None:
    assert RefreshRequest(refresh_token="x" * 256)
    with pytest.raises(ValidationError):
        RefreshRequest(refresh_token="x" * 257)


def test_trial_resume_needs_both_halves_of_the_pair() -> None:
    """The session id alone is not a credential."""
    with pytest.raises(ValidationError):
        TrialResumeRequest.model_validate({"session_id": "abc"})
    request = TrialResumeRequest(session_id="abc", claim_secret="s")
    assert request.claim_secret == "s"


def test_trial_resume_is_bounded() -> None:
    with pytest.raises(ValidationError):
        TrialResumeRequest(session_id="x" * 65, claim_secret="s")
    with pytest.raises(ValidationError):
        TrialResumeRequest(session_id="abc", claim_secret="x" * 257)


def test_quota_reports_remaining_separately_from_used() -> None:
    """The client shows remaining; deriving it client-side invites drift."""
    quota = QuotaStatus(questions_used=3, question_limit=10, questions_remaining=7)
    assert quota.questions_remaining == 7
