"""Health reporting service."""

from __future__ import annotations

from app.models.schemas.health import HealthResponse


def liveness() -> HealthResponse:
    """Report that the process is running.

    Returns:
        A healthy response; never touches dependencies.
    """
    raise NotImplementedError


def readiness() -> HealthResponse:
    """Report whether the process can serve traffic.

    Checks the database and the OpenSearch production index.

    Returns:
        A response naming each dependency and its state.
    """
    raise NotImplementedError
