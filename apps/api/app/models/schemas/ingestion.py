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
    """Request to queue a crawl.

    Attributes:
        source_id: Which allow-listed source to crawl.
        seed_urls: Listing pages to discover documents from.
        document_urls: Documents to fetch directly, skipping discovery. For
            sources whose listings cannot be crawled — ABB's is a JavaScript
            application serving no links — while the documents themselves are
            plainly fetchable. Supplying these skips discovery and nothing
            else: robots.txt, hashing, parsing and human verification all
            still apply.
        max_depth: How deep to follow listings.

    At least one of ``seed_urls`` or ``document_urls`` must be present; a
    request carrying neither has nothing to fetch, and the domain refuses it
    rather than recording an empty run as a success.
    """

    source_id: str
    seed_urls: list[str] = []
    document_urls: list[str] = []
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
