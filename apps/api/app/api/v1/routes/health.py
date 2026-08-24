"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.domain import health as health_domain
from app.models.schemas.health import HealthResponse

router = APIRouter()


@router.get("/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return health_domain.liveness()


@router.get("/ready", response_model=HealthResponse)
def readiness() -> HealthResponse:
    return health_domain.readiness()
