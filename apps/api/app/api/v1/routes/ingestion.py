"""Ingestion and content-review endpoints.

These endpoints operate on the *staging* index and the human verification
queue. There is deliberately no endpoint that writes to the production index;
promotion happens only through ``app.domain.promotion.promote_document`` after
human approval. See docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import ingestion as ingestion_domain
from app.domain import promotion as promotion_domain
from app.models.schemas.ingestion import (
    CrawlJobRequest,
    CrawlJobResponse,
    PromotionRequest,
    PromotionResponse,
    VerificationQueuePage,
)

router = APIRouter()


# 202, not 200: the crawl is queued for the worker, not done. Poll the job.
@router.post("/crawl-jobs", response_model=CrawlJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_crawl_job(
    payload: CrawlJobRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> CrawlJobResponse:
    job = ingestion_domain.create_crawl_job(session=session, user=user, request=payload)
    session.commit()
    return job


@router.get("/crawl-jobs/{job_id}", response_model=CrawlJobResponse)
def get_crawl_job(job_id: str, session: SessionDep, user: CurrentUserDep) -> CrawlJobResponse:
    return ingestion_domain.get_crawl_job(session=session, user=user, job_id=job_id)


@router.get("/verification-queue", response_model=VerificationQueuePage)
def list_verification_queue(
    session: SessionDep,
    user: CurrentUserDep,
    limit: int = 50,
    cursor: str | None = None,
) -> VerificationQueuePage:
    return ingestion_domain.list_verification_queue(
        session=session, user=user, limit=limit, cursor=cursor
    )


@router.post("/promotions", response_model=PromotionResponse)
def promote_document(
    payload: PromotionRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> PromotionResponse:
    return promotion_domain.promote_document(session=session, reviewer=user, request=payload)
