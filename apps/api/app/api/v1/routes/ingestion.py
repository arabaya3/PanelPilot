"""Ingestion and content-review endpoints.

These endpoints operate on the *staging* index. There is deliberately no
endpoint here that writes to the production index: a chunk is published when a
reviewer labels it correct (``POST /verification/items/{id}/label``), through
``app.domain.promotion``. See docs/adr/0001-staging-vs-production-index.md.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUserDep, SessionDep
from app.domain import ingestion as ingestion_domain
from app.models.schemas.ingestion import CrawlJobRequest, CrawlJobResponse

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
