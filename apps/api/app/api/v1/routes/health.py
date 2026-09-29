"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.domain import health as health_domain
from app.models.schemas.health import DependencyState, HealthResponse

router = APIRouter()


# `async` on purpose. It touches no dependency, and a sync route needs a free
# thread from the pool every diagnosis also draws on: under load, liveness
# queued behind model calls and the container was restarted for being busy.
@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return health_domain.liveness()


@router.get("/ready", response_model=HealthResponse)
def readiness(response: Response) -> HealthResponse:
    # 503 when a dependency is down: container healthchecks and orchestrator
    # readiness gates key off the status code, not the body.
    result = health_domain.readiness()
    if result.status is not DependencyState.UP:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
