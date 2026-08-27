"""Aggregates every v1 route module into a single router.

New route files are registered here and nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import enforce_trial_rate_limit
from app.api.v1.routes import (
    auth,
    calculations,
    diagnostics,
    health,
    images,
    ingestion,
    search,
    verification,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# The diagnosis and upload paths carry the trial's cost — a model call and a
# stored file respectively — so they carry the per-source limit. Auth and
# health deliberately do not: throttling login would lock a shared site out of
# its own accounts, and throttling health checks would take a service out of
# rotation for being monitored.
api_router.include_router(
    diagnostics.router,
    prefix="/diagnostics",
    tags=["diagnostics"],
    dependencies=[Depends(enforce_trial_rate_limit)],
)
api_router.include_router(calculations.router, prefix="/calculations", tags=["calculations"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
api_router.include_router(verification.router, prefix="/verification", tags=["verification"])
api_router.include_router(
    images.router,
    prefix="/images",
    tags=["images"],
    dependencies=[Depends(enforce_trial_rate_limit)],
)
