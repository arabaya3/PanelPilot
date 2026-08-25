"""Health reporting service.

Liveness answers "is this process running"; readiness answers "can it serve a
request". Keeping them distinct matters to the container runtime: a failing
readiness probe should stop traffic being routed here, while a failing liveness
probe should restart the process. Conflating them turns a transient database
blip into a restart loop.
"""

from __future__ import annotations

from sqlalchemy import text

from app.ai.retrieval.client import get_client
from app.core.db import create_engine_from_settings
from app.core.logging import get_logger
from app.models.schemas.health import DependencyState, HealthResponse

logger = get_logger(__name__)


def _check_database() -> DependencyState:
    """Report whether a trivial query succeeds against Postgres."""
    try:
        with create_engine_from_settings().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness_check_failed", dependency="database", error=str(exc))
        return DependencyState.DOWN
    return DependencyState.UP


def _check_opensearch() -> DependencyState:
    """Report whether the OpenSearch cluster answers a ping."""
    try:
        reachable = bool(get_client().ping())
    except Exception as exc:
        logger.warning("readiness_check_failed", dependency="opensearch", error=str(exc))
        return DependencyState.DOWN
    return DependencyState.UP if reachable else DependencyState.DOWN


def liveness() -> HealthResponse:
    """Report that the process is running.

    Returns:
        A healthy response; never touches dependencies, so a slow database
        cannot cause the container to be killed and restarted.
    """
    return HealthResponse(status=DependencyState.UP, dependencies={})


def readiness() -> HealthResponse:
    """Report whether the process can serve traffic.

    Checks the database and the OpenSearch cluster. Never raises: a probe that
    fails with a 500 tells the orchestrator less than one that reports which
    dependency is down.

    Returns:
        A response naming each dependency and its state. Overall status is
        ``UP`` only when every dependency is.
    """
    dependencies = {
        "database": _check_database(),
        "opensearch": _check_opensearch(),
    }
    healthy = all(state is DependencyState.UP for state in dependencies.values())
    return HealthResponse(
        status=DependencyState.UP if healthy else DependencyState.DOWN,
        dependencies=dependencies,
    )
