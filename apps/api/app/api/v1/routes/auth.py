"""Authentication endpoints.

Thin by contract: parse, call one domain function, return. Every rule about who
may sign up, what a quota permits, and whether a trial session may be claimed
lives in ``app.domain.auth``.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import auth as auth_domain
from app.models.schemas.auth_flows import (
    LoginRequest,
    QuotaStatus,
    RefreshRequest,
    SignupRequest,
    TokenPair,
)

router = APIRouter()


@router.post("/signup", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, session: SessionDep) -> TokenPair:
    tokens = auth_domain.signup(
        session=session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        claim_session_id=payload.claim_session_id,
        claim_secret=payload.claim_secret,
    )
    session.commit()
    return tokens


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    tokens = auth_domain.login(session=session, email=payload.email, password=payload.password)
    session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    tokens = auth_domain.refresh(session=session, refresh_token=payload.refresh_token)
    session.commit()
    return tokens


@router.get("/quota", response_model=QuotaStatus)
def quota(session: SessionDep, user: CurrentUserDep) -> QuotaStatus:
    return auth_domain.get_quota(session=session, tenant_id=user.tenant_id)
