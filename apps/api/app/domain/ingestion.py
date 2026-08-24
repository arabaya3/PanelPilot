"""Ingestion service.

Schedules crawl jobs and exposes the human verification queue. Everything this
module writes lands in staging; it has no capability to touch production.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.schemas.auth import CurrentUser
from app.models.schemas.ingestion import (
    CrawlJobRequest,
    CrawlJobResponse,
    VerificationQueuePage,
)


def create_crawl_job(
    *,
    session: Session,
    user: CurrentUser,
    request: CrawlJobRequest,
) -> CrawlJobResponse:
    """Queue a crawl of a manufacturer documentation source.

    Args:
        session: Open database session.
        user: The authenticated caller; must hold the ingestion role.
        request: Source identifier, seed URLs, and crawl depth.

    Returns:
        The queued job with its identifier and initial status.

    Raises:
        AuthorizationError: If the caller lacks the ingestion role.
        ValidationError: If the source is not on the allowed-source list.
    """
    raise NotImplementedError


def list_verification_queue(
    *,
    session: Session,
    user: CurrentUser,
    limit: int,
    cursor: str | None,
) -> VerificationQueuePage:
    """List staged documents awaiting human verification.

    Args:
        session: Open database session.
        user: The authenticated caller; must hold the reviewer role.
        limit: Maximum number of items to return.
        cursor: Opaque pagination cursor from a previous page.

    Returns:
        A page of pending items, newest first.

    Raises:
        AuthorizationError: If the caller lacks the reviewer role.
    """
    raise NotImplementedError
