"""Authentication endpoints.

Thin by contract: parse, call one domain function, return. Every rule about who
may sign up, what a quota permits, and whether a trial session may be claimed
lives in ``app.domain.auth``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    CurrentUserDep,
    SessionDep,
    enforce_login_rate_limit,
    enforce_signup_rate_limit,
    enforce_trial_start_rate_limit,
)
from app.domain import auth as auth_domain
from app.models.schemas.auth_flows import (
    LoginRequest,
    QuotaStatus,
    RefreshRequest,
    SignupRequest,
    TokenPair,
    TrialResumeRequest,
    TrialStart,
)

router = APIRouter()

# Each throttled endpoint has its own namespace and budget; see
# app.domain.rate_limit. Refresh and quota are not throttled: refresh already
# requires a live single-use token, and quota is a read of the caller's own
# tenant.


@router.post(
    "/signup",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_signup_rate_limit)],
)
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


@router.post(
    "/trial",
    response_model=TrialStart,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_trial_start_rate_limit)],
)
def start_trial(session: SessionDep) -> TrialStart:
    """Begin an anonymous trial.

    Takes no body and no credentials — requiring either would put a form in
    front of the product, which is the funnel this deliberately does not have.
    """
    trial = auth_domain.start_trial(session=session)
    session.commit()
    return trial


@router.post(
    "/trial/resume",
    response_model=TrialStart,
    dependencies=[Depends(enforce_trial_start_rate_limit)],
)
def resume_trial(payload: TrialResumeRequest, session: SessionDep) -> TrialStart:
    """Mint a fresh access token for a trial this browser already started.

    Shares the trial-start budget: to an abuser, resuming a known trial and
    starting a new one are the same request.
    """
    return auth_domain.resume_trial(
        session=session, session_id=payload.session_id, claim_secret=payload.claim_secret
    )


@router.post("/login", response_model=TokenPair, dependencies=[Depends(enforce_login_rate_limit)])
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
