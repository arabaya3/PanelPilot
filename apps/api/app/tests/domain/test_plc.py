"""Tests for `app/domain/plc.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The parser is exercised in `app/tests/ai/plc/`. What this layer owns is one
policy: a validator that raises has not passed anything. That is tested
directly here rather than only through HTTP, because it is a property of the
domain call — anything that reaches this function, request or not, must get
the same answer.
"""

from __future__ import annotations

import pytest

from app.domain import plc as plc_domain
from app.domain.plc import (
    VALIDATION_UNAVAILABLE,
    PlcError,
    generate_code,
    review_code,
    safe_validate,
)
from app.models.schemas.plc import (
    PlcDialect,
    PlcGenerationRequest,
    PlcLanguage,
    ValidationStatus,
)

VALID_ST = """PROGRAM MotorStart
VAR_INPUT
    StartButton : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
END_VAR
    IF StartButton THEN
        MotorRun := TRUE;
    END_IF;
END_PROGRAM"""

BROKEN_ST = """PROGRAM P
VAR
    A : BOOL;
END_VAR
    A := NeverDeclared;
END_PROGRAM"""


def test_valid_code_reviews_clean() -> None:
    result = review_code(VALID_ST)

    assert result.status is ValidationStatus.VALID
    assert result.ready


def test_broken_code_is_reported_as_invalid() -> None:
    result = review_code(BROKEN_ST)

    assert result.status is ValidationStatus.INVALID
    assert not result.ready


def test_the_dialect_is_carried_through() -> None:
    result = review_code(VALID_ST, dialect=PlcDialect.CODESYS_ST)

    assert result.dialect is PlcDialect.CODESYS_ST


# --- the policy this layer owns -----------------------------------------------


def test_a_validator_that_raises_yields_an_explicit_non_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-010's edge case, tested at the layer that decides it rather than only
    # through a route. A checker that fell over has not approved anything, and
    # returning code with nothing said about it reads as approval.
    def _explode(source: str, *, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("the parser fell over")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    result = safe_validate(VALID_ST, PlcDialect.IEC_61131_3)

    assert result.status is ValidationStatus.INCOMPLETE
    assert result.checked_by == VALIDATION_UNAVAILABLE
    assert not result.ready


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("boom"), ValueError("bad"), MemoryError(), RecursionError()],
    ids=["RuntimeError", "ValueError", "MemoryError", "RecursionError"],
)
def test_any_exception_at_all_is_caught(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    # Deliberately broad. A grammar can recurse too deeply on adversarial
    # input, and an unexpected exception type escaping would be the one path
    # back to returning unchecked code as though it had been checked.
    def _explode(source: str, *, dialect: PlcDialect) -> None:
        del source, dialect
        raise failure

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    result = safe_validate(VALID_ST, PlcDialect.IEC_61131_3)

    assert result.status is ValidationStatus.INCOMPLETE


def test_a_broken_validator_is_distinguishable_from_an_unsupported_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both untrusted, both INCOMPLETE — but one is a known gap the checker can
    # describe and the other is a defect someone has to fix.
    unsupported = review_code(
        "FUNCTION_BLOCK FB\nVAR\n A : BOOL;\nEND_VAR\n A := TRUE;\nEND_FUNCTION_BLOCK"
    )

    def _explode(source: str, *, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("boom")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)
    broken = safe_validate(VALID_ST, PlcDialect.IEC_61131_3)

    assert broken.status is unsupported.status is ValidationStatus.INCOMPLETE
    assert broken.checked_by != unsupported.checked_by


def test_the_failure_names_what_went_wrong(monkeypatch: pytest.MonkeyPatch) -> None:
    # So the defect is diagnosable. "Validation could not be completed" with no
    # cause gives a maintainer nothing to act on, and this path only fires when
    # something is genuinely broken — which is exactly when the detail matters.
    def _explode(source: str, *, dialect: PlcDialect) -> None:
        del source, dialect
        raise ZeroDivisionError("something silly")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    result = safe_validate(VALID_ST, PlcDialect.IEC_61131_3)

    assert "ZeroDivisionError" in result.findings[0].message


def test_the_non_verdict_is_a_warning_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The code is not known to be wrong — nothing looked at it. Reporting an
    # error would claim a defect that was never found, in code that may be
    # perfectly good.
    def _explode(source: str, *, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("boom")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    result = safe_validate(VALID_ST, PlcDialect.IEC_61131_3)

    assert all(f.severity.value == "warning" for f in result.findings)


# --- generation ---------------------------------------------------------------


def test_generation_refuses_rather_than_inventing_output() -> None:
    # Not wired to a model yet, and it says so. A plausible stub would make the
    # feature look finished and hand a caller a program no model wrote.
    with pytest.raises(PlcError, match="not yet wired"):
        generate_code(PlcGenerationRequest(description="start a motor"))


def test_ladder_generation_refuses_too() -> None:
    with pytest.raises(PlcError, match="ladder generation is not yet wired"):
        generate_code(
            PlcGenerationRequest(
                description="start a motor",
                language=PlcLanguage.LADDER,
            )
        )
