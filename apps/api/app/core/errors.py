"""Framework-agnostic error types and their HTTP translation.

Domain and AI code raises these errors. The API layer installs handlers via
``install_exception_handlers`` that map them onto status codes, so no module
under ``app/domain`` or ``app/ai`` ever imports ``HTTPException``.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class PanelPilotError(Exception):
    """Base class for every error this application raises deliberately."""


class NotFoundError(PanelPilotError):
    """A requested entity does not exist."""


class ValidationError(PanelPilotError):
    """Caller input is well-formed but unacceptable to the domain."""


class AuthenticationError(PanelPilotError):
    """The caller could not be identified."""


class AuthorizationError(PanelPilotError):
    """The caller is known but not permitted to perform the action."""


class InsufficientEvidenceError(PanelPilotError):
    """Retrieval produced no citable source, so the assistant must refuse.

    Raised by ``app.ai.guardrails``; see the cite-or-refuse invariant in the
    README.
    """


class PromotionError(PanelPilotError):
    """A staging-to-production content promotion was rejected."""


# The single place mapping domain failures to HTTP. Adding an error type
# without adding it here yields a 500, which is the correct default: an
# unmapped error is a bug, not a documented outcome.
STATUS_BY_ERROR: dict[type[PanelPilotError], HTTPStatus] = {
    NotFoundError: HTTPStatus.NOT_FOUND,
    ValidationError: HTTPStatus.UNPROCESSABLE_ENTITY,
    AuthenticationError: HTTPStatus.UNAUTHORIZED,
    AuthorizationError: HTTPStatus.FORBIDDEN,
    InsufficientEvidenceError: HTTPStatus.UNPROCESSABLE_ENTITY,
    PromotionError: HTTPStatus.CONFLICT,
}


def status_for(error: PanelPilotError) -> HTTPStatus:
    """Return the status code for an error, walking its base classes.

    Args:
        error: The raised domain error.

    Returns:
        The mapped status, or 500 when the type is not mapped.
    """
    for klass in type(error).__mro__:
        if klass in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[klass]
    return HTTPStatus.INTERNAL_SERVER_ERROR


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers translating ``PanelPilotError`` subclasses to responses.

    Starlette resolves handlers along the exception's MRO, so registering the
    base class covers every subclass — including ones added later.

    Args:
        app: The FastAPI application to register handlers on.
    """

    async def handle_panelpilot_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, PanelPilotError)
        status = status_for(exc)
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "detail": str(exc) or status.phrase},
        )

    app.add_exception_handler(PanelPilotError, handle_panelpilot_error)
