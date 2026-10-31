"""Authentication endpoints.

Thin by contract: parse, call one domain function, return. Every rule about who
may sign up, what a quota permits, and whether a trial session may be claimed
lives in ``app.domain.auth``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    AppSettingsDep,
    BearerClaimsDep,
    CurrentUserDep,
    OptionalCurrentUserDep,
    SessionDep,
    enforce_login_rate_limit,
    enforce_signup_rate_limit,
    enforce_trial_resume_rate_limit,
    enforce_trial_start_rate_limit,
    require_same_site_request,
)
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.domain import auth as auth_domain
from app.models.schemas.auth_flows import (
    MAX_REFRESH_TOKEN_LENGTH,
    AccessToken,
    LoginRequest,
    QuotaStatus,
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
    response_model=AccessToken,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_signup_rate_limit)],
)
def signup(
    payload: SignupRequest, response: Response, session: SessionDep, settings: AppSettingsDep
) -> AccessToken:
    tokens = auth_domain.signup(
        session=session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        claim_session_id=payload.claim_session_id,
        claim_secret=payload.claim_secret,
        invite_token=payload.invite_token,
    )
    session.commit()
    return _signed_in(response, tokens, settings)


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
    dependencies=[Depends(enforce_trial_resume_rate_limit)],
)
def resume_trial(payload: TrialResumeRequest, session: SessionDep) -> TrialStart:
    """Mint a fresh access token for a trial this browser already started.

    Shares the trial-start budget: to an abuser, resuming a known trial and
    starting a new one are the same request.
    """
    return auth_domain.resume_trial(
        session=session, session_id=payload.session_id, claim_secret=payload.claim_secret
    )


@router.post("/login", response_model=AccessToken, dependencies=[Depends(enforce_login_rate_limit)])
def login(
    payload: LoginRequest, response: Response, session: SessionDep, settings: AppSettingsDep
) -> AccessToken:
    """Sign in: the access token in the body, the refresh token in the session cookie."""
    tokens = auth_domain.login(session=session, email=payload.email, password=payload.password)
    session.commit()
    return _signed_in(response, tokens, settings)


# --- the refresh session ------------------------------------------------------
#
# The refresh token lives in one cookie, and only these endpoints read it:
#
# * HttpOnly: no script on the page can read it, so an XSS cannot carry the
#   credential that outlives every access token off the machine.
# * Secure outside dev (`Settings.refresh_cookie_secure`).
# * SameSite=Strict: the cookie is only ever needed by a `fetch` the web app
#   itself makes, which is a same-site request however the page was reached,
#   so nothing legitimate needs the laxer setting. Lax would also send it on a
#   cross-site top-level navigation, which these POST-only endpoints never
#   serve but there is no reason to allow.
# * Name and Path depend on Secure. With Secure: `__Host-pp_refresh`, Path=/,
#   no Domain. The prefix makes the browser refuse any such cookie not set by
#   this exact host over HTTPS, so a sibling subdomain cannot plant (toss) a
#   session of its choosing. `__Host-` requires Path=/, so the browser sends it
#   everywhere; only these endpoints read it, and the web proxy forwards it to
#   them alone. Without Secure (dev over http, where `__Host-` is refused):
#   `pp_refresh` on Path=/api/v1/auth/session.
# * Max-Age: the refresh token's own lifetime.
#
# Every endpoint here also requires a same-site request (`app.api.csrf`).

SESSION_COOKIE = "pp_refresh"
SECURE_SESSION_COOKIE = "__Host-pp_refresh"
# Under this router's `/auth` mount: `/api/v1/auth/session/...`.
SESSION_PATH = "/session"


def _cookie_name(settings: Settings) -> str:
    return SECURE_SESSION_COOKIE if settings.refresh_cookie_secure else SESSION_COOKIE


def _cookie_path(settings: Settings) -> str:
    if settings.refresh_cookie_secure:
        return "/"
    return f"{settings.api_v1_prefix}/auth{SESSION_PATH}"


def _set_session_cookie(response: Response, *, token: str, settings: Settings) -> None:
    response.set_cookie(
        _cookie_name(settings),
        token,
        max_age=int(auth_domain.REFRESH_TOKEN_TTL.total_seconds()),
        path=_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        _cookie_name(settings),
        path=_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _signed_in(response: Response, tokens: TokenPair, settings: Settings) -> AccessToken:
    """Put the refresh token in the cookie and answer with the access token only."""
    _set_session_cookie(response, token=tokens.refresh_token, settings=settings)
    return AccessToken(access_token=tokens.access_token, expires_in=tokens.expires_in)


def _session_cookie(request: Request, settings: Settings) -> str | None:
    """The refresh cookie's value, if one came and it is not oversized."""
    value = request.cookies.get(_cookie_name(settings))
    if not value or len(value) > MAX_REFRESH_TOKEN_LENGTH:
        return None
    return value


@router.post(
    f"{SESSION_PATH}/refresh",
    response_model=AccessToken,
    dependencies=[Depends(require_same_site_request)],
)
def refresh(
    request: Request, session: SessionDep, settings: AppSettingsDep, bearer: BearerClaimsDep
) -> Response:
    """Exchange the refresh cookie for a new access token, rotating the cookie.

    The page sends its access token too, when it holds one: proof that it is
    the session's own holder, which lets a jar a late Set-Cookie moved back
    one rotation recover (see `auth.refresh`). It is optional, and an
    expired or foreign one is no proof rather than a refusal.

    What a page calls on load to find out whether this browser is signed in,
    and after a 401. No cookie, or one that no longer refreshes, is a 401 and
    clears the cookie, so the browser stops sending a dead credential.

    The JSON-body refresh this replaced was used by the web app only; there
    is no non-browser client, so it was removed rather than kept beside the
    cookie.
    """
    token = _session_cookie(request, settings)
    if token is None:
        raise AuthenticationError("no sign-in session", code="session_ended")
    try:
        tokens = auth_domain.refresh(session=session, refresh_token=token, bearer=bearer)
    except AuthenticationError as exc:
        # The refusal's own body, with the cookie cleared. Raising would lose
        # the Set-Cookie: the error handler builds a fresh response.
        refused = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "AuthenticationError",
                "detail": str(exc),
                "code": exc.code or "session_ended",
                "params": exc.params,
            },
        )
        _clear_session_cookie(refused, settings=settings)
        return refused
    session.commit()
    answer = JSONResponse(
        content=AccessToken(
            access_token=tokens.access_token, expires_in=tokens.expires_in
        ).model_dump()
    )
    if tokens.sets_cookie:
        _set_session_cookie(answer, token=tokens.refresh_token, settings=settings)
    return answer


@router.post(
    f"{SESSION_PATH}/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_same_site_request)],
)
def logout(
    request: Request,
    session: SessionDep,
    settings: AppSettingsDep,
    user: OptionalCurrentUserDep,
) -> Response:
    """Sign out: end this sign-in on the server and clear the cookie.

    Ends the session the access token names and the one the cookie belongs
    to: their refresh tokens stop refreshing, and every access token of the
    session is refused from the next request, not at its expiry. Other
    sign-ins of the account are left alone. Works with only the cookie, since
    the access token may already have expired. Always 204, so a retry never
    fails and the answer says nothing about either credential.
    """
    auth_domain.logout(
        session=session, caller=user, refresh_token=_session_cookie(request, settings)
    )
    session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, settings=settings)
    return response


@router.post(
    f"{SESSION_PATH}/logout-everywhere",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_same_site_request)],
)
def logout_everywhere(
    session: SessionDep, settings: AppSettingsDep, user: CurrentUserDep
) -> Response:
    """End every sign-in of the caller's account, on every device, and clear the cookie.

    Needs a live access token: ending other devices' sessions is a deliberate
    act of the signed-in account, not something a cookie alone may ask for.
    """
    auth_domain.logout_everywhere(session=session, caller=user)
    session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, settings=settings)
    return response


@router.get("/quota", response_model=QuotaStatus)
def quota(session: SessionDep, user: CurrentUserDep) -> QuotaStatus:
    return auth_domain.get_quota(session=session, tenant_id=user.tenant_id)
