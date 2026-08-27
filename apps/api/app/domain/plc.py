"""PLC generation and review, as the API calls them.

Sits between the routes and ``app/ai/plc`` because routes stay thin — they
parse, delegate, and return. Everything here is a policy decision rather than
an HTTP one, and putting it in a route would make it untestable without a
client and invisible to anything that is not a request.

The policy that matters: **a validator that raises has not passed anything.**
If the checker itself falls over, the answer is an explicit non-verdict, never
silence. Code handed back with nothing said about it reads as approved, which
is precisely the failure the whole PLC feature exists to prevent — and the one
place it could sneak back in is an unhandled exception on the checking path.
"""

from __future__ import annotations

import structlog

from app.ai.plc.generation import GenerationError, generate_plc_code
from app.ai.plc.validation import validate_plc_code
from app.models.schemas.plc import (
    FindingSeverity,
    LadderRung,
    PlcDialect,
    PlcGenerationRequest,
    PlcGenerationResult,
    PlcValidationResult,
    ValidationFinding,
    ValidationStatus,
)

logger = structlog.get_logger(__name__)

#: Reported when the validator itself failed, rather than finding a problem.
#:
#: Distinct from the checker's own "incomplete for this dialect", which is a
#: known gap it can describe. This is the checker breaking. Both are untrusted
#: and both must behave the same way, but a maintainer reading the response
#: needs to tell a gap from a defect.
VALIDATION_UNAVAILABLE = "validation-unavailable"


class PlcError(RuntimeError):
    """Raised when a PLC request cannot be served as asked."""


def review_code(
    source: str,
    *,
    dialect: PlcDialect = PlcDialect.IEC_61131_3,
) -> PlcValidationResult:
    """Validate code the caller already has.

    Args:
        source: The code to check.
        dialect: What flavour it is written in.

    Returns:
        The verdict, or an explicit non-verdict if checking failed.

    No generation happens. This is the path an engineer uses on their own
    code, which is why AI-009 keeps validation callable on its own.
    """
    return safe_validate(source, dialect)


def generate_code(request: PlcGenerationRequest) -> PlcGenerationResult:
    """Generate PLC code for a description, with its validation verdict.

    Args:
        request: What to generate.

    Returns:
        The generated code and the verdict on it.

    Raises:
        PlcError: If generation cannot be performed as asked.
    """
    try:
        return generate_plc_code(
            request,
            write_source=_write_source,
            write_ladder=_write_ladder,
            validate=safe_validate,
        )
    except GenerationError as exc:
        raise PlcError(str(exc)) from exc


def safe_validate(source: str, dialect: PlcDialect) -> PlcValidationResult:
    """Validate, turning a broken validator into an explicit non-verdict.

    Args:
        source: The code to check.
        dialect: What flavour it is.

    Returns:
        The verdict, or an ``INCOMPLETE`` one if validation itself failed.

    Catches broadly on purpose. Any exception at all means the code was not
    checked, and the caller must be told that rather than handed back code
    with nothing said about it. The exception is logged with a traceback so
    the defect is visible to whoever maintains the checker — swallowing it
    quietly would trade one silent failure for another.
    """
    try:
        return validate_plc_code(source, dialect=dialect)
    except Exception as exc:
        logger.exception("plc.validation_failed", dialect=dialect.value, error=str(exc))
        return PlcValidationResult(
            status=ValidationStatus.INCOMPLETE,
            dialect=dialect,
            checked_by=VALIDATION_UNAVAILABLE,
            findings=[
                ValidationFinding(
                    code=VALIDATION_UNAVAILABLE,
                    message=f"validation could not be completed: {exc.__class__.__name__}",
                    severity=FindingSeverity.WARNING,
                )
            ],
        )


def _write_source(request: PlcGenerationRequest) -> str:
    """Produce Structured Text for a request.

    Args:
        request: What to generate.

    Returns:
        The generated source.

    Raises:
        GenerationError: Always, for now.

    **Not implemented.** The model call belongs here, and wiring one in is a
    separate piece of work from the endpoint and the validation path this task
    delivers. It raises rather than returning a plausible stub: a stub would
    make the endpoint look finished, and would hand a caller a program no
    model wrote and no requirement described — wearing whatever verdict the
    validator happened to give it.
    """
    del request
    raise GenerationError(
        "code generation is not yet wired to a model; /plc/review validates existing code today"
    )


def _write_ladder(request: PlcGenerationRequest) -> list[LadderRung]:
    """Produce ladder rungs for a request.

    Args:
        request: What to generate.

    Returns:
        The generated rungs.

    Raises:
        GenerationError: Always, for now. See ``_write_source``.
    """
    del request
    raise GenerationError(
        "ladder generation is not yet wired to a model; /plc/review validates existing code today"
    )
