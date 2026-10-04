"""Plans and subscriptions.

Thin by contract: parse, call one domain function, return. What each plan
allows, and what an account is on, lives in ``app.domain.billing``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import billing
from app.models.schemas.billing import Entitlements, PlanCatalogue, PlanRequest

router = APIRouter()


@router.get("/plans", response_model=PlanCatalogue)
def plans() -> PlanCatalogue:
    """Every plan and its price; public, for the pricing page."""
    return billing.catalogue()


@router.get("/entitlements", response_model=Entitlements)
def entitlements(session: SessionDep, user: CurrentUserDep) -> Entitlements:
    """What the caller's account is on and has used."""
    return billing.entitlements(session=session, user=user)


@router.post("/request", response_model=Entitlements)
def request_plan(payload: PlanRequest, session: SessionDep, user: CurrentUserDep) -> Entitlements:
    """Ask to move the caller's account to a plan; an operator activates it."""
    result = billing.request_plan(session=session, user=user, request=payload)
    session.commit()
    return result
