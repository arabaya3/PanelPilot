"""Background job registry.

The worker's equivalent of ``app/api/v1/routes/`` — and thin for the same
reason. A job handler opens a session, calls **one** function from
``app.domain``, and returns. No business logic here.

Adding a job is two steps: write the domain function, then register it below.
Nothing else enumerates jobs, so the schedule cannot drift from what exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.schemas.auth import CurrentUser


@dataclass(frozen=True)
class JobSpec:
    """A runnable background job.

    Attributes:
        name: Stable identifier used on the command line and in the schedule.
        description: One-line summary shown by ``--list``.
        handler: Callable taking the parsed job arguments and returning an
            exit code, 0 for success.
    """

    name: str
    description: str
    handler: Callable[[list[str]], int]


def run_crawl(args: list[str]) -> int:
    """Crawl one documentation source into staging.

    Delegates to ``app.domain.ingestion.create_crawl_job``. Writes reach the
    staging index only; nothing this job does can make content live.

    Args:
        args: Positional arguments, ``[source_id]``.

    Returns:
        ``0`` on success, non-zero on failure.
    """
    raise NotImplementedError


def run_reindex_staging(args: list[str]) -> int:
    """Re-chunk and re-embed the staging corpus in place.

    Run this after a chunking or embedding change. Production is untouched:
    re-verification and promotion follow separately. See
    docs/adr/0001-staging-vs-production-index.md.

    Args:
        args: Positional arguments, optionally ``[source_id]`` to limit scope.

    Returns:
        ``0`` on success, non-zero on failure.
    """
    raise NotImplementedError


def run_expire_stale_sources(args: list[str]) -> int:
    """Flag production documents whose upstream source has been superseded.

    Flags only — retraction is a reviewed operation, not an automated one.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` on success, non-zero on failure.
    """
    raise NotImplementedError


REGISTRY: dict[str, JobSpec] = {
    spec.name: spec
    for spec in (
        JobSpec("crawl", "Crawl one documentation source into staging.", run_crawl),
        JobSpec(
            "reindex-staging",
            "Re-chunk and re-embed the staging corpus after a pipeline change.",
            run_reindex_staging,
        ),
        JobSpec(
            "expire-stale-sources",
            "Flag production documents whose upstream source was superseded.",
            run_expire_stale_sources,
        ),
    )
}


def get_job(name: str) -> JobSpec:
    """Look up a job by name.

    Args:
        name: The job's registered identifier.

    Returns:
        The matching job spec.

    Raises:
        NotFoundError: If no job is registered under that name.
    """
    raise NotImplementedError


def system_actor() -> CurrentUser:
    """Return the identity background jobs act as.

    Jobs run unattended, so they need an explicit principal rather than an
    implicit one. This actor deliberately holds the ingestion role and **not**
    the reviewer role: no scheduled job can approve its own content.

    Returns:
        The system actor.
    """
    raise NotImplementedError
