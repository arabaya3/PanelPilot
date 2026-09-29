"""Schematic specification endpoint (PD-007).

Thin by contract: parse the schedule, call one domain function, return.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep
from app.domain import schematics as schematics_domain
from app.models.schemas.schematic import SchematicRequest, SchematicSpec

router = APIRouter()


@router.post("", response_model=SchematicSpec)
def build_schematic(payload: SchematicRequest, user: CurrentUserDep) -> SchematicSpec:
    """Turn a panel schedule into what the single-line diagram draws.

    Authenticated but not rate-limited: it reads nothing and calls no model,
    so it costs a validation pass. A schedule whose wiring is ambiguous is a
    422 listing every problem.
    """
    del user  # Authentication is the gate; the specification is not per-tenant.
    return schematics_domain.build_schematic(payload)
