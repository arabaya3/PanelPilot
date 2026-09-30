"""Tests for `app/domain/promotion.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

BE-004's acceptance criterion is that promotion happens only via the
verification-clearance path. These run against a real OpenSearch: asserting
"only an approved item results in a production write" against a mock would
assert only that the mock was called, not that the index changed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.ai.retrieval.mappings import DocType, VerificationStatus
from app.core.errors import AuthorizationError, NotFoundError, PromotionError
from app.domain import promotion as promotion_module
from app.domain.promotion import promote_chunk, retract_source
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.ingestion import VerificationDecision, VerificationVerdict
from app.models.schemas.verification import VerificationLabel

# Every table module is imported so SQLAlchemy can resolve the audit row's
# foreign keys into users and staged_documents. Importing only the row
# under test leaves those targets unmapped.
from app.models.tables import calculations, diagnostics, user  # noqa: F401
from app.models.tables.ingestion import PromotionAuditRow, RetractionAuditRow

# A chunk id in the shape the chunker actually produces --
# "<document>#<ordinal>-<digest>" -- not a UUID. These tests used a UUID here,
# which is why none of them noticed that promotion refused every real chunk.
STAGED_DOCUMENT_ID = "3f7a1c2e-0b44-4d21-9a51-6c8e5d2f1a90"
CHUNK_ID = f"{STAGED_DOCUMENT_ID.replace('-', '')}#0001-0a1b2c3d4e5f"
REVIEWER_ID = "9c1d4e6a-2f33-4b78-8e10-5a7b3c9d2e41"
INGESTER_ID = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5a6b"
TENANT_ID = "5d6e7f80-9a1b-4c2d-8e3f-4a5b6c7d8e9f"


def _staged_chunk(*, content: str = "Fault F0001 OVERCURRENT.", **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "brand": "ABB",
        "model": "ACS880",
        "doc_type": DocType.MANUAL.value,
        "page": 88,
        "section": "Fault tracing",
        "source_url": "https://example.invalid/acs880#f0001",
        "verification_status": VerificationStatus.UNVERIFIED.value,
        "content": content,
        "content_vector": [0.1] * 1024,
        "content_hash": f"hash-of-{content}",
        "ingested_by": INGESTER_ID,
    }
    document.update(overrides)
    return document


def _reviewer(user_id: str = REVIEWER_ID) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        email=f"{user_id}@example.invalid",
        tenant_id=TENANT_ID,
        roles=frozenset({Role.REVIEWER}),
    )


APPROVED = VerificationVerdict(decision=VerificationDecision.APPROVED)
REJECTED = VerificationVerdict(decision=VerificationDecision.REJECTED)


def _opensearch_available() -> bool:
    try:
        from app.ai.retrieval.client import get_client

        return bool(get_client().ping())
    except Exception:
        return False


requires_opensearch = pytest.mark.skipif(
    not os.environ.get("OPENSEARCH_URL") or not _opensearch_available(),
    reason="needs a reachable OpenSearch; CI provides one as a service container",
)


@pytest.fixture
def indices() -> Iterator[tuple[str, str]]:
    """Fresh staging and production indices, dropped after."""
    from app.ai.retrieval.client import IndexTarget, ensure_index, get_client

    client = get_client()
    staging = ensure_index(IndexTarget.STAGING, recreate=True)
    production = ensure_index(IndexTarget.PRODUCTION, recreate=True)
    try:
        yield staging, production
    finally:
        client.indices.delete(index=staging, ignore=[404])
        client.indices.delete(index=production, ignore=[404])


@pytest.fixture
def db() -> Iterator[Session]:
    """A real session against a migrated Postgres.

    The audit row is a database write with foreign keys, so a fake session
    would assert only that a method was called — not that the row survived a
    constraint check and a commit.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine)()
    # users.tenant_id is NOT NULL since BE-014, so the tenant comes first.
    session.execute(
        text(
            "INSERT INTO tenants (id, slug, name, is_active, created_at, updated_at) "
            "VALUES (:i, :s, 'Promotion test tenant', true, now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"i": TENANT_ID, "s": f"promo-test-{TENANT_ID[:8]}"},
    )
    # Satisfy the reviewer/ingester foreign keys; users are BE-002's concern.
    for user_id in (REVIEWER_ID, INGESTER_ID):
        session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, is_active, created_at, updated_at) "
                "VALUES (:i, :t, :e, true, now(), now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": user_id, "t": TENANT_ID, "e": f"{user_id}@example.invalid"},
        )
    session.execute(
        text(
            "INSERT INTO crawl_jobs (id, source_id, status, created_at, updated_at) "
            "VALUES (:i, 'promotion-test', 'succeeded', now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"i": STAGED_DOCUMENT_ID},
    )
    session.execute(
        text(
            "INSERT INTO staged_documents (id, crawl_job_id, source_url, content_hash, "
            "created_at, updated_at) VALUES (:i, :i, 'https://x.invalid', :h, now(), now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"i": STAGED_DOCUMENT_ID, "h": STAGED_DOCUMENT_ID},
    )
    session.commit()
    # The reviewer has labelled the chunk correct in the queue: the record
    # promotion now requires, rather than trusting the verdict it is passed.
    _review(session)
    try:
        yield session
    finally:
        session.rollback()
        # Everything this fixture created, not only the audits: other modules
        # count staged documents, and leftovers made them fail when this ran
        # first against a reused database.
        session.execute(text("DELETE FROM promotion_audits"))
        session.execute(text("DELETE FROM retraction_audits"))
        session.execute(
            text("DELETE FROM verification_items WHERE staged_document_id = :i"),
            {"i": STAGED_DOCUMENT_ID},
        )
        session.execute(
            text("DELETE FROM staged_documents WHERE id = :i"), {"i": STAGED_DOCUMENT_ID}
        )
        session.execute(text("DELETE FROM crawl_jobs WHERE id = :i"), {"i": STAGED_DOCUMENT_ID})
        session.commit()
        session.close()


def _review(
    session: Session,
    *,
    chunk_id: str = CHUNK_ID,
    label: str = "correct",
    status: str = "labeled",
    reviewer_id: str = REVIEWER_ID,
) -> None:
    """Record a queue item for a chunk, as the review queue would have."""
    from sqlalchemy import text

    session.execute(text("DELETE FROM verification_items WHERE chunk_id = :c"), {"c": chunk_id})
    session.execute(
        text(
            "INSERT INTO verification_items (id, staged_document_id, chunk_id, assigned_to_id, "
            "status, origin, label, created_at, updated_at) VALUES (gen_random_uuid(), :d, :c, "
            ":r, :s, 'crawl', :l, now(), now())"
        ),
        {"d": STAGED_DOCUMENT_ID, "c": chunk_id, "r": reviewer_id, "s": status, "l": label},
    )
    session.commit()


def _stage(staging: str, document: dict[str, Any], chunk_id: str = CHUNK_ID) -> None:
    from app.ai.retrieval.client import get_client

    get_client().index(index=staging, id=chunk_id, body=document, refresh=True)


def _live(production: str, chunk_id: str = CHUNK_ID) -> dict[str, Any] | None:
    from app.ai.retrieval.client import get_client

    client = get_client()
    if not client.exists(index=production, id=chunk_id):
        return None
    return dict(client.get(index=production, id=chunk_id)["_source"])


# --- the spec's integration test -------------------------------------------


@requires_opensearch
def test_approved_item_results_in_a_production_write(indices: tuple[str, str], db: Session) -> None:
    staging, production = indices
    _stage(staging, _staged_chunk())

    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)

    live = _live(production)
    assert live is not None, "approved item did not reach production"
    # Promotion is what marks it verified; staging content is unverified.
    assert live["verification_status"] == VerificationStatus.VERIFIED.value


@requires_opensearch
def test_rejected_item_results_in_no_production_write(
    indices: tuple[str, str], db: Session
) -> None:
    """The other half: only a 'correct' label may publish."""
    staging, production = indices
    _stage(staging, _staged_chunk())

    with pytest.raises(PromotionError, match="not approved"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=REJECTED)

    assert _live(production) is None, "a rejected item reached production"


@requires_opensearch
def test_staging_copy_survives_promotion(indices: tuple[str, str], db: Session) -> None:
    """Promotion copies; it does not move. ADR 0001 relies on staging keeping its copy."""
    from app.ai.retrieval.client import get_client

    staging, _ = indices
    _stage(staging, _staged_chunk())
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert get_client().exists(index=staging, id=CHUNK_ID)


# --- the edge case the spec names ------------------------------------------


@requires_opensearch
def test_recrawled_chunk_is_never_silently_overwritten(
    indices: tuple[str, str], db: Session
) -> None:
    """Changed content must not replace live text under an existing citation.

    An engineer who acted on a cited passage has no way to learn the text moved
    beneath it, so a changed chunk re-enters as a new pending item instead.
    """
    staging, production = indices
    original = _staged_chunk(content="Fault F0001 OVERCURRENT.")
    _stage(staging, original)
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is not None

    # Re-crawled with different text, same id.
    _stage(staging, _staged_chunk(content="Fault F0001 now means something else."))

    with pytest.raises(PromotionError, match="already live with different content"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)

    # The live text is untouched.
    live = _live(production)
    assert live is not None
    assert live["content"] == original["content"]


@requires_opensearch
def test_repromoting_unchanged_content_is_a_no_op_not_an_error(
    indices: tuple[str, str],
    db: Session,
) -> None:
    """Re-clearing an identical chunk is harmless and must not raise."""
    staging, _ = indices
    _stage(staging, _staged_chunk())
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)


# --- preconditions ----------------------------------------------------------


@requires_opensearch
def test_non_reviewer_cannot_promote(indices: tuple[str, str], db: Session) -> None:
    staging, production = indices
    _stage(staging, _staged_chunk())
    engineer = CurrentUser(
        id="7e8f9a0b-1c2d-4e3f-8a9b-0c1d2e3f4a5b",
        email="eng@example.invalid",
        tenant_id=TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )

    with pytest.raises(AuthorizationError):
        promote_chunk(session=db, reviewer=engineer, chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is None


@requires_opensearch
def test_ingester_cannot_clear_their_own_content(indices: tuple[str, str], db: Session) -> None:
    """Four-eyes, from ADR 0001: one person cannot both stage and bless."""
    staging, production = indices
    _stage(staging, _staged_chunk(ingested_by=REVIEWER_ID))

    with pytest.raises(PromotionError, match="ingester of record"):
        promote_chunk(
            session=db, reviewer=_reviewer(REVIEWER_ID), chunk_id=CHUNK_ID, verdict=APPROVED
        )
    assert _live(production) is None


@requires_opensearch
def test_missing_staged_chunk_is_not_found(indices: tuple[str, str], db: Session) -> None:
    _review(db, chunk_id="nope")
    with pytest.raises(NotFoundError):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id="nope", verdict=APPROVED)


@requires_opensearch
def test_incomplete_chunk_is_refused(indices: tuple[str, str], db: Session) -> None:
    """A chunk missing a citation field must not become an unresolvable citation."""
    staging, production = indices
    incomplete = _staged_chunk()
    incomplete["page"] = None
    _stage(staging, incomplete)

    with pytest.raises(PromotionError, match="missing or null"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is None
    # This path raises ValueError inside index_chunk, which has its own
    # rollback branch separate from the generic one. Without this assertion,
    # deleting that rollback leaves an orphan audit row for content that was
    # never published, and the suite stays green.
    assert db.query(PromotionAuditRow).count() == 0, "an incomplete chunk left an audit row behind"


# --- ADR 0001 §5: the audit row and the index write commit together ---------


@requires_opensearch
def test_promotion_writes_an_audit_row_naming_the_reviewer(
    indices: tuple[str, str], db: Session
) -> None:
    """Live content must always trace to a named human.

    The returned ``audit_id`` has to identify a row that exists — reporting a
    fabricated id is worse than reporting none, because it looks answerable.
    """
    staging, _ = indices
    _stage(staging, _staged_chunk())

    response = promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)

    row = db.get(PromotionAuditRow, uuid.UUID(response.audit_id))
    assert row is not None, "audit_id does not identify a real row"
    assert str(row.reviewer_id) == REVIEWER_ID
    assert row.production_document_id == CHUNK_ID


@requires_opensearch
def test_reviewer_notes_are_recorded_on_the_audit_row(
    indices: tuple[str, str], db: Session
) -> None:
    staging, _ = indices
    _stage(staging, _staged_chunk())

    response = promote_chunk(
        session=db,
        reviewer=_reviewer(),
        chunk_id=CHUNK_ID,
        verdict=VerificationVerdict(
            decision=VerificationDecision.APPROVED, notes="table checked against p.88"
        ),
    )

    row = db.get(PromotionAuditRow, uuid.UUID(response.audit_id))
    assert row is not None
    assert row.notes == "table checked against p.88"


@requires_opensearch
def test_a_refused_promotion_leaves_no_audit_row(indices: tuple[str, str], db: Session) -> None:
    """No publication, no audit entry — the two move together in both directions."""
    staging, production = indices
    _stage(staging, _staged_chunk())

    with pytest.raises(PromotionError):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=REJECTED)

    assert _live(production) is None
    assert db.query(PromotionAuditRow).count() == 0


@requires_opensearch
def test_a_failed_index_write_rolls_the_audit_row_back(
    indices: tuple[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering that makes "commit together" real.

    The audit row is staged and flushed before the index write. If publishing
    fails, the rollback discards it — so there is never an audit entry for
    content that did not go live, and never live content without one.
    """
    staging, production = indices
    _stage(staging, _staged_chunk())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("opensearch is down")

    monkeypatch.setattr("app.domain.promotion.index_chunk", boom)

    with pytest.raises(RuntimeError):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)

    assert _live(production) is None
    assert (
        db.query(PromotionAuditRow).count() == 0
    ), "audit row survived a failed publish; it must roll back with it"


@requires_opensearch
def test_the_audit_row_is_flushed_before_anything_is_published(
    indices: tuple[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering, not just presence.

    OpenSearch is not in the database transaction, so "commit together" is
    achieved by ordering: audit first, flushed, then publish. Reversing it
    still passes a test that only checks the row exists afterwards — the
    window where live content has no audit entry is exactly what this pins.
    """
    staging, _ = indices
    _stage(staging, _staged_chunk())
    observed: list[str] = []

    # Reach the module attribute the same way monkeypatch will replace it,
    # rather than importing the name (which would bind a separate reference).
    real_index_chunk = promotion_module.index_chunk  # type: ignore[attr-defined]

    def recording_index_chunk(*args: object, **kwargs: object) -> None:
        # By the time we publish, the audit row must already have been sent to
        # the database. Autoflush is disabled for this check on purpose: a
        # plain query would trigger a flush itself and then observe the row,
        # so it could not tell "the code flushed" from "my assertion flushed".
        observed.append("publish")
        assert not db.new, (
            "audit row was still pending when the publish started; a caller "
            "with autoflush disabled would publish before it was written"
        )
        with db.no_autoflush:
            assert db.query(PromotionAuditRow).count() == 1, (
                "published before the audit row reached the database; a crash "
                "here would leave live content with no named human attached"
            )
        real_index_chunk(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(promotion_module, "index_chunk", recording_index_chunk)
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)

    assert observed == ["publish"]


@requires_opensearch
def test_the_audit_row_survives_the_callers_commit(indices: tuple[str, str], db: Session) -> None:
    """promote_chunk flushes; the caller commits. Verify the row actually lands.

    Every other assertion here reads through the same session, where an
    uncommitted row is indistinguishable from a committed one. This one commits
    and re-reads through a SEPARATE connection, which is the only way to tell.
    """
    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    staging, _ = indices
    _stage(staging, _staged_chunk())

    response = promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    # promote_chunk must NOT have committed — that is the caller's call, so
    # BE-007 can promote and update its queue item in one transaction.
    other = create_engine(get_settings().database_url.get_secret_value())
    with other.connect() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM promotion_audits WHERE id = :i"),
            {"i": response.audit_id},
        ).scalar()
    assert before == 0, "promote_chunk committed; the caller owns the transaction"

    db.commit()

    with other.connect() as conn:
        after = conn.execute(
            text("SELECT count(*) FROM promotion_audits WHERE id = :i"),
            {"i": response.audit_id},
        ).scalar()
    assert after == 1, "audit row did not survive the caller's commit"


@requires_opensearch
def test_caller_rollback_leaves_live_content_unattributed(
    indices: tuple[str, str], db: Session
) -> None:
    """Pins the known window that caller-owned commit opens.

    ``promote_chunk`` flushes the audit row but does not commit, so BE-007 can
    promote and update its queue item in one transaction. The cost is this: a
    caller that rolls back AFTER a successful publish leaves content live with
    no committed audit row, because the index write is not transactional and
    cannot be rolled back with the database.

    This is asserted rather than fixed. It is inherent to a non-transactional
    publish under a caller-owned transaction — the alternative (committing
    inside) trades it for a window where content is live while the queue item
    still reads pending, which silently re-promotes on retry. Recovery is
    re-promotion, which this test also verifies.

    If this test starts failing, the transaction contract changed; read the
    ``session`` note on ``promote_chunk`` before adjusting it.
    """
    staging, production = indices
    _stage(staging, _staged_chunk())

    response = promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    # The caller's own later step fails and it rolls back.
    db.rollback()

    assert _live(production) is not None, "publish is not transactional; it stands"
    assert (
        db.query(PromotionAuditRow).count() == 0
    ), "expected the audit row to be discarded with the caller's rollback"

    # Recoverable: the content hash is unchanged, so re-promotion is a no-op on
    # the index and writes the audit row that was lost.
    retry = promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    db.commit()
    row = db.get(PromotionAuditRow, uuid.UUID(retry.audit_id))
    assert row is not None, "retry did not restore the audit trail"
    assert response.audit_id != retry.audit_id


# --- promotion acts on the review queue's record, not on a claim --------------


@requires_opensearch
def test_a_real_chunk_id_is_promoted(indices: tuple[str, str], db: Session) -> None:
    """Every real chunk failed here: its id was parsed as a staged-document UUID."""
    staging, production = indices
    _stage(staging, _staged_chunk())

    response = promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    db.commit()

    assert "#" in CHUNK_ID
    assert _live(production) is not None
    audit = db.get(PromotionAuditRow, uuid.UUID(response.audit_id))
    assert audit is not None
    assert str(audit.staged_document_id) == STAGED_DOCUMENT_ID
    assert audit.production_document_id == CHUNK_ID


@requires_opensearch
@pytest.mark.parametrize(
    ("label", "status", "reviewer_id"),
    [
        ("incorrect", "escalated", REVIEWER_ID),  # judged wrong
        ("correct", "pending", REVIEWER_ID),  # not yet labelled
        ("correct", "labeled", INGESTER_ID),  # labelled by somebody else
    ],
)
def test_a_verdict_nobody_recorded_publishes_nothing(
    indices: tuple[str, str], db: Session, label: str, status: str, reviewer_id: str
) -> None:
    """The verdict argument is a claim; the queue item is the record."""
    staging, production = indices
    _stage(staging, _staged_chunk())
    _review(db, label=label, status=status, reviewer_id=reviewer_id)

    with pytest.raises(PromotionError, match="not been labelled correct"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is None


@requires_opensearch
def test_a_chunk_with_no_ingester_is_refused(indices: tuple[str, str], db: Session) -> None:
    """Four-eyes used to be skipped when there was nobody to compare against."""
    staging, production = indices
    chunk = _staged_chunk()
    del chunk["ingested_by"]
    _stage(staging, chunk)

    with pytest.raises(PromotionError, match="no ingester of record"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is None


# --- clear_item: labelling is clearance ----------------------------------------


def _pending_item(db: Session) -> uuid.UUID:
    """A crawled chunk assigned to the reviewer and not yet labelled."""
    from sqlalchemy import text

    _review(db, status="pending", label="")
    db.execute(
        text("UPDATE verification_items SET label = NULL WHERE chunk_id = :c"), {"c": CHUNK_ID}
    )
    db.commit()
    return uuid.UUID(
        str(
            db.execute(
                text("SELECT id FROM verification_items WHERE chunk_id = :c"), {"c": CHUNK_ID}
            ).scalar_one()
        )
    )


@requires_opensearch
def test_labelling_correct_publishes_in_the_same_transaction(
    indices: tuple[str, str], db: Session
) -> None:
    staging, production = indices
    _stage(staging, _staged_chunk())
    item_id = _pending_item(db)

    row = promotion_module.clear_item(
        session=db, reviewer=_reviewer(), item_id=item_id, label=VerificationLabel.CORRECT
    )
    db.commit()

    assert row.status == "labeled"
    live = _live(production)
    assert live is not None
    assert live["verification_status"] == VerificationStatus.VERIFIED.value


@requires_opensearch
def test_an_escalating_label_publishes_nothing(indices: tuple[str, str], db: Session) -> None:
    staging, production = indices
    _stage(staging, _staged_chunk())
    item_id = _pending_item(db)

    row = promotion_module.clear_item(
        session=db,
        reviewer=_reviewer(),
        item_id=item_id,
        label=VerificationLabel.INCORRECT,
        note="the manual says 63 A, not 36 A",
    )

    assert row.status == "escalated"
    assert _live(production) is None


@requires_opensearch
def test_a_failed_publication_takes_the_label_with_it(
    indices: tuple[str, str], db: Session
) -> None:
    """Never marked correct without being published: here, four-eyes refuses."""
    from sqlalchemy import text

    staging, production = indices
    _stage(staging, _staged_chunk(ingested_by=REVIEWER_ID))
    item_id = _pending_item(db)

    with pytest.raises(PromotionError):
        promotion_module.clear_item(
            session=db, reviewer=_reviewer(), item_id=item_id, label=VerificationLabel.CORRECT
        )
    db.rollback()  # what the request does when the handler raises

    status: str = db.execute(
        text("SELECT status FROM verification_items WHERE id = :i"), {"i": item_id}
    ).scalar_one()
    assert status == "pending"
    assert _live(production) is None


def test_only_a_reviewer_may_label() -> None:
    """Labelling is the verdict promotion acts on, so it takes the same role."""
    engineer = CurrentUser(
        id=REVIEWER_ID, email="e@example.invalid", tenant_id=TENANT_ID, roles=frozenset()
    )
    with pytest.raises(AuthorizationError):
        promotion_module.clear_item(
            session=cast(Session, None),
            reviewer=engineer,
            item_id=uuid.uuid4(),
            label=VerificationLabel.CORRECT,
        )


# --- retraction: the audited way back out ------------------------------------

SOURCE = "https://example.invalid/acs880#f0001"


def _publish(production: str, chunk_id: str, **overrides: Any) -> None:
    """Put a chunk straight into production, as a past promotion would have."""
    from app.ai.retrieval.client import get_client

    body = _staged_chunk(verification_status=VerificationStatus.VERIFIED.value, **overrides)
    get_client().index(index=production, id=chunk_id, body=body, refresh=True)


@requires_opensearch
def test_retraction_removes_every_live_passage_of_the_document_and_nothing_else(
    indices: tuple[str, str], db: Session
) -> None:
    staging, production = indices
    _publish(production, "doc#0001", content="one")
    _publish(production, "doc#0002", content="two")
    _publish(production, "other#0001", source_url="https://example.invalid/other")
    _stage(staging, _staged_chunk(), chunk_id="doc#0001")

    audit = retract_source(
        session=db, reviewer=_reviewer(), source_url=SOURCE, reason="  withdrawn by ABB  "
    )
    db.commit()

    assert _live(production, "doc#0001") is None
    assert _live(production, "doc#0002") is None
    assert _live(production, "other#0001") is not None
    # Staging keeps its copy, as it does through promotion.
    from app.ai.retrieval.client import get_client

    assert get_client().exists(index=staging, id="doc#0001")

    row = db.get(RetractionAuditRow, audit.id)
    assert row is not None
    assert (row.source_url, str(row.reviewer_id), row.reason) == (
        SOURCE,
        REVIEWER_ID,
        "withdrawn by ABB",
    )
    assert row.chunk_ids == ["doc#0001", "doc#0002"]
    assert row.content_hashes == ["hash-of-one", "hash-of-two"]


@requires_opensearch
def test_a_retracted_revision_cannot_be_promoted_again(
    indices: tuple[str, str], db: Session
) -> None:
    """Its staged copy is still there; clearing it must not republish it."""
    staging, production = indices
    _stage(staging, _staged_chunk())
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    db.commit()

    retract_source(session=db, reviewer=_reviewer(), source_url=SOURCE, reason="withdrawn")
    db.commit()
    _review(db)

    with pytest.raises(PromotionError, match="was retracted"):
        promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is None


@requires_opensearch
def test_a_new_revision_of_a_retracted_document_can_still_be_published(
    indices: tuple[str, str], db: Session
) -> None:
    """The refusal is per revision: a corrected manual at the same URL may go live."""
    staging, production = indices
    _publish(production, "old#0001", content="old")
    retract_source(session=db, reviewer=_reviewer(), source_url=SOURCE, reason="superseded")
    db.commit()

    _stage(staging, _staged_chunk(content="corrected"))
    promote_chunk(session=db, reviewer=_reviewer(), chunk_id=CHUNK_ID, verdict=APPROVED)
    assert _live(production) is not None


@requires_opensearch
def test_retraction_needs_a_reason_and_something_live(
    indices: tuple[str, str], db: Session
) -> None:
    _, production = indices
    with pytest.raises(NotFoundError, match="nothing live cites"):
        retract_source(session=db, reviewer=_reviewer(), source_url=SOURCE, reason="x")

    _publish(production, "doc#0001")
    with pytest.raises(PromotionError, match="needs a reason"):
        retract_source(session=db, reviewer=_reviewer(), source_url=SOURCE, reason="  ")
    assert _live(production, "doc#0001") is not None


@requires_opensearch
def test_a_failed_delete_records_nothing(
    indices: tuple[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No audit row for a retraction that did not happen."""
    _, production = indices
    _publish(production, "doc#0001")

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(promotion_module, "bulk", broken)
    with pytest.raises(RuntimeError):
        retract_source(session=db, reviewer=_reviewer(), source_url=SOURCE, reason="withdrawn")

    assert db.query(RetractionAuditRow).count() == 0
    assert _live(production, "doc#0001") is not None


def test_only_a_reviewer_may_retract() -> None:
    engineer = CurrentUser(
        id=REVIEWER_ID,
        email="engineer@example.invalid",
        tenant_id=TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )
    with pytest.raises(AuthorizationError):
        retract_source(
            session=None,  # type: ignore[arg-type]
            reviewer=engineer,
            source_url=SOURCE,
            reason="withdrawn",
        )
