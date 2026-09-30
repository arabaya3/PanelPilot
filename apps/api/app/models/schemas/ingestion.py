"""Ingestion, verification, and promotion schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

#: Most listing pages one crawl request may name. The three sources each have
#: a handful of library entry points; twenty covers them with room to spare.
MAX_SEED_URLS = 20

#: Most documents one crawl request may name directly. Four times the
#: default per-run document cap, so a curated list can outgrow a single run
#: -- unchanged documents cost a fetch but stage nothing -- without becoming
#: unbounded.
MAX_DOCUMENT_URLS = 100


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

    Both lists are capped (``MAX_SEED_URLS``, ``MAX_DOCUMENT_URLS``). Each URL
    is a request the crawler makes, so an uncapped list is an uncapped crawl
    one POST away; the crawler also caps total fetches per run, but refusing
    an oversized request outright tells the caller rather than silently
    crawling a prefix of it.
    """

    source_id: str
    seed_urls: list[str] = Field(default=[], max_length=MAX_SEED_URLS)
    document_urls: list[str] = Field(default=[], max_length=MAX_DOCUMENT_URLS)
    max_depth: int = 2


class CrawlJobResponse(BaseModel):
    """A crawl job and where it has got to.

    Attributes:
        id: The job, for polling ``GET /ingestion/crawl-jobs/{id}``.
        status: ``queued`` until the worker picks it up.
        error: Why it failed, when it did.
    """

    id: str
    status: CrawlJobStatus
    error: str | None = None


class VerificationDecision(StrEnum):
    """A reviewer's decision on a staged document."""

    APPROVED = "approved"
    REJECTED = "rejected"


class VerificationVerdict(BaseModel):
    """A reviewer's decision plus their notes."""

    decision: VerificationDecision
    notes: str = ""


class PromotionResponse(BaseModel):
    """Result of a promotion, including the audit entry written."""

    production_document_id: str
    revision: int
    audit_id: str
