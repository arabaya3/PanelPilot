"""Generating PLC code, and refusing to call it ready until a parser agrees.

Generation and validation are separate on purpose. The model produces code;
something that is not the model decides whether it is sound. Asking the writer
to mark its own work is the failure this task exists to prevent, and it is
worth being structural about rather than merely intended — so the validator
here is injected, and the result always carries its verdict.

Ladder comes back as structured rungs rather than as text. FE-009 draws it,
and a diagram reconstructed by parsing prose can silently disagree with what
the generator meant. The rungs are also what the validation runs against,
after being rendered to equivalent ST — a rung is a boolean expression driving
a coil, so the same parser answers both.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from app.ai.plc.validation import validate_plc_code
from app.models.schemas.plc import (
    LadderContact,
    LadderRung,
    PlcDialect,
    PlcGenerationRequest,
    PlcGenerationResult,
    PlcLanguage,
    PlcValidationResult,
)

logger = structlog.get_logger(__name__)

#: Produces source for a request. Injected so generation can be exercised
#: without a model call, and so the model is never the thing that also judges.
CodeWriter = Callable[[PlcGenerationRequest], str]

#: Produces ladder rungs for a request.
LadderWriter = Callable[[PlcGenerationRequest], list[LadderRung]]

#: Checks source. Injected for the same reason the writer is.
Validator = Callable[[str, PlcDialect], PlcValidationResult]


class GenerationError(RuntimeError):
    """Raised when a request cannot be generated as asked."""


def generate_plc_code(
    request: PlcGenerationRequest,
    *,
    write_source: CodeWriter | None = None,
    write_ladder: LadderWriter | None = None,
    validate: Validator | None = None,
) -> PlcGenerationResult:
    """Generate code for a request and validate it before returning.

    Args:
        request: What to generate.
        write_source: Produces Structured Text. Required for an ST request.
        write_ladder: Produces ladder rungs. Required for a ladder request.
        validate: Checks the result. Defaults to the parser-based validator.

    Returns:
        The generated code with its validation verdict attached.

    Raises:
        GenerationError: If no writer was supplied for the requested language.

    The validation is not optional and not a separate step the caller may
    forget. A ``PlcGenerationResult`` cannot be constructed without one, so
    there is no path that returns generated code carrying no verdict.
    """
    check = validate or (lambda source, dialect: validate_plc_code(source, dialect=dialect))

    if request.language is PlcLanguage.STRUCTURED_TEXT:
        if write_source is None:
            raise GenerationError("a Structured Text request needs a source writer")
        source = write_source(request)
        result = check(source, request.dialect)
        logger.info(
            "plc.generated",
            language=request.language.value,
            dialect=request.dialect.value,
            status=result.status.value,
            ready=result.ready,
        )
        return PlcGenerationResult(
            language=request.language,
            dialect=request.dialect,
            source=source,
            validation=result,
        )

    if write_ladder is None:
        raise GenerationError("a ladder request needs a ladder writer")

    rungs = write_ladder(request)
    # Validated through its ST equivalent rather than by a second checker
    # written for ladder. A rung is a boolean expression driving a coil, so
    # the same grammar answers both — and a separate ladder checker would be a
    # second thing to keep in agreement with the first.
    result = check(render_rungs_as_st(rungs, program_name="Generated"), request.dialect)
    logger.info(
        "plc.generated",
        language=request.language.value,
        dialect=request.dialect.value,
        rungs=len(rungs),
        status=result.status.value,
        ready=result.ready,
    )
    return PlcGenerationResult(
        language=request.language,
        dialect=request.dialect,
        rungs=rungs,
        validation=result,
    )


def render_rungs_as_st(rungs: list[LadderRung], *, program_name: str) -> str:
    """Render ladder rungs as the Structured Text they are equivalent to.

    Args:
        rungs: The rungs to render.
        program_name: Name for the generated program.

    Returns:
        Structured Text.

    Raises:
        GenerationError: If a rung uses a contact kind that has no meaning.

    Exists so ladder gets the same parser-backed check as ST rather than a
    weaker one. Every tag becomes a BOOL, which is what contacts and coils
    are; a normally-closed contact becomes ``NOT tag``, which is what it does.
    """
    tags: list[str] = []
    for rung in rungs:
        for contact in [*rung.inputs, rung.output]:
            if contact.tag not in tags:
                tags.append(contact.tag)

    lines = [f"PROGRAM {program_name}", "VAR"]
    lines.extend(f"    {tag} : BOOL;" for tag in tags)
    lines.append("END_VAR")

    for rung in rungs:
        if rung.output.kind != "coil":
            raise GenerationError(f"rung output {rung.output.tag!r} is not a coil")
        lines.append(f"    (* {rung.comment} *)")
        condition = " AND ".join(_contact_expression(contact) for contact in rung.inputs)
        lines.append(f"    {rung.output.tag} := {condition or 'TRUE'};")

    lines.append("END_PROGRAM")
    return "\n".join(lines)


def _contact_expression(contact: LadderContact) -> str:
    """Render one contact as a boolean expression.

    Args:
        contact: The contact.

    Returns:
        Its expression.

    Raises:
        GenerationError: If the contact kind is not one of the two that exist.
    """
    if contact.kind == "no":
        return contact.tag
    if contact.kind == "nc":
        return f"NOT {contact.tag}"
    raise GenerationError(f"contact {contact.tag!r} has unknown kind {contact.kind!r}")
