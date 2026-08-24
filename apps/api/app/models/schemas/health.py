"""Health-check schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DependencyState(StrEnum):
    """Reachability of a dependency."""

    UP = "up"
    DOWN = "down"


class HealthResponse(BaseModel):
    """Result of a liveness or readiness probe."""

    status: DependencyState
    dependencies: dict[str, DependencyState] = {}
