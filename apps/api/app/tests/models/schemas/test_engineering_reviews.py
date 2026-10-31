"""Tests for ``app/models/schemas/engineering_reviews.py``: strict requests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.models.schemas.engineering_reviews import DecisionRequest, DesignateRequest

GOOD: dict[str, Any] = {
    "subject_kind": "template",
    "subject_ref": "tenant/m3-synthetic/v1",
    "subject_digest": "a" * 64,
    "decision": "approved",
    "standards": [],
    "reason": "Synthetic",
}


def test_a_good_request_parses() -> None:
    assert DecisionRequest(**GOOD).decision == "approved"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"reviewer": "someone"},
        {"subject_kind": "design"},
        {"decision": "approve"},
        {"subject_digest": "A" * 64},
        {"subject_digest": "a" * 63},
        {"reason": ""},
        {"standards": ["x"] * 21},
    ],
)
def test_a_bad_request_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(**{**GOOD, **change})


def test_a_designation_is_strict() -> None:
    with pytest.raises(ValidationError):
        DesignateRequest(member_id="x", qualification_statement="")
    with pytest.raises(ValidationError):
        DesignateRequest(member_id="x", qualification_statement="s", role="admin")  # type: ignore[call-arg]
