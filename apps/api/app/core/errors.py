"""Framework-agnostic error types and their HTTP translation.

Domain and AI code raises these errors. The API layer installs handlers via
``install_exception_handlers`` that map them onto status codes, so no module
under ``app/domain`` or ``app/ai`` ever imports ``HTTPException``.
"""

from __future__ import annotations

from fastapi import FastAPI


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


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers translating ``PanelPilotError`` subclasses to responses.

    Args:
        app: The FastAPI application to register handlers on.
    """
    raise NotImplementedError
