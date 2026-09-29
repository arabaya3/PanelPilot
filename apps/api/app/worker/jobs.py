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
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.errors import NotFoundError
from app.models.schemas.auth import CurrentUser, Role

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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
        args: Positional arguments, ``[source_id, seed_url, ...]``. With no
            seed URL the source's curated document list is crawled
            (``app.ingestion.known_documents``); a source with neither is
            refused by the domain with a message saying so.

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

    if len(args) < 1:
        print("usage: crawl <source_id> [seed_url ...]", file=sys.stderr)
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
    """Re-embed the staging corpus in place: ``reindex-staging [source_id]``.

    Run after an embedding-model change. Production is untouched: live chunks
    keep their vectors until a reviewer re-promotes them. Re-chunking is a
    re-crawl (``crawl``), not this job — staging holds chunks, not documents.
    See ``app.domain.staging_maintenance.reembed_staging``.

    Args:
        args: Optionally ``[source_id]`` to limit scope.

    Returns:
        ``0`` on success, ``1`` if embedding failed part-way (safe to re-run),
        ``2`` for a usage error.
    """
    from app.core.errors import PanelPilotError
    from app.domain import staging_maintenance

    if len(args) > 1:
        print("usage: reindex-staging [source_id]", file=sys.stderr)
        return 2
    source_id = args[0] if args else None

    with _session() as session:
        try:
            count = staging_maintenance.reembed_staging(session=session, source_id=source_id)
        except PanelPilotError as exc:
            # EmbeddingError, named by its base: the worker is barred from
            # importing app.ai, and every provider failure derives from this.
            print(
                f"re-embedding stopped: {exc}. Re-run to finish; it is idempotent.", file=sys.stderr
            )
            return 1

    print(f"re-embedded {count} staging chunks")
    return 0


#: Exit code when live content is stale. Distinct from 1 (the job failed) so a
#: scheduler can alert on "someone needs to review this" without treating it
#: as a broken job, and vice versa.
EXIT_STALE_CONTENT_FOUND = 3


def run_expire_stale_sources(args: list[str]) -> int:
    """Flag production chunks whose upstream document has since changed.

    Flags only — retraction is a reviewed operation, not an automated one. Each
    stale chunk is printed and logged; the exit code tells a scheduler whether
    anything needs a reviewer.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` when production is current, ``3`` when stale chunks were found.
    """
    del args
    from app.domain import staging_maintenance

    with _session() as session:
        stale = staging_maintenance.find_superseded(session=session)

    for item in stale:
        print(f"stale: {item.chunk_id}  ({item.source_url} changed since verification)")
    print(f"{len(stale)} live chunks cite a document that has changed")
    return EXIT_STALE_CONTENT_FOUND if stale else 0


def run_grant_role(args: list[str]) -> int:
    """Give an account a role: ``grant-role <email> <role>``.

    The only way to make someone a reviewer or ingester. Deliberately an
    operator command rather than an API route: no request can elevate its own
    caller, so a compromised account cannot grant itself the reviewer role
    that publishing to production requires.

    Args:
        args: ``[email, role]``, the role one of ``engineer``, ``reviewer``,
            ``ingestion``, ``admin``.

    Returns:
        ``0`` on success, ``2`` for a usage error or unknown role, ``1`` if
        the account does not exist.
    """
    return _change_role(args, grant=True)


def run_revoke_role(args: list[str]) -> int:
    """Take a role away: ``revoke-role <email> <role>``. Effective immediately.

    Args:
        args: ``[email, role]``.

    Returns:
        ``0`` on success, ``2`` for a usage error, ``1`` if the account does
        not exist or the role cannot be revoked.
    """
    return _change_role(args, grant=False)


def _change_role(args: list[str], *, grant: bool) -> int:
    """Shared body of ``grant-role`` and ``revoke-role``.

    Args:
        args: ``[email, role]``.
        grant: Grant when true, revoke when false.

    Returns:
        The process exit code.
    """
    from app.core.errors import NotFoundError, ValidationError
    from app.domain import auth as auth_domain

    verb = "grant-role" if grant else "revoke-role"
    if len(args) != 2:
        print(f"usage: {verb} <email> <role>", file=sys.stderr)
        return 2
    email, role_name = args
    try:
        role = Role(role_name)
    except ValueError:
        known = ", ".join(r.value for r in Role)
        print(f"unknown role {role_name!r}; known roles: {known}", file=sys.stderr)
        return 2

    change = auth_domain.grant_role if grant else auth_domain.revoke_role
    with _session() as session:
        try:
            held = change(session=session, email=email, role=role)
        except (NotFoundError, ValidationError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        session.commit()

    print(f"{email}: {', '.join(sorted(r.value for r in held))}")
    return 0


def run_assign_verification(args: list[str]) -> int:
    """Hand today's verification batches to every reviewer.

    Run daily. Crawled chunks sit unassigned — and so in nobody's queue — until
    this runs, which is why a crawl alone never shows up in the dashboard.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` on success, ``1`` if no account holds the reviewer role.
    """
    del args
    from app.domain import verification_queue as queue_domain

    with _session() as session:
        try:
            counts = queue_domain.assign_to_reviewers(session=session)
        except queue_domain.QueueError as exc:
            print(f"{exc}; grant one with: grant-role <email> reviewer", file=sys.stderr)
            return 1
        session.commit()

    print(f"assigned {sum(counts.values())} items across {len(counts)} reviewers")
    return 0


@contextmanager
def _session() -> Iterator[Session]:
    """Open a database session for one job run, closed on exit.

    Yields:
        The session. The job commits what it wants kept.

    ``get_session`` is a FastAPI dependency generator, so it is driven by hand
    rather than reshaped into a context manager for these callers.
    """
    from app.core.db import get_session

    sessions = get_session()
    session = next(sessions)
    with closing(session):
        yield session


REGISTRY: dict[str, JobSpec] = {
    spec.name: spec
    for spec in (
        JobSpec("crawl", "Crawl one documentation source into staging.", run_crawl),
        JobSpec(
            "reindex-staging",
            "Re-embed the staging corpus after an embedding-model change.",
            run_reindex_staging,
        ),
        JobSpec(
            "expire-stale-sources",
            "Flag production documents whose upstream source was superseded.",
            run_expire_stale_sources,
        ),
        JobSpec(
            "assign-verification",
            "Hand today's verification batches to every reviewer.",
            run_assign_verification,
        ),
        JobSpec("grant-role", "Give an account a role: grant-role <email> <role>.", run_grant_role),
        JobSpec("revoke-role", "Take a role away: revoke-role <email> <role>.", run_revoke_role),
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
