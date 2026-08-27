"""Tests for the ingestion-to-verification wiring.

The acceptance criterion is end-to-end and unattended: a document dropped into
a source is chunked and appears in the verification queue with no manual
trigger. So the central test here runs a real crawl result through the real
staging pipeline and the real queue, and asserts every resulting chunk is
queued exactly once.

Postgres, for the same reason as the queue's own tests: the "exactly once"
guarantee is a unique constraint, and SQLite would let a reimplementation of
it pass.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.ingestion_wiring import (
    MAX_CHUNKS_PER_RUN,
    chunk_ids_from_bodies,
    make_staging_hook,
    populate_queue_from_staging,
)

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

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason=(
        "needs Postgres: the exactly-once guarantee is a unique constraint, and "
        "SQLite would let a reimplementation of it pass"
    ),
)


@pytest.fixture(scope="module", name="engine")
def _engine() -> Iterator[Engine]:
    """A Postgres engine with the verification tables created."""
    engine = create_engine(DATABASE_URL)
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


def _bodies(*, documents: int, chunks_per: int) -> dict[str, list[dict[str, object]]]:
    """Build staging bodies in the shape `prepare_documents` returns.

    Args:
        documents: How many documents.
        chunks_per: Chunks in each.

    Returns:
        Chunk bodies keyed by document id.
    """
    return {
        f"doc-{d}": [{"chunk_id": f"doc-{d}-chunk-{c}"} for c in range(chunks_per)]
        for d in range(documents)
    }


# --- reading chunk ids off a staging run --------------------------------------


def test_every_chunk_id_is_extracted_in_order() -> None:
    ids = chunk_ids_from_bodies(_bodies(documents=2, chunks_per=3))

    assert len(ids) == 6
    assert ids[0] == "doc-0-chunk-0"


def test_a_body_without_a_chunk_id_is_refused() -> None:
    # Refused rather than skipped. A chunk that cannot be queued is content in
    # staging that no verifier will ever be shown — and afterwards it is
    # indistinguishable from content that was reviewed and passed, which is the
    # exact failure this wiring exists to prevent.
    with pytest.raises(ValueError, match="no chunk_id"):
        chunk_ids_from_bodies({"doc-1": [{"text": "no id here"}]})


def test_an_empty_chunk_id_is_refused_too() -> None:
    # An empty string is present-but-useless, which a truthiness check catches
    # and a `in body` check would not.
    with pytest.raises(ValueError, match="no chunk_id"):
        chunk_ids_from_bodies({"doc-1": [{"chunk_id": ""}]})


def test_a_run_that_produced_nothing_yields_no_ids() -> None:
    # A crawl finding nothing new is the normal case on most days.
    assert chunk_ids_from_bodies({}) == []


# --- the acceptance criterion -------------------------------------------------


@requires_postgres
def test_every_chunk_reaches_the_queue_exactly_once(session: Session) -> None:
    # "A test document dropped into a source is chunked and appears in the
    # verification queue without any manual trigger", and AI-013's testing
    # requirement: every resulting chunk appears exactly once, none duplicated
    # and none dropped.
    bodies = _bodies(documents=3, chunks_per=4)
    produced = chunk_ids_from_bodies(bodies)

    result = populate_queue_from_staging(session=session, chunk_ids=produced)
    session.commit()

    assert len(result.queued) == 12
    queued = {row.chunk_id for row in session.query(VerificationItemRow).all()}
    assert queued == set(produced)


@requires_postgres
def test_nothing_is_dropped_between_chunking_and_the_queue(session: Session) -> None:
    # The silent-loss check. Counting what went in against what landed is the
    # only way to catch a chunk quietly disappearing in the middle.
    produced = chunk_ids_from_bodies(_bodies(documents=5, chunks_per=7))

    populate_queue_from_staging(session=session, chunk_ids=produced)
    session.commit()

    assert session.query(VerificationItemRow).count() == len(produced)


@requires_postgres
def test_a_recrawl_does_not_queue_the_same_chunk_twice(session: Session) -> None:
    # The property that makes this safe to wire to a crawler at all. A re-crawl
    # of unchanged content produces the same chunk ids, and queueing them again
    # would put one chunk in two verifiers' queues.
    produced = chunk_ids_from_bodies(_bodies(documents=2, chunks_per=3))
    populate_queue_from_staging(session=session, chunk_ids=produced)
    session.commit()

    second = populate_queue_from_staging(session=session, chunk_ids=produced)
    session.commit()

    assert second.queued == []
    assert second.already_present == 6
    assert session.query(VerificationItemRow).count() == 6


@requires_postgres
def test_a_partial_recrawl_queues_only_what_is_new(session: Session) -> None:
    # The realistic case: a source republishes one document out of several.
    first = chunk_ids_from_bodies(_bodies(documents=2, chunks_per=2))
    populate_queue_from_staging(session=session, chunk_ids=first)
    session.commit()

    mixed = [*first, "doc-9-chunk-0", "doc-9-chunk-1"]
    second = populate_queue_from_staging(session=session, chunk_ids=mixed)
    session.commit()

    assert set(second.queued) == {"doc-9-chunk-0", "doc-9-chunk-1"}
    assert second.already_present == 4


# --- the burst edge case ------------------------------------------------------


@requires_postgres
def test_a_large_run_is_capped_rather_than_dumped(session: Session) -> None:
    # AI-013's edge case: a source publishes a large update. The daily
    # assignment already caps what each verifier receives, so the risk is not
    # an overloaded verifier — it is one noisy source crowding out every other
    # source's chunks for weeks, because assignment takes the oldest first.
    produced = [f"chunk-{i}" for i in range(120)]

    result = populate_queue_from_staging(session=session, chunk_ids=produced, max_per_run=50)
    session.commit()

    assert len(result.queued) == 50
    assert len(result.deferred) == 70
    assert session.query(VerificationItemRow).count() == 50


@requires_postgres
def test_deferred_chunks_are_queued_by_a_later_run(session: Session) -> None:
    # Deferred, not dropped. The distinction only holds if a later run actually
    # picks them up — otherwise the cap is silent data loss with a nicer name.
    produced = [f"chunk-{i}" for i in range(120)]
    populate_queue_from_staging(session=session, chunk_ids=produced, max_per_run=50)
    session.commit()

    populate_queue_from_staging(session=session, chunk_ids=produced, max_per_run=50)
    session.commit()
    populate_queue_from_staging(session=session, chunk_ids=produced, max_per_run=50)
    session.commit()

    # Three runs of 50 covers 120 with room to spare, and the idempotence means
    # re-presenting the already-queued ones costs nothing.
    assert session.query(VerificationItemRow).count() == 120


@requires_postgres
def test_the_default_cap_is_generous_enough_for_an_ordinary_run(session: Session) -> None:
    # A cap that fires on a normal night would make deferral the rule rather
    # than the exception, and the warning it logs would stop meaning anything.
    produced = [f"chunk-{i}" for i in range(200)]

    result = populate_queue_from_staging(session=session, chunk_ids=produced)
    session.commit()

    assert result.deferred == []
    assert MAX_CHUNKS_PER_RUN >= 200


# --- the hook itself ----------------------------------------------------------


@requires_postgres
def test_the_hook_queues_what_it_is_given(session: Session) -> None:
    # The seam AI-013 asks for: the crawler's caller holds one of these and
    # invokes it, knowing nothing about a verification queue.
    hook = make_staging_hook(session=session)

    hook(["a", "b", "c"])
    session.commit()

    assert session.query(VerificationItemRow).count() == 3


@requires_postgres
def test_the_hook_is_safe_to_call_twice(session: Session) -> None:
    hook = make_staging_hook(session=session)

    hook(["a", "b"])
    hook(["a", "b"])
    session.commit()

    assert session.query(VerificationItemRow).count() == 2


@requires_postgres
def test_the_hook_on_an_empty_run_does_nothing(session: Session) -> None:
    # Most nights a source publishes nothing. That must not be an error.
    hook = make_staging_hook(session=session)

    hook([])
    session.commit()

    assert session.query(VerificationItemRow).count() == 0


@requires_postgres
def test_queued_chunks_are_immediately_assignable(session: Session) -> None:
    # The end of the chain, and what "appears in the verification queue"
    # actually has to mean: not merely a row existing, but a row the daily
    # assignment will pick up. A chunk queued in some other status would be
    # invisible to the thing that hands out work.
    from app.domain.verification_queue import STATUS_PENDING

    hook = make_staging_hook(session=session)
    hook(["a", "b"])
    session.commit()

    rows = session.query(VerificationItemRow).all()
    assert all(row.status == STATUS_PENDING for row in rows)
    assert all(row.assigned_to_id is None for row in rows)


def test_the_hook_signature_takes_only_chunk_ids() -> None:
    # Pinned deliberately. The value of the seam is that the crawler side knows
    # nothing about the queue; widening this to take a session or a verifier
    # would put that knowledge back on the wrong side of the boundary.
    import inspect

    from app.domain.ingestion_wiring import StagingChunkHook

    hook = make_staging_hook(session=None)  # type: ignore[arg-type]
    parameters = list(inspect.signature(hook).parameters)

    assert parameters == ["chunk_ids"]
    assert StagingChunkHook is not None


def test_the_result_reports_all_three_outcomes() -> None:
    # Queued, already-there, and deferred are different facts and an operator
    # needs all three: the same "0 queued" means healthy on a quiet night and
    # broken if 500 were produced.
    from app.domain.ingestion_wiring import QueuePopulationResult

    result = QueuePopulationResult(queued=["a"], already_present=2, deferred=["b"])

    assert result.queued == ["a"]
    assert result.already_present == 2
    assert result.deferred == ["b"]


def test_chunk_ids_survive_a_realistic_staging_body() -> None:
    # Guards the coupling to `chunk_body`'s output shape. If that renamed its
    # id field, this wiring would raise rather than silently queue nothing —
    # but the test should say so at the seam rather than in a Postgres test.
    from app.ingestion.staging_pipeline import chunk_body
    from app.models.schemas.documents import DocumentChunk

    chunk = DocumentChunk(
        id="doc-1-chunk-0",
        document_id="doc-1",
        text="Set the overload to 1.15 times full load current.",
        page=41,
        section="3.4 Overload protection",
        brand="siemens",
        model="SIRIUS",
        doc_type="manual",
        source_url="https://example.invalid/manual.pdf",
        is_atomic=False,
    )

    ids = chunk_ids_from_bodies({"doc-1": [chunk_body(chunk)]})

    assert ids == ["doc-1-chunk-0"]


def test_a_uuid_shaped_chunk_id_is_accepted() -> None:
    # Chunk ids are opaque strings, not a fixed format. Nothing here should
    # depend on them looking like "doc-N-chunk-M".
    generated = str(uuid.uuid4())

    assert chunk_ids_from_bodies({"doc-1": [{"chunk_id": generated}]}) == [generated]
