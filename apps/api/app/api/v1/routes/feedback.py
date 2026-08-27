"""Post-launch feedback: reporting an answer as wrong.

One endpoint, and its whole job is to record what the user was looking at
before that becomes unrecoverable. The retrieved passages arrive in the
request rather than being re-fetched here, because retrieval over a growing
index does not return the same passages for the same question later — and a
reviewer shown a fresh retrieval would be judging an answer nobody gave.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import feedback as feedback_domain
from app.models.schemas.feedback import FlagRequest, FlagResponse

router = APIRouter()


@router.post("/flag", response_model=FlagResponse, status_code=status.HTTP_201_CREATED)
def flag_answer(
    payload: FlagRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> FlagResponse:
    """Record a flagged answer and queue it for verification.

    Raises:
        HTTPException: 404 if the turn does not exist or belongs to another
            tenant.
    """
    try:
        flag = feedback_domain.flag_answer(
            session=session,
            turn_id=payload.message_id,
            tenant_id=UUID(user.tenant_id),
            flagged_by_id=UUID(user.id),
            retrieved=payload.retrieved,
            reason=payload.reason,
        )
    except feedback_domain.FeedbackError as exc:
        # Both causes report 404, deliberately. Distinguishing "no such turn"
        # from "not yours" would let a caller probe for the existence of other
        # tenants' turns by watching which code comes back.
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    session.commit()
    return FlagResponse(flag_id=flag.id, queued=True)
