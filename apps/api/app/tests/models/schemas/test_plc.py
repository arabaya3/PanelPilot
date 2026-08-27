"""Tests for `app/models/schemas/plc.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Mostly declarations, with one piece of real logic: `ready`. That property is
the single gate deciding whether generated code is presented as trustworthy,
so it gets tested against every status rather than the happy path.
"""

from __future__ import annotations

import pytest

from app.models.schemas.plc import (
    FindingSeverity,
    PlcDialect,
    PlcValidationResult,
    ValidationFinding,
    ValidationStatus,
)


def _result(status: ValidationStatus, *findings: ValidationFinding) -> PlcValidationResult:
    """Build a validation result.

    Args:
        status: The verdict.
        *findings: What was found.

    Returns:
        The result.
    """
    return PlcValidationResult(
        status=status,
        findings=list(findings),
        dialect=PlcDialect.IEC_61131_3,
        checked_by="test",
    )


def _finding(severity: FindingSeverity) -> ValidationFinding:
    """Build a finding.

    Args:
        severity: How much it matters.

    Returns:
        The finding.
    """
    return ValidationFinding(code="x", message="x", severity=severity)


def test_clean_valid_code_is_ready() -> None:
    assert _result(ValidationStatus.VALID).ready


def test_valid_code_with_warnings_is_still_ready() -> None:
    # A warning is a note, not a blocker. Requiring a clean sheet would mean an
    # unused spare tag stops correct code from being usable.
    assert _result(ValidationStatus.VALID, _finding(FindingSeverity.WARNING)).ready


def test_an_error_blocks_ready_even_on_a_valid_status() -> None:
    # Defensive: the two should not disagree, but if they ever do, the error is
    # the one to believe. Presenting code as ready while holding an error
    # against it is the exact failure this task exists to prevent.
    assert not _result(ValidationStatus.VALID, _finding(FindingSeverity.ERROR)).ready


def test_invalid_code_is_never_ready() -> None:
    assert not _result(ValidationStatus.INVALID, _finding(FindingSeverity.ERROR)).ready


@pytest.mark.parametrize("status", list(ValidationStatus))
def test_only_valid_can_ever_be_ready(status: ValidationStatus) -> None:
    # The whole vocabulary, so a status added later cannot quietly default to
    # being treated as a pass.
    result = _result(status)

    assert result.ready == (status is ValidationStatus.VALID)


def test_incomplete_is_not_ready() -> None:
    # AI-009's stated edge case, pinned on the property that acts on it: "an
    # unverifiable result is not the same as a verified-correct one."
    assert not _result(ValidationStatus.INCOMPLETE).ready


def test_incomplete_is_not_ready_even_with_no_findings() -> None:
    # Nothing found is not the same as nothing wrong. A checker that could not
    # run has no findings precisely because it did not look.
    result = _result(ValidationStatus.INCOMPLETE)

    assert not result.findings
    assert not result.ready


def test_the_three_statuses_are_distinct() -> None:
    assert len({s.value for s in ValidationStatus}) == 3


def test_a_finding_may_omit_its_line() -> None:
    # Whole-program findings — an unreferenced tag, an unverifiable dialect —
    # have no single line to point at.
    assert _finding(FindingSeverity.WARNING).line is None
