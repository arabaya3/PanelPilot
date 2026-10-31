"""Engineering review decisions and the reviewers who record them (M3).

Thin by contract: parse, call one domain function, return. Who may record a
decision, and who designates reviewers, lives in
``app.domain.engineering_reviews``. A decision recorded here is never an
approval of a design and never makes anything ready for execution.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import engineering_reviews as reviews_domain
from app.models.schemas.engineering_reviews import (
    DecisionOut,
    DecisionRequest,
    DesignateRequest,
    ReviewerOut,
)

router = APIRouter()


@router.get("/decisions", response_model=list[DecisionOut])
def list_decisions(
    session: SessionDep,
    user: CurrentUserDep,
    subject_kind: Literal["template", "pattern", "rule"] = Query(),
    subject_ref: str = Query(min_length=1, max_length=160),
) -> list[DecisionOut]:
    """Every decision on a subject, this tenant's and global, newest first."""
    return reviews_domain.decisions(
        session=session, user=user, subject_kind=subject_kind, subject_ref=subject_ref
    )


@router.post("/decisions", response_model=DecisionOut, status_code=status.HTTP_201_CREATED)
def record_decision(
    payload: DecisionRequest, session: SessionDep, user: CurrentUserDep
) -> DecisionOut:
    """Record a decision; a designated reviewer only."""
    recorded = reviews_domain.record_decision(session=session, user=user, request=payload)
    session.commit()
    return recorded


@router.get("/reviewers", response_model=list[ReviewerOut])
def list_reviewers(session: SessionDep, user: CurrentUserDep) -> list[ReviewerOut]:
    """The current designations; the owner or an admin."""
    return reviews_domain.reviewers(session=session, user=user)


@router.post("/reviewers", response_model=ReviewerOut, status_code=status.HTTP_201_CREATED)
def designate(payload: DesignateRequest, session: SessionDep, user: CurrentUserDep) -> ReviewerOut:
    """Designate an account as a qualified engineering reviewer; the owner only."""
    created = reviews_domain.designate(
        session=session,
        user=user,
        member_id=payload.member_id,
        qualification_statement=payload.qualification_statement,
    )
    session.commit()
    return created


@router.delete("/reviewers/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke(member_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Revoke a designation; the owner only. Decisions already recorded stay."""
    reviews_domain.revoke(session=session, user=user, member_id=member_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
