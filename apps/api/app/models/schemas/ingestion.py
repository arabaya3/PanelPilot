"""Ingestion, verification, and promotion schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CrawlJobStatus(StrEnum):
    """Lifecycle state of a crawl job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CrawlJobRequest(BaseModel):
    """Request to queue a crawl."""

    source_id: str
    seed_urls: list[str] = []
    max_depth: int = 2


class CrawlJobResponse(BaseModel):
    """A queued crawl job."""

    id: str
    status: CrawlJobStatus


class VerificationDecision(StrEnum):
    """A reviewer's decision on a staged document."""

    APPROVED = "approved"
    REJECTED = "rejected"


class VerificationVerdict(BaseModel):
    """A reviewer's decision plus their notes."""

    decision: VerificationDecision
    notes: str = ""


class VerificationItem(BaseModel):
    """A staged document awaiting or holding a review decision."""

    id: str
    staged_document_id: str
    claimed_by: str | None = None
    verdict: VerificationVerdict | None = None


class VerificationQueuePage(BaseModel):
    """A page of the verification queue."""

    items: list[VerificationItem]
    next_cursor: str | None = None


class PromotionRequest(BaseModel):
    """Request to make a verified staged document live."""

    staged_document_id: str
    notes: str = ""


class PromotionResponse(BaseModel):
    """Result of a promotion, including the audit entry written."""

    production_document_id: str
    revision: int
    audit_id: str
