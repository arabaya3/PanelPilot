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

from collections.abc import Callable, Sequence

import structlog

from app.ai.plc.validation import validate_plc_code
from app.models.schemas.plc import (
    LadderBlock,
    LadderBranch,
    LadderContact,
    LadderElement,
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
        GenerationError: If a rung uses an element that has no ST equivalent.

    Exists so ladder gets the same parser-backed check as ST rather than a
    weaker one. Series is AND, a parallel branch is OR, and a normally-closed
    contact is NOT — which is what each of them means on a rung.

    Function blocks are the exception: a timer is state over time, and no
    expression captures it. The block's output tag is read as a plain BOOL,
    which is exactly what the rung downstream of it does. The block's own
    correctness is not checked here, and this does not pretend otherwise.
    """
    tags: list[str] = []
    for rung in rungs:
        for tag in _tags_in(rung):
            if tag not in tags:
                tags.append(tag)

    lines = [f"PROGRAM {program_name}", "VAR"]
    lines.extend(f"    {tag} : BOOL;" for tag in tags)
    lines.append("END_VAR")

    for rung in rungs:
        if rung.output.kind != "coil":
            raise GenerationError(f"rung output {rung.output.tag!r} is not a coil")
        lines.append(f"    (* {rung.comment} *)")
        condition = _series_expression(rung.elements)
        lines.append(f"    {rung.output.tag} := {condition};")

    lines.append("END_PROGRAM")
    return "\n".join(lines)


def _tags_in(rung: LadderRung) -> list[str]:
    """Collect every tag a rung names, in order.

    Args:
        rung: The rung to walk.

    Returns:
        Its tags, including those inside branches and blocks.
    """
    found: list[str] = []

    def walk(elements: Sequence[LadderElement]) -> None:
        """Collect tags from one series of elements.

        Args:
            elements: The elements to walk.
        """
        for element in elements:
            if isinstance(element, LadderBranch):
                for path in element.paths:
                    walk(path)
            else:
                found.append(element.tag)

    walk(rung.elements)
    found.append(rung.output.tag)
    return found


def _series_expression(elements: Sequence[LadderElement]) -> str:
    """Render elements in series as a conjunction.

    Args:
        elements: The elements, left to right.

    Returns:
        Their combined expression, or ``TRUE`` when there are none.

    An empty series is an always-on coil, which is legal ladder — an enable
    bit, a permanently energised lamp — so it renders as something that
    parses rather than as an empty condition.
    """
    parts = [_element_expression(element) for element in elements]
    return " AND ".join(parts) if parts else "TRUE"


def _element_expression(element: LadderElement) -> str:
    """Render one rung element as a boolean expression.

    Args:
        element: The element.

    Returns:
        Its expression.

    Raises:
        GenerationError: If a contact kind has no meaning.
    """
    if isinstance(element, LadderBranch):
        # Parallel paths are an OR, parenthesised so a following series
        # element does not bind tighter than the branch it follows.
        paths = [_series_expression(path) for path in element.paths]
        return "(" + " OR ".join(paths) + ")" if paths else "TRUE"
    if isinstance(element, LadderBlock):
        # Read the block's output. See `render_rungs_as_st` on why its
        # internals are not modelled.
        return element.tag
    return _contact_expression(element)


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
