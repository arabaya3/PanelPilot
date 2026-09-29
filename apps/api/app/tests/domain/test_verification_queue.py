"""Tests for the verification queue.

The acceptance criterion is an invariant under concurrency: every staging item
is assigned to exactly one queue at a time, with ten people working it. So the
central test here fires genuinely simultaneous claims at one item from many
threads and asserts exactly one wins.

That test needs a real database. SQLite has no row-level locking, no
``SKIP LOCKED``, and serialises writes behind a single file lock — so a
concurrency test against it would pass whether or not the code is correct,
which is worse than not having one. It is skipped unless ``TEST_DATABASE_URL``
names a Postgres instance, and CI provides one.

The rest of the behaviour is exercised against the same database, because the
uniqueness constraint and the conditional UPDATE this module relies on are
database behaviour, not Python behaviour — a stub session would be asserting
against a reimplementation of the thing under test.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AuthorizationError
from app.domain.auth import grant_role, held_roles
from app.domain.verification_queue import (
    STATUS_ESCALATED,
    STATUS_LABELED,
    STATUS_PENDING,
    STATUS_RESOLVED,
    QueueError,
    assign_daily_batches,
    assign_to_reviewers,
    claim_item,
    enqueue_chunks,
    escalations,
    label_and_publish,
    queue_for,
    record_label,
    resolve_escalation,
    review_queue,
    reviewer_ids,
)
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.ingestion import VerificationDecision
from app.models.schemas.verification import VerificationLabel

# Imported for their side effect on `Base.metadata`, not for direct use.
# `create_all` resolves every foreign key across the whole collection, so a
# table referencing `users` fails unless that model has been imported —
# whatever this module itself touches. Without these the file passes only when
# some earlier test happens to have imported them first, which is a test that
# depends on collection order.
from app.models.tables import diagnostics as _diagnostics  # noqa: F401
from app.models.tables import escalation as _escalation  # noqa: F401
from app.models.tables import session as _session_tables  # noqa: F401
from app.models.tables import tenant as _tenant  # noqa: F401
from app.models.tables import user as _user  # noqa: F401
from app.models.tables.base import Base
from app.models.tables.ingestion import VerificationItemRow
from app.models.tables.tenant import TenantRow
from app.models.tables.user import User

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

#: Enough real accounts for the widest fan-out any test here needs.
VERIFIER_POOL_SIZE = 120

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs Postgres: SQLite has no row-level locking or SKIP LOCKED, so a "
        "concurrency test against it would pass regardless of correctness"
    ),
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    """A Postgres engine with the verification tables created.

    Module-scoped: creating the schema per test costs more than the tests do.
    """
    engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=10)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="session")
def _session(engine: Engine) -> Iterator[Session]:
    """A clean queue for one test."""
    with sessionmaker(bind=engine)() as session:
        session.execute(text("TRUNCATE verification_items CASCADE"))
        session.commit()
        yield session
        session.rollback()


@pytest.fixture(scope="module", name="verifier_pool")
def _verifier_pool(engine: Engine) -> list[uuid.UUID]:
    """Real user rows to assign work to.

    Assignment writes a foreign key to ``users``, so fabricated UUIDs are
    rejected by the database — correctly, since an item assigned to an account
    that does not exist is an item nobody will ever review. Created once per
    module and reused; the queue is truncated per test, not the roster.
    """
    factory = sessionmaker(bind=engine)
    with factory() as session:
        tenant = session.execute(
            select(TenantRow).where(TenantRow.slug == "queue-tests")
        ).scalar_one_or_none()
        if tenant is None:
            tenant = TenantRow(slug="queue-tests", name="Queue tests")
            session.add(tenant)
            session.flush()

        ids: list[uuid.UUID] = []
        for index in range(VERIFIER_POOL_SIZE):
            email = f"verifier-{index}@queue-tests.invalid"
            user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user is None:
                user = User(email=email, tenant_id=tenant.id)
                session.add(user)
                session.flush()
            ids.append(user.id)
        session.commit()
        return ids


def _verifiers(pool: list[uuid.UUID], count: int) -> list[uuid.UUID]:
    """Take `count` verifiers from the pool.

    Args:
        pool: The module's roster of real user ids.
        count: How many are needed.

    Returns:
        The first `count` ids, stably ordered so a failure names the same
        verifier on a rerun.
    """
    assert count <= len(pool), f"pool holds {len(pool)}, test wants {count}"
    return pool[:count]


# --- queueing -----------------------------------------------------------------


@requires_postgres
def test_chunks_are_queued_as_pending(session: Session) -> None:
    created = enqueue_chunks(session=session, chunk_ids=["c1", "c2", "c3"])
    session.commit()

    assert len(created) == 3
    assert all(row.status == STATUS_PENDING for row in created)
    assert all(row.assigned_to_id is None for row in created)


@requires_postgres
def test_queueing_the_same_chunk_twice_creates_one_item(session: Session) -> None:
    # AI-013 wires this to the crawler's staging write, and a re-crawl, a
    # retry, or a replayed event will present the same chunk again. Queueing it
    # twice would put one chunk in two people's queues, which is precisely the
    # invariant this task exists to hold.
    enqueue_chunks(session=session, chunk_ids=["c1", "c2"])
    session.commit()

    again = enqueue_chunks(session=session, chunk_ids=["c1", "c2", "c3"])
    session.commit()

    assert [row.chunk_id for row in again] == ["c3"]
    assert session.query(VerificationItemRow).count() == 3


@requires_postgres
def test_a_duplicate_does_not_lose_the_rest_of_the_batch(session: Session) -> None:
    # The reason each insert gets its own SAVEPOINT. Without it the first
    # collision aborts the whole transaction and every later chunk in the batch
    # is silently dropped — dropped chunks being unverified content that
    # nothing will ever queue again.
    enqueue_chunks(session=session, chunk_ids=["dupe"])
    session.commit()

    created = enqueue_chunks(session=session, chunk_ids=["a", "dupe", "b"])
    session.commit()

    assert {row.chunk_id for row in created} == {"a", "b"}


@requires_postgres
def test_the_database_refuses_a_duplicate_chunk_outright(session: Session) -> None:
    # The guarantee under everything else here. Even if `enqueue_chunks` were
    # bypassed entirely, the constraint holds.
    session.add(VerificationItemRow(chunk_id="c1", status=STATUS_PENDING))
    session.commit()

    session.add(VerificationItemRow(chunk_id="c1", status=STATUS_PENDING))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@requires_postgres
def test_an_item_must_identify_something_to_verify(session: Session) -> None:
    # Both target columns are nullable so the chunk-level and document-level
    # paths can coexist; the check constraint stops a row identifying neither.
    session.add(VerificationItemRow(status=STATUS_PENDING))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- assignment ---------------------------------------------------------------


@requires_postgres
def test_items_are_spread_across_verifiers(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    enqueue_chunks(session=session, chunk_ids=[f"c{i}" for i in range(20)])
    session.commit()

    counts = assign_daily_batches(
        session=session, verifier_ids=_verifiers(verifier_pool, 4), now=NOW
    )
    session.commit()

    assert sum(counts.values()) == 20
    # "Roughly equal batches" — with 20 items and 4 verifiers, exactly equal.
    assert set(counts.values()) == {5}


@requires_postgres
def test_an_uneven_split_differs_by_at_most_one(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    enqueue_chunks(session=session, chunk_ids=[f"c{i}" for i in range(10)])
    session.commit()

    counts = assign_daily_batches(
        session=session, verifier_ids=_verifiers(verifier_pool, 3), now=NOW
    )
    session.commit()

    assert sum(counts.values()) == 10
    assert max(counts.values()) - min(counts.values()) <= 1


@requires_postgres
def test_assignment_is_capped_so_a_large_crawl_cannot_swamp_the_day(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    # AI-013's edge case: a source publishes a large update and one crawl run
    # produces far more chunks than ten people can review. A queue that cannot
    # be finished is one people stop opening, at which point the backlog is
    # invisible rather than merely large.
    enqueue_chunks(session=session, chunk_ids=[f"c{i}" for i in range(100)])
    session.commit()

    counts = assign_daily_batches(
        session=session, verifier_ids=_verifiers(verifier_pool, 2), batch_size=5, now=NOW
    )
    session.commit()

    assert sum(counts.values()) == 10
    # The rest stays pending for the next run rather than being dropped.
    unassigned = (
        session.query(VerificationItemRow)
        .filter(VerificationItemRow.assigned_to_id.is_(None))
        .count()
    )
    assert unassigned == 90


@requires_postgres
def test_an_already_assigned_item_is_not_reassigned(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    enqueue_chunks(session=session, chunk_ids=["c1", "c2"])
    session.commit()
    first = _verifiers(verifier_pool, 1)
    assign_daily_batches(session=session, verifier_ids=first, now=NOW)
    session.commit()

    second = [uuid.UUID(int=99)]
    counts = assign_daily_batches(session=session, verifier_ids=second, now=NOW)
    session.commit()

    assert counts[second[0]] == 0
    rows = session.query(VerificationItemRow).all()
    assert all(row.assigned_to_id == first[0] for row in rows)


@requires_postgres
def test_assigning_with_no_verifiers_is_refused(session: Session) -> None:
    # Refused rather than silently doing nothing: an assignment run against an
    # empty verifier list means the rota is misconfigured, and a run that
    # reports success would hide that until someone noticed the queue was
    # never moving.
    with pytest.raises(QueueError, match="no verifiers"):
        assign_daily_batches(session=session, verifier_ids=[], now=NOW)


@requires_postgres
def test_a_verifier_sees_only_their_own_queue(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    enqueue_chunks(session=session, chunk_ids=[f"c{i}" for i in range(6)])
    session.commit()
    verifiers = _verifiers(verifier_pool, 3)
    assign_daily_batches(session=session, verifier_ids=verifiers, now=NOW)
    session.commit()

    mine = queue_for(session=session, verifier_id=verifiers[0])

    assert len(mine) == 2
    assert all(row.assigned_to_id == verifiers[0] for row in mine)


# --- the acceptance criterion: never two verifiers on one item ----------------


@requires_postgres
def test_simultaneous_claims_produce_exactly_one_winner(
    engine: Engine, verifier_pool: list[uuid.UUID]
) -> None:
    # BE-007's stated testing requirement, and the whole point of the task:
    # "A concurrency test firing simultaneous assignment/claim requests and
    # asserting no item is ever double-assigned."
    #
    # Real threads against real connections. The claim is a conditional UPDATE
    # whose WHERE clause matches only unassigned rows, so the database decides
    # the winner while holding the row lock. A read-check-write in Python would
    # pass a sequential test and fail exactly here.
    factory = sessionmaker(bind=engine)
    with factory() as setup:
        setup.execute(text("TRUNCATE verification_items CASCADE"))
        item = VerificationItemRow(chunk_id="contested", status=STATUS_PENDING)
        setup.add(item)
        setup.commit()
        item_id = item.id

    contenders = 12
    # A barrier, not just threads. `ThreadPoolExecutor.map` alone does not
    # guarantee the threads overlap: each claim is a single fast statement, so
    # in practice they serialise and every implementation passes — including a
    # read-check-write, which was verified to let all contenders win once the
    # reads are genuinely concurrent. The barrier releases every thread at the
    # same instant, after each has opened its session, so the window a naive
    # implementation leaves open is actually entered.
    ready = threading.Barrier(contenders)

    def attempt(index: int) -> bool:
        """Claim the contested item as one verifier.

        Args:
            index: Which contender this is.

        Returns:
            Whether this contender won.
        """
        with factory() as session:
            # Open the connection and start the transaction before waiting, so
            # the barrier releases into the claim itself rather than into
            # connection setup.
            session.execute(text("SELECT 1"))
            ready.wait(timeout=30)
            won = claim_item(
                session=session,
                item_id=item_id,
                verifier_id=verifier_pool[index],
                now=NOW,
            )
            session.commit()
            return won

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        results = list(pool.map(attempt, range(contenders)))

    assert sum(results) == 1, f"{sum(results)} verifiers won the same item"

    with factory() as check:
        row = check.get(VerificationItemRow, item_id)
        assert row is not None
        assert row.assigned_to_id is not None


@requires_postgres
def test_concurrent_assignment_runs_never_double_assign(
    engine: Engine, verifier_pool: list[uuid.UUID]
) -> None:
    # The other half of the same risk. Two assignment runs overlapping — a
    # retry, a cron firing twice, two operators — must not hand one item to two
    # people.
    #
    # What holds that is the row lock, not SKIP LOCKED: removing SKIP LOCKED
    # was measured and changes no outcome, only whether the second run waits.
    # This test therefore pins the invariant (each row has at most one
    # assignee) and deliberately does not claim to pin SKIP LOCKED, which no
    # outcome-based test here can distinguish.
    factory = sessionmaker(bind=engine)
    with factory() as setup:
        setup.execute(text("TRUNCATE verification_items CASCADE"))
        setup.add_all(
            VerificationItemRow(chunk_id=f"c{i}", status=STATUS_PENDING) for i in range(40)
        )
        setup.commit()

    def run(offset: int) -> None:
        """Run one assignment pass with its own verifier set.

        Args:
            offset: Distinguishes this run's verifier ids from the other's.
        """
        with factory() as session:
            assign_daily_batches(
                session=session,
                verifier_ids=verifier_pool[offset : offset + 4],
                batch_size=5,
                now=NOW,
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [0, 100]))

    with factory() as check:
        rows = check.query(VerificationItemRow).all()
        assigned = [row for row in rows if row.assigned_to_id is not None]
        # Each row carries exactly one assignee — the invariant. Two runs of 20
        # against 40 rows should touch disjoint sets.
        assert len(assigned) == len({row.id for row in assigned})
        assert len(assigned) <= 40


# --- labelling and escalation -------------------------------------------------


@requires_postgres
def test_a_correct_label_closes_the_item(session: Session, verifier_pool: list[uuid.UUID]) -> None:
    enqueue_chunks(session=session, chunk_ids=["c1"])
    session.commit()
    verifier = _verifiers(verifier_pool, 1)[0]
    assign_daily_batches(session=session, verifier_ids=[verifier], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).one()

    row = record_label(
        session=session,
        item_id=item.id,
        verifier_id=verifier,
        label=VerificationLabel.CORRECT,
    )
    session.commit()

    assert row.status == STATUS_LABELED
    assert row.label == "correct"


@requires_postgres
@pytest.mark.parametrize(
    "label",
    [VerificationLabel.INCORRECT, VerificationLabel.UNCERTAIN],
)
def test_a_non_correct_label_escalates(
    session: Session, verifier_pool: list[uuid.UUID], label: VerificationLabel
) -> None:
    # AI-012's rule, enforced here rather than restated: an incorrect or
    # uncertain label routes to lead review instead of being resolved by the
    # verifier who applied it.
    enqueue_chunks(session=session, chunk_ids=["c1"])
    session.commit()
    verifier = _verifiers(verifier_pool, 1)[0]
    assign_daily_batches(session=session, verifier_ids=[verifier], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).one()

    row = record_label(
        session=session,
        item_id=item.id,
        verifier_id=verifier,
        label=label,
        note="cited section gives 63 A, chunk says 80 A",
    )
    session.commit()

    assert row.status == STATUS_ESCALATED


@requires_postgres
def test_an_escalating_label_requires_a_note(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    # A lead receiving "incorrect" with no note has to redo the verification
    # from scratch to find out what was wrong — which is the work the label was
    # supposed to save.
    enqueue_chunks(session=session, chunk_ids=["c1"])
    session.commit()
    verifier = _verifiers(verifier_pool, 1)[0]
    assign_daily_batches(session=session, verifier_ids=[verifier], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).one()

    with pytest.raises(QueueError, match="requires a note"):
        record_label(
            session=session,
            item_id=item.id,
            verifier_id=verifier,
            label=VerificationLabel.INCORRECT,
            note="   ",
        )


@requires_postgres
def test_a_verifier_cannot_label_someone_elses_item(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    # The item id arrives in a URL. Without this check, one verifier could
    # overwrite another's assignment by guessing or pasting an id.
    enqueue_chunks(session=session, chunk_ids=["c1"])
    session.commit()
    mine, theirs = _verifiers(verifier_pool, 2)
    assign_daily_batches(session=session, verifier_ids=[mine], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).one()

    with pytest.raises(QueueError, match="not assigned"):
        record_label(
            session=session,
            item_id=item.id,
            verifier_id=theirs,
            label=VerificationLabel.CORRECT,
        )


@requires_postgres
def test_labelling_a_missing_item_is_refused(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    with pytest.raises(QueueError, match="no verification item"):
        record_label(
            session=session,
            item_id=uuid.UUID(int=12345),
            verifier_id=verifier_pool[0],
            label=VerificationLabel.CORRECT,
        )


@requires_postgres
def test_escalated_items_are_visible_to_a_lead_immediately(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    # The second half of the acceptance criterion: "an escalated item is
    # visible in the lead-review view within one polling cycle". It is visible
    # as soon as the label commits — the status change and this read hit the
    # same row, with no intermediate queue to lag behind.
    enqueue_chunks(session=session, chunk_ids=["c1", "c2"])
    session.commit()
    verifier = _verifiers(verifier_pool, 1)[0]
    assign_daily_batches(session=session, verifier_ids=[verifier], now=NOW)
    session.commit()
    first, second = session.query(VerificationItemRow).order_by(VerificationItemRow.chunk_id).all()

    record_label(
        session=session,
        item_id=first.id,
        verifier_id=verifier,
        label=VerificationLabel.UNCERTAIN,
        note="two passages in the manual conflict",
    )
    record_label(
        session=session,
        item_id=second.id,
        verifier_id=verifier,
        label=VerificationLabel.CORRECT,
    )
    session.commit()

    waiting = escalations(session=session)

    assert [row.id for row in waiting] == [first.id]


@requires_postgres
def test_a_labelled_item_leaves_the_verifiers_queue(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    enqueue_chunks(session=session, chunk_ids=["c1", "c2"])
    session.commit()
    verifier = _verifiers(verifier_pool, 1)[0]
    assign_daily_batches(session=session, verifier_ids=[verifier], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).order_by(VerificationItemRow.chunk_id).first()
    assert item is not None

    record_label(
        session=session,
        item_id=item.id,
        verifier_id=verifier,
        label=VerificationLabel.CORRECT,
    )
    session.commit()

    assert len(queue_for(session=session, verifier_id=verifier)) == 1


# --- clearing publishes (BE-004 x BE-007) -------------------------------------
#
# A `correct` label is the only way content reaches production, so these run
# against a real OpenSearch as well as Postgres: the property under test is that
# the label and the production write happen together or not at all.

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "")


def _opensearch_available() -> bool:
    if not OPENSEARCH_URL:
        return False
    try:
        from app.ai.retrieval.client import get_client

        return bool(get_client().ping())
    except Exception:
        return False


requires_search = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql") or not _opensearch_available(),
    reason="needs Postgres and a reachable OpenSearch; CI provides both",
)

#: Whoever crawled the content. Not a reviewer, and not anyone in the pool, so
#: the four-eyes rule never refuses the reviewers these tests use.
INGESTER = "0f0f0f0f-0000-4000-8000-000000000001"


def _as_user(session: Session, user_id: uuid.UUID, *roles: Role) -> CurrentUser:
    """Grant `roles` for real, and return the caller a token would carry."""
    user = session.get(User, user_id)
    assert user is not None
    for role in roles:
        grant_role(session=session, email=user.email, role=role)
    session.commit()
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        tenant_id=str(user.tenant_id),
        roles=held_roles(user),
    )


@pytest.fixture(name="staged")
def _staged(session: Session) -> Iterator[tuple[str, str, str]]:
    """One crawled chunk in a fresh staging index, and its staged document.

    Yields:
        ``(chunk_id, staging_index, production_index)``.
    """
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client
    from app.ai.retrieval.mappings import VerificationStatus
    from app.models.tables.ingestion import CrawlJobRow, StagedDocumentRow

    client = get_client()
    staging = ensure_index(IndexTarget.STAGING, recreate=True)
    production = ensure_index(IndexTarget.PRODUCTION, recreate=True)

    content_hash = f"queue-test-{uuid.uuid4().hex}"
    job = CrawlJobRow(source_id="queue-test", status="succeeded")
    session.add(job)
    session.flush()
    session.add(
        StagedDocumentRow(
            crawl_job_id=job.id,
            source_url="https://example.invalid/m.pdf",
            content_hash=content_hash,
        )
    )
    session.commit()

    chunk_id = "queue-test-doc#0001-abcdef012345"
    client.index(
        index=staging,
        id=chunk_id,
        refresh=True,
        body={
            "brand": "ABB",
            "model": "ACS880",
            "doc_type": "manual",
            "page": 12,
            "section": "Fault tracing",
            "source_url": "https://example.invalid/m.pdf#page=12",
            "verification_status": VerificationStatus.UNVERIFIED.value,
            "content": "F0001 OVERCURRENT: output current exceeded the trip limit.",
            "content_vector": [0.1] * 1024,
            "content_hash": content_hash,
            "ingested_by": INGESTER,
        },
    )
    enqueue_chunks(session=session, chunk_ids=[chunk_id])
    session.commit()
    try:
        yield chunk_id, staging, production
    finally:
        session.rollback()
        session.execute(text("DELETE FROM promotion_audits"))
        session.execute(text("TRUNCATE verification_items CASCADE"))
        session.execute(
            text(
                "DELETE FROM staged_documents WHERE crawl_job_id IN "
                "(SELECT id FROM crawl_jobs WHERE source_id = 'queue-test')"
            )
        )
        session.execute(text("DELETE FROM crawl_jobs WHERE source_id = 'queue-test'"))
        session.commit()
        client.indices.delete(index=staging, ignore=[404])
        client.indices.delete(index=production, ignore=[404])


def _assigned_item(session: Session, verifier: CurrentUser) -> VerificationItemRow:
    assign_daily_batches(session=session, verifier_ids=[uuid.UUID(verifier.id)], now=NOW)
    session.commit()
    item = session.query(VerificationItemRow).one()
    return item


def _is_live(production: str, chunk_id: str) -> bool:
    from app.ai.retrieval.client import get_client

    return bool(get_client().exists(index=production, id=chunk_id))


@requires_search
def test_a_correct_label_makes_the_chunk_live(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    chunk_id, _, production = staged
    reviewer = _as_user(session, verifier_pool[0], Role.REVIEWER)
    item = _assigned_item(session, reviewer)

    row = label_and_publish(
        session=session, item_id=item.id, verifier=reviewer, label=VerificationLabel.CORRECT
    )
    session.commit()

    assert row.status == STATUS_LABELED
    assert row.decision == VerificationDecision.APPROVED.value
    assert _is_live(production, chunk_id)
    audits: int = session.execute(text("SELECT count(*) FROM promotion_audits")).scalar_one()
    assert audits == 1, "live content without an audit row names no human"


@requires_search
def test_a_correct_label_without_the_reviewer_role_publishes_nothing(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    """Refused whole: the queue must never read "verified" for content not live."""
    chunk_id, _, production = staged
    engineer = _as_user(session, verifier_pool[1])
    item = _assigned_item(session, engineer)

    with pytest.raises(AuthorizationError):
        label_and_publish(
            session=session, item_id=item.id, verifier=engineer, label=VerificationLabel.CORRECT
        )
    session.rollback()

    assert not _is_live(production, chunk_id)
    assert session.get(VerificationItemRow, item.id).status == STATUS_PENDING  # type: ignore[union-attr]


@requires_search
def test_an_escalating_label_publishes_nothing(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    chunk_id, _, production = staged
    reviewer = _as_user(session, verifier_pool[0], Role.REVIEWER)
    item = _assigned_item(session, reviewer)

    row = label_and_publish(
        session=session,
        item_id=item.id,
        verifier=reviewer,
        label=VerificationLabel.INCORRECT,
        note="the manual gives 63 A, not 50 A",
    )
    session.commit()

    assert row.status == STATUS_ESCALATED
    assert row.decision is None
    assert not _is_live(production, chunk_id)


def _escalated(session: Session, verifier: CurrentUser) -> VerificationItemRow:
    item = _assigned_item(session, verifier)
    label_and_publish(
        session=session,
        item_id=item.id,
        verifier=verifier,
        label=VerificationLabel.UNCERTAIN,
        note="two passages conflict",
    )
    session.commit()
    return item


@requires_search
def test_a_lead_approval_publishes_and_keeps_the_verifiers_note(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    chunk_id, _, production = staged
    verifier = _as_user(session, verifier_pool[0], Role.REVIEWER)
    lead = _as_user(session, verifier_pool[2], Role.REVIEWER)
    item = _escalated(session, verifier)

    row = resolve_escalation(
        session=session,
        item_id=item.id,
        lead=lead,
        decision=VerificationDecision.APPROVED,
        note="p.12 governs; p.40 is a superseded table",
    )
    session.commit()

    assert row.status == STATUS_RESOLVED
    assert row.decision == VerificationDecision.APPROVED.value
    assert row.label == VerificationLabel.UNCERTAIN.value
    assert row.notes is not None
    assert "two passages conflict" in row.notes
    assert "p.12 governs" in row.notes
    assert _is_live(production, chunk_id)


@requires_search
def test_a_lead_rejection_keeps_the_chunk_out(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    chunk_id, _, production = staged
    verifier = _as_user(session, verifier_pool[0], Role.REVIEWER)
    lead = _as_user(session, verifier_pool[2], Role.REVIEWER)
    item = _escalated(session, verifier)

    row = resolve_escalation(
        session=session,
        item_id=item.id,
        lead=lead,
        decision=VerificationDecision.REJECTED,
        note="the citation does not support the claim",
    )
    session.commit()

    assert row.status == STATUS_RESOLVED
    assert row.decision == VerificationDecision.REJECTED.value
    assert not _is_live(production, chunk_id)
    assert escalations(session=session) == []


@requires_search
def test_the_verifier_who_escalated_cannot_resolve_it(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    """AI-012: never resolved unilaterally by whoever applied the label."""
    chunk_id, _, production = staged
    verifier = _as_user(session, verifier_pool[0], Role.REVIEWER)
    item = _escalated(session, verifier)

    with pytest.raises(QueueError, match="another lead"):
        resolve_escalation(
            session=session,
            item_id=item.id,
            lead=verifier,
            decision=VerificationDecision.APPROVED,
            note="on reflection it is fine",
        )

    assert not _is_live(production, chunk_id)


@requires_search
@pytest.mark.parametrize(
    ("note", "match"),
    [("", "requires a note"), ("   ", "requires a note")],
)
def test_a_resolution_needs_a_reason(
    session: Session,
    verifier_pool: list[uuid.UUID],
    staged: tuple[str, str, str],
    note: str,
    match: str,
) -> None:
    verifier = _as_user(session, verifier_pool[0], Role.REVIEWER)
    lead = _as_user(session, verifier_pool[2], Role.REVIEWER)
    item = _escalated(session, verifier)

    with pytest.raises(QueueError, match=match):
        resolve_escalation(
            session=session,
            item_id=item.id,
            lead=lead,
            decision=VerificationDecision.APPROVED,
            note=note,
        )


@requires_search
def test_only_escalated_items_can_be_resolved(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    lead = _as_user(session, verifier_pool[2], Role.REVIEWER)
    item = session.query(VerificationItemRow).one()

    with pytest.raises(QueueError, match="not escalated"):
        resolve_escalation(
            session=session,
            item_id=item.id,
            lead=lead,
            decision=VerificationDecision.APPROVED,
            note="looks fine",
        )


@requires_postgres
def test_resolving_needs_the_reviewer_role(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    engineer = _as_user(session, verifier_pool[3])

    with pytest.raises(AuthorizationError):
        resolve_escalation(
            session=session,
            item_id=uuid.uuid4(),
            lead=engineer,
            decision=VerificationDecision.APPROVED,
            note="x",
        )


# --- assigning to the reviewer pool -------------------------------------------


@requires_postgres
def test_work_is_assigned_only_to_reviewers(
    session: Session, verifier_pool: list[uuid.UUID]
) -> None:
    reviewer = _as_user(session, verifier_pool[0], Role.REVIEWER)
    engineer = _as_user(session, verifier_pool[5])
    enqueue_chunks(session=session, chunk_ids=[f"c{i}" for i in range(3)])
    session.commit()

    counts = assign_to_reviewers(session=session, now=NOW)

    assert uuid.UUID(reviewer.id) in counts
    assert uuid.UUID(engineer.id) not in counts
    assert uuid.UUID(engineer.id) not in reviewer_ids(session=session)


@requires_postgres
def test_no_reviewers_is_an_error_not_a_silent_no_op(session: Session) -> None:
    """A queue nobody can drain looks, from outside, like one keeping up."""
    session.execute(
        text(
            "DELETE FROM user_roles WHERE role_id IN (SELECT id FROM roles WHERE name = 'reviewer')"
        )
    )

    with pytest.raises(QueueError, match="no verifiers"):
        assign_to_reviewers(session=session, now=NOW)
    session.rollback()


@requires_search
def test_the_review_queue_carries_the_staged_text_and_citation(
    session: Session, verifier_pool: list[uuid.UUID], staged: tuple[str, str, str]
) -> None:
    chunk_id, _, _ = staged
    reviewer = _as_user(session, verifier_pool[0], Role.REVIEWER)
    _assigned_item(session, reviewer)

    (item,) = review_queue(session=session, verifier_id=uuid.UUID(reviewer.id))

    assert item.chunk_id == chunk_id
    assert item.content is not None
    assert item.content.startswith("F0001 OVERCURRENT")
    assert item.source_url == "https://example.invalid/m.pdf#page=12"
    assert item.page == 12
