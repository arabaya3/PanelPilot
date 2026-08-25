"""Aggregates every v1 route module into a single router.

New route files are registered here and nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    calculations,
    diagnostics,
    health,
    ingestion,
    search,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
api_router.include_router(calculations.router, prefix="/calculations", tags=["calculations"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
