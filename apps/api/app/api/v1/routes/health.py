"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.domain import health as health_domain
from app.models.schemas.health import DependencyState, HealthResponse

router = APIRouter()


@router.get("/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return health_domain.liveness()


@router.get("/ready", response_model=HealthResponse)
def readiness(response: Response) -> HealthResponse:
    # 503 when a dependency is down: container healthchecks and orchestrator
    # readiness gates key off the status code, not the body.
    result = health_domain.readiness()
    if result.status is not DependencyState.UP:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
