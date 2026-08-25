"""Tests for `app/models/schemas/auth_flows.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.auth_flows import QuotaStatus, SignupRequest


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


def test_quota_reports_remaining_separately_from_used() -> None:
    """The client shows remaining; deriving it client-side invites drift."""
    quota = QuotaStatus(questions_used=3, question_limit=10, questions_remaining=7)
    assert quota.questions_remaining == 7
