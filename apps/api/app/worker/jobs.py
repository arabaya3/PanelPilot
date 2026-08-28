"""Background job registry.

The worker's equivalent of ``app/api/v1/routes/`` — and thin for the same
reason. A job handler opens a session, calls **one** function from
``app.domain``, and returns. No business logic here.

Adding a job is two steps: write the domain function, then register it below.
Nothing else enumerates jobs, so the schedule cannot drift from what exists.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.models.schemas.auth import CurrentUser, Role

#: The principal unattended jobs act as. Fixed so a staged document always
#: names an ingester, and so no human can ever hold this identity.
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-00000000515e")
SYSTEM_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-0000000005a1")


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
        args: Positional arguments, ``[source_id, seed_url, ...]``. At least
            one seed URL is required — there is no stored source registry, so
            the entry points come from the command line or the API caller.

    Returns:
        ``0`` on success, non-zero on failure.

    Thin by contract: open a session, call one domain function, translate the
    outcome to an exit code. The crawl itself, including every decision about
    what may be fetched and what is written where, lives in
    ``app.domain.ingestion``.
    """
    from contextlib import closing

    from app.core.db import get_session
    from app.domain import ingestion as ingestion_domain
    from app.models.schemas.ingestion import CrawlJobRequest, CrawlJobStatus

    if len(args) < 2:
        print("usage: crawl <source_id> <seed_url> [seed_url ...]", file=sys.stderr)
        return 2

    source_id, *seed_urls = args
    request = CrawlJobRequest(source_id=source_id, seed_urls=seed_urls)

    # `get_session` is a FastAPI dependency generator, so it is driven by hand
    # here rather than reshaped into a context manager for one caller. `closing`
    # runs its finally block, which closes the connection.
    sessions = get_session()
    session = next(sessions)
    with closing(session):
        response = ingestion_domain.create_crawl_job(
            session=session, user=system_actor(), request=request
        )

    print(f"crawl {response.id}: {response.status.value}")
    # A FAILED job is a successful recording of a failure, but the process must
    # still exit non-zero: a scheduler that sees 0 will not alert, and a source
    # that silently stops returning documents is the exact failure BE-006's
    # staleness alerting exists to catch.
    return 0 if response.status is CrawlJobStatus.SUCCEEDED else 1


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
    spec = REGISTRY.get(name)
    if spec is None:
        known = ", ".join(sorted(REGISTRY))
        raise NotFoundError(f"no job named {name!r}; known jobs: {known}")
    return spec


def system_actor() -> CurrentUser:
    """Return the identity background jobs act as.

    Jobs run unattended, so they need an explicit principal rather than an
    implicit one. This actor deliberately holds the ingestion role and **not**
    the reviewer role: no scheduled job can approve its own content.

    Returns:
        The system actor.

    The id is a fixed, well-known UUID rather than a row in ``users``. Jobs
    have to name an ingester of record on everything they stage, and a
    scheduled crawl that depended on someone having created an account first
    would fail at 3am for a reason nobody would guess. Being a constant is also
    what makes the four-eyes rule hold: promotion refuses when the reviewer is
    the ingester, and no human can ever authenticate as this principal.
    """
    return CurrentUser(
        id=str(SYSTEM_ACTOR_ID),
        email="system@panelpilot.local",
        tenant_id=str(SYSTEM_TENANT_ID),
        # Ingestion only. Granting this actor the reviewer role would let a
        # scheduled job approve the content it just crawled, which is the whole
        # point of ADR 0001's human gate.
        roles=frozenset({Role.INGESTION}),
    )
