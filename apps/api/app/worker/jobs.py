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

from app.core.errors import NotFoundError, PanelPilotError, ValidationError
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
    from app.core.tenancy import cross_tenant
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
    # A system job acts for no tenant, so it cannot be bound to one: it
    # declares that instead of querying customer tables unscoped (ADR 0003).
    # Queued like an API request, then run here and now: one path for a crawl,
    # whoever asked for it.
    with closing(session), cross_tenant(session, reason="a system job acts for no tenant"):
        queued = ingestion_domain.create_crawl_job(
            session=session, user=system_actor(), request=request
        )
        session.commit()
        response = ingestion_domain.run_crawl_job(session=session, job_id=queued.id)

    print(f"crawl {response.id}: {response.status.value}")
    # A FAILED job is a successful recording of a failure, but the process must
    # still exit non-zero: a scheduler that sees 0 will not alert, and a source
    # that silently stops returning documents is the exact failure BE-006's
    # staleness alerting exists to catch.
    return 0 if response.status is CrawlJobStatus.SUCCEEDED else 1


def run_crawl_queue(args: list[str]) -> int:
    """Run the oldest crawl queued through the API, if there is one.

    ``POST /ingestion/crawl-jobs`` only queues; this is what runs them. One job
    per invocation, like every worker job — schedule it as often as crawls
    should start, and run several at once for parallel crawls: each claims a
    different job.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` when nothing was queued or the crawl succeeded, ``1`` when it
        failed, so a scheduler alerts on the failure.
    """
    from contextlib import closing

    from app.core.db import get_session
    from app.core.tenancy import cross_tenant
    from app.domain import ingestion as ingestion_domain
    from app.models.schemas.ingestion import CrawlJobStatus

    del args
    sessions = get_session()
    session = next(sessions)
    with closing(session), cross_tenant(session, reason="a system job acts for no tenant"):
        response = ingestion_domain.run_next_crawl_job(session=session)

    if response is None:
        print("crawl queue: empty")
        return 0
    print(f"crawl {response.id}: {response.status.value}")
    return 0 if response.status is CrawlJobStatus.SUCCEEDED else 1


def run_assign_review_batches(args: list[str]) -> int:
    """Hand today's verification batches to everyone holding the reviewer role.

    Schedule it once a day. Without it nothing is ever assigned, and nothing
    assigned is nothing labelled, so nothing is ever promoted.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` on success, ``1`` if nobody holds the reviewer role.
    """
    from contextlib import closing

    from app.core.db import get_session
    from app.core.tenancy import cross_tenant
    from app.domain import verification_queue as queue_domain

    del args
    sessions = get_session()
    session = next(sessions)
    with closing(session), cross_tenant(session, reason="reviewers work every tenant's queue"):
        try:
            assigned = queue_domain.assign_to_reviewers(session=session)
        except queue_domain.QueueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        session.commit()

    print(f"assigned {sum(assigned.values())} items across {len(assigned)} reviewers")
    return 0


def run_reindex_staging(args: list[str]) -> int:
    """Re-embed the staging corpus in place: ``reindex-staging [source_id]``.

    Run this after changing the embedding model. Production is untouched; its
    chunks are re-embedded on their way through promotion. A chunking change
    needs a fresh crawl instead -- the original files are not kept. See
    docs/adr/0001-staging-vs-production-index.md.

    Args:
        args: Optionally ``[source_id]`` to limit the run to one source.

    Returns:
        ``0`` on success, ``1`` if embedding is misconfigured or fails, ``2`` on
        bad arguments or an unknown source.
    """
    from app.core.config import ConfigurationError
    from app.domain.corpus_maintenance import reindex_staging

    if len(args) > 1:
        print("usage: reindex-staging [source_id]", file=sys.stderr)
        return 2
    try:
        count = reindex_staging(source_id=args[0] if args else None)
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ConfigurationError, PanelPilotError) as exc:
        # No embedding key, or the provider refused: an operator's problem to
        # fix, said in one line rather than a traceback in the scheduler log.
        print(f"reindex-staging: {exc}", file=sys.stderr)
        return 1
    print(f"re-embedded {count} staged chunks")
    return 0


def run_expire_stale_sources(args: list[str]) -> int:
    """Flag live documents whose upstream source changed or was withdrawn.

    Flags only, into ``stale_documents``: retraction is a reviewed operation,
    not an automated one. Schedule it daily or weekly; manufacturer documents
    change on a scale of months.

    Args:
        args: Unused; accepted for a uniform handler signature.

    Returns:
        ``0`` when nothing live is stale, ``1`` when something is -- so a
        scheduler's failure alert is the notification that a reviewer is needed.
    """
    from contextlib import closing

    from app.core.db import get_session
    from app.domain.corpus_maintenance import expire_stale_sources

    del args
    session = next(get_session())
    with closing(session):
        report = expire_stale_sources(session=session)
        session.commit()

    for url, reason in sorted(report.flagged.items()):
        print(f"{reason}: {url}")
    for url in report.cleared:
        print(f"cleared: {url}")
    for url, why in sorted(report.unchecked.items()):
        print(f"unchecked ({why}): {url}", file=sys.stderr)
    print(
        f"checked {report.checked} live documents: {len(report.flagged)} stale, "
        f"{len(report.cleared)} cleared, {len(report.unchecked)} could not be checked"
    )
    return 1 if report.flagged else 0


def run_grant_role(args: list[str]) -> int:
    """Give an account a role: ``grant-role <email> <role>``.

    The only way a role is granted. An operator runs it; there is no API for
    it, deliberately, until there is an admin surface to put it behind.

    Args:
        args: ``[email, role]``.

    Returns:
        ``0`` on success (including "already held"), ``2`` on bad arguments.
    """
    return _change_role(args, grant=True)


def run_revoke_role(args: list[str]) -> int:
    """Take a role away: ``revoke-role <email> <role>``. Applies to the next request.

    Args:
        args: ``[email, role]``.

    Returns:
        ``0`` on success (including "was not held"), ``2`` on bad arguments.
    """
    return _change_role(args, grant=False)


def _change_role(args: list[str], *, grant: bool) -> int:
    """Parse the arguments, then call one domain function and commit.

    Args:
        args: ``[email, role]``.
        grant: Grant when true, revoke when false.

    Returns:
        The exit code.
    """
    from contextlib import closing

    from app.core.db import get_session
    from app.core.tenancy import cross_tenant
    from app.domain import roles as roles_domain

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

    sessions = get_session()
    session = next(sessions)
    # An operator acts for no tenant: the account is found by email, whichever
    # tenant it is in (ADR 0003).
    with closing(session), cross_tenant(session, reason="an operator manages any account's roles"):
        change = roles_domain.grant_role if grant else roles_domain.revoke_role
        try:
            changed = change(session=session, email=email, role=role)
        except (NotFoundError, ValidationError) as exc:
            # An operator's typo, not a crash: say which, exit non-zero, and
            # leave the traceback out of it.
            print(f"{verb}: {exc}", file=sys.stderr)
            return 1
        session.commit()

    state = ("granted" if grant else "revoked") if changed else "unchanged"
    print(f"{verb} {role.value}: {state}")
    return 0


def run_calibrate_relevance(args: list[str]) -> int:
    """Recommend a retrieval similarity floor from an eval set.

    Runs every question in the set against the production index with no
    floor, and reports the highest floor that still lets 95% of in-scope
    questions through, with how many out-of-scope ones it would refuse. Read
    only: it recommends ``RETRIEVAL_MIN_SIMILARITY``, an operator sets it.

    Args:
        args: ``[eval_set.json]``: a JSON array of eval entries.

    Returns:
        ``0`` with a recommendation, ``1`` without one (the report says why),
        ``2`` if the eval set cannot be read.
    """
    from pathlib import Path

    from pydantic import TypeAdapter, ValidationError

    from app.domain import search as search_domain
    from app.models.schemas.evaluation import EvalEntry

    if len(args) != 1:
        print("usage: calibrate-relevance <eval_set.json>", file=sys.stderr)
        return 2
    try:
        entries = TypeAdapter(list[EvalEntry]).validate_json(Path(args[0]).read_bytes())
    except (OSError, ValidationError) as exc:
        print(f"cannot read eval set {args[0]!r}: {exc}", file=sys.stderr)
        return 2

    result = search_domain.calibrate_relevance(entries)

    print(
        f"measured {result.in_scope} in-scope and {result.out_of_scope} out-of-scope "
        f"questions; skipped {result.skipped} (code-anchored, nothing retrieved, or unmeasurable)"
    )
    if result.floor is None:
        print(f"no recommendation: {result.reason}")
        return 1
    print(
        f"RETRIEVAL_MIN_SIMILARITY={result.floor}  "
        f"keeps {result.kept:.0%} of in-scope, refuses {result.refused:.0%} of out-of-scope"
    )
    return 0


REGISTRY: dict[str, JobSpec] = {
    spec.name: spec
    for spec in (
        JobSpec("crawl", "Crawl one documentation source into staging.", run_crawl),
        JobSpec("crawl-queue", "Run the oldest crawl queued through the API.", run_crawl_queue),
        JobSpec(
            "assign-review-batches",
            "Hand today's verification batches to every reviewer.",
            run_assign_review_batches,
        ),
        JobSpec(
            "reindex-staging",
            "Re-embed the staging corpus after an embedding model change.",
            run_reindex_staging,
        ),
        JobSpec(
            "expire-stale-sources",
            "Flag live documents whose upstream source changed or was withdrawn.",
            run_expire_stale_sources,
        ),
        JobSpec(
            "grant-role", "Give an account a role (reviewer, ingestion, admin).", run_grant_role
        ),
        JobSpec("revoke-role", "Take a role away from an account.", run_revoke_role),
        JobSpec(
            "calibrate-relevance",
            "Recommend RETRIEVAL_MIN_SIMILARITY from an eval set.",
            run_calibrate_relevance,
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
