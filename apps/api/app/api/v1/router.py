"""Aggregates every v1 route module into a single router.

New route files are registered here and nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import enforce_trial_rate_limit, enforce_trial_rate_limit_on_writes
from app.api.v1.routes import (
    auth,
    calculations,
    design,
    diagnostics,
    feedback,
    health,
    images,
    ingestion,
    plc,
    search,
    verification,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
# Auth carries its own, per-endpoint limits (see routes/auth.py), each in a
# namespace of its own. The trial-path limit below would be the wrong budget:
# a burst of logins must not lock a site out of asking questions, nor the
# reverse. Health is never throttled: throttling health checks would take a
# service out of rotation for being monitored.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# The diagnosis and upload paths carry the trial's cost — a model call and a
# stored file respectively — so they carry the per-source limit. Only the
# writes, though: `GET /diagnostics/{session_id}` reads the caller's own
# conversation and costs nothing, and a reload must not spend the allowance
# for asking questions.
api_router.include_router(
    diagnostics.router,
    prefix="/diagnostics",
    tags=["diagnostics"],
    dependencies=[Depends(enforce_trial_rate_limit_on_writes)],
)
# Outside the diagnostics prefix, and so outside its trial rate limit: see
# `sessions_router` for why reading your own conversation list is not throttled
# like asking a question is.
api_router.include_router(diagnostics.sessions_router, prefix="/sessions", tags=["diagnostics"])
api_router.include_router(calculations.router, prefix="/calculations", tags=["calculations"])
api_router.include_router(design.router, prefix="/design", tags=["design"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
api_router.include_router(verification.router, prefix="/verification", tags=["verification"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
# Unauthenticated by design — the README advertises PLC review as usable with
# no account — and CPU-heavy, so with no account to hold to a quota the
# per-source limit is the only thing between one caller and the whole box.
api_router.include_router(
    plc.router,
    prefix="/plc",
    tags=["plc"],
    dependencies=[Depends(enforce_trial_rate_limit)],
)
api_router.include_router(
    images.router,
    prefix="/images",
    tags=["images"],
    dependencies=[Depends(enforce_trial_rate_limit)],
)
