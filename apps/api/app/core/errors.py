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


class TooManyRequestsError(PanelPilotError):
    """The caller has made too many requests and must wait.

    Its own type rather than a ``ValidationError``: nothing is wrong with the
    request itself, and a 422 tells a well-behaved client to fix its payload
    rather than to slow down.
    """

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        """Record the message and, when known, how long to wait.

        Args:
            message: Human-readable explanation, including the wait.
            retry_after_seconds: Whole seconds until a retry can succeed,
                sent as ``Retry-After`` so a client need not parse the text.
        """
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class NotImplementedYetError(PanelPilotError):
    """The endpoint exists in the contract but its behaviour does not yet.

    Raised instead of ``NotImplementedError`` so a stub answers 501 with a
    reason, rather than an anonymous 500 that reads as the server breaking.
    """


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
    TooManyRequestsError: HTTPStatus.TOO_MANY_REQUESTS,
    NotImplementedYetError: HTTPStatus.NOT_IMPLEMENTED,
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
        headers: dict[str, str] = {}
        if isinstance(exc, TooManyRequestsError) and exc.retry_after_seconds is not None:
            # RFC 9110 §10.2.3: the machine-readable half of "please wait",
            # so a client backs off by the number rather than guessing.
            headers["Retry-After"] = str(exc.retry_after_seconds)
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "detail": str(exc) or status.phrase},
            headers=headers or None,
        )

    app.add_exception_handler(PanelPilotError, handle_panelpilot_error)
