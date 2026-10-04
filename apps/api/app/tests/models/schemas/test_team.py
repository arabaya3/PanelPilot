"""Tests for `app/models/schemas/team.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.team import InviteRequest


def test_an_invitation_needs_an_email() -> None:
    assert InviteRequest(email="a@example.com").email == "a@example.com"
    with pytest.raises(ValidationError):
        InviteRequest.model_validate({"email": "not an email"})
