"""Tests for `app/domain/diagnostics.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

This is the endpoint every upstream guardrail relies on. The invariants below
are not about this function behaving reasonably; they are about it being
unable to behave unreasonably. In particular: when the guardrail refuses, the
model must not be called **at all**, asserted by call count rather than by
inspecting the answer — generating and then discarding is one more branch that
can be forgotten, and a forgotten discard is an unsourced answer.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFoundError, ValidationError
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.diagnostics import DiagnosticRequest, EquipmentContext
from app.models.schemas.search import Citation, RetrievedPassage
from app.models.tables import calculations, escalation, ingestion  # noqa: F401
from app.models.tables.diagnostics import DiagnosticSessionRow, DiagnosticTurnRow
from app.models.tables.tenant import TenantRow

_REPLAY_CONFIDENT_FLOOR = diagnostics_domain._REPLAY_CONFIDENT

_TENANT_ID = "44444444-4444-4444-4444-444444444444"

_CITATION = Citation(
    document_id="abb-acs880-fw",
    document_title="ACS880 firmware manual",
    manufacturer="ABB",
    page=88,
    section="3 Fault tracing",
)


def _passage(score: float = 0.9) -> RetrievedPassage:
    return RetrievedPassage(
        id="p1",
        text="F0001 overcurrent: the acceleration time is too short for the load inertia.",
        score=score,
        citation=_CITATION,
    )


def _other_passage(doc_id: str) -> RetrievedPassage:
    """A retrieved passage the answer does not cite."""
    return RetrievedPassage(
        id=doc_id,
        text="Unrelated guidance.",
        score=0.7,
        citation=Citation(document_id=doc_id, document_title="Other manual", manufacturer="ABB"),
    )


def _valid_tool_payload() -> dict[str, Any]:
    return {
        "summary": "The acceleration time is too short for the load inertia.",
        "summary_citation_ids": ["p1"],
        "steps": [
            {
                "order": 1,
                "instruction": "Isolate the drive and verify zero voltage.",
                "rationale": "Work on a live DC link is fatal.",
                "citation_ids": ["p1"],
                "severity": "critical",
            }
        ],
        "severity": "critical",
        "equipment_model": "ACS880",
    }


class _Block:
    def __init__(self, kind: str, name: str | None = None, payload: Any = None) -> None:
        self.type = kind
        self.name = name
        self.input = payload


class _Message:
    def __init__(self, *blocks: _Block) -> None:
        self.content = list(blocks)


class _CountingClient:
    """Records how many times generation was invoked."""

    def __init__(self, payload: Any = None) -> None:
        self.calls = 0
        self._payload = payload if payload is not None else _valid_tool_payload()
        self.messages = self

    def create(self, **_kwargs: Any) -> _Message:
        self.calls += 1
        return _Message(_Block("tool_use", "emit_diagnosis", self._payload))


class _Turn:
    """Stands in for a persisted turn row."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeSession:
    """A session recording what would be persisted."""

    def __init__(
        self, *, existing: object | None = None, positions: list[int] | None = None
    ) -> None:
        self.added: list[Any] = []
        self.flushes = 0
        self.locked = False
        self._existing = existing
        self._positions = positions or []

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def execute(self, statement: Any) -> Any:
        # The row lock taken before reading the last turn position. Recorded
        # so a test can assert it happened; the real behaviour is covered by
        # the database tests below, because a fake cannot lock anything.
        self.locked = "FOR UPDATE" in str(statement).upper()
        return None

    def scalars(self, statement: Any) -> Any:
        # The domain issues two kinds of select: one for a session row, one for
        # the last turn position. Distinguished by what the caller asked for.
        rendered = str(statement)
        session = self

        class _Result:
            def one_or_none(self) -> Any:
                if "diagnostic_sessions" in rendered:
                    return session._existing
                return session._positions[-1] if session._positions else None

            def all(self) -> list[Any]:
                return []

        return _Result()


class _Conversation:
    def __init__(self, conversation_id: str = "11111111-1111-1111-1111-111111111111") -> None:
        import uuid as _uuid

        self.id = _uuid.UUID(conversation_id)
        self.tenant_id = _uuid.UUID(_TENANT_ID)


def _user() -> CurrentUser:
    # A real UUID string, as a JWT claim actually carries. The previous
    # fixture used "tenant-1", which the fake session accepted and a real
    # database would not — the column is a UUID, and that mismatch crashed
    # every new conversation until a review caught it.
    return CurrentUser(
        id="user-1",
        email="e@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )


def _request(**overrides: Any) -> DiagnosticRequest:
    payload: dict[str, Any] = {
        "symptom": "F0001 overcurrent on acceleration",
        "equipment": EquipmentContext(manufacturer="ABB", model="ACS880"),
    }
    payload.update(overrides)
    return DiagnosticRequest.model_validate(payload)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> _CountingClient:
    """Wire the domain to fakes, returning the generation client.

    The guardrail threshold is set explicitly rather than left to ambient
    configuration. Without settings, `_resolve_threshold` correctly falls back
    to 1.0 and refuses everything — so a test relying on ambient config would
    measure the fallback rather than the orchestration.
    """
    client = _CountingClient()
    monkeypatch.setattr(diagnostics_domain, "_anthropic_client", lambda: client)
    monkeypatch.setattr(diagnostics_domain, "consume_free_question", lambda **_kw: None)
    monkeypatch.setattr(
        "app.ai.guardrails.cite_or_refuse._resolve_threshold",
        lambda threshold: 0.6 if threshold is None else threshold,
    )

    class _Settings:
        llm_model = "claude-sonnet-5"

    monkeypatch.setattr(diagnostics_domain, "get_settings", _Settings)
    return client


def _retrieving(monkeypatch: pytest.MonkeyPatch, passages: list[RetrievedPassage]) -> None:
    monkeypatch.setattr(diagnostics_domain, "search", lambda *_a, **_kw: passages)


# --- (a) a high-confidence known query -------------------------------------


def test_a_supported_question_returns_a_cited_structured_card(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The acceptance criterion, at the domain boundary."""
    _retrieving(monkeypatch, [_passage()])
    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )

    assert response.diagnosis is not None, response.refusal_message
    assert response.diagnosis.steps
    assert response.answer is not None
    assert response.answer.citations, "an answered response must carry its citations"
    assert response.refusal_message is None
    assert wired.calls == 1


def test_the_answer_and_the_card_agree(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Prose and structure are two renderings of one answer, not two answers."""
    _retrieving(monkeypatch, [_passage()])
    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert response.diagnosis is not None
    assert response.answer is not None
    assert response.answer.text == response.diagnosis.summary


# --- (b) a no-match query makes ZERO generation calls -----------------------


def test_a_refusal_never_invokes_the_model(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Asserted by call count, not by reading the answer.

    Generating and then discarding is more expensive and strictly less safe:
    the discard is one more branch that can be forgotten, and a forgotten
    discard is an unsourced answer reaching an engineer.
    """
    _retrieving(monkeypatch, [])
    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )

    assert wired.calls == 0, "the model was invoked for a question with no evidence"
    assert response.diagnosis is None
    assert response.refusal_message


def test_weak_evidence_also_refuses_without_generating(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Not only the no-evidence case: below-threshold evidence short-circuits too."""
    _retrieving(monkeypatch, [_passage(score=0.01)])
    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert wired.calls == 0
    assert response.diagnosis is None


def test_a_refusal_still_reports_its_confidence(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """An engineer who is refused deserves to see how close it came."""
    _retrieving(monkeypatch, [_passage(score=0.01)])
    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert response.low_confidence
    assert response.confidence.retrieval_score == pytest.approx(0.01)


# --- (c) malformed generation degrades, never 500s -------------------------


def test_unparseable_output_becomes_a_refusal_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response the system could not parse is one it cannot vouch for."""
    client = _CountingClient(payload={"summary": "no steps, no citations"})
    monkeypatch.setattr(diagnostics_domain, "_anthropic_client", lambda: client)
    monkeypatch.setattr(diagnostics_domain, "consume_free_question", lambda **_kw: None)
    monkeypatch.setattr(
        "app.ai.guardrails.cite_or_refuse._resolve_threshold",
        lambda threshold: 0.6 if threshold is None else threshold,
    )

    class _Settings:
        llm_model = "claude-sonnet-5"

    monkeypatch.setattr(diagnostics_domain, "get_settings", _Settings)
    _retrieving(monkeypatch, [_passage()])

    response = diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert client.calls == 1
    assert response.diagnosis is None
    assert response.refusal_message


# --- the quota is charged before any work ----------------------------------


def test_only_a_delivered_answer_is_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The policy `TenantRow.free_questions_used` records, honoured.

    "A failed or refused answer must not burn a question the engineer never
    received." Charging for a refusal bills for the one outcome the engineer
    cannot use.
    """
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [_passage()])
    diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert charged == ["x"]


def test_a_refusal_is_not_charged(monkeypatch: pytest.MonkeyPatch, wired: _CountingClient) -> None:
    """The other half of the same policy."""
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [])
    diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert charged == [], "a refusal burned a question the engineer never received"


def test_unparseable_output_is_not_charged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response withheld for failing validation was never received either."""
    client = _CountingClient(payload={"summary": "no steps"})
    monkeypatch.setattr(diagnostics_domain, "_anthropic_client", lambda: client)
    monkeypatch.setattr(
        "app.ai.guardrails.cite_or_refuse._resolve_threshold",
        lambda threshold: 0.6 if threshold is None else threshold,
    )

    class _Settings:
        llm_model = "claude-sonnet-5"

    monkeypatch.setattr(diagnostics_domain, "get_settings", _Settings)
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [_passage()])
    diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert charged == []


def test_the_advisory_quota_check_is_not_the_gate() -> None:
    """`check_free_question_allowed` reports; `consume_free_question` charges.

    Using the advisory one would let concurrent requests each see "allowed"
    and all proceed — the race a prior review demonstrated live.
    """
    import inspect

    source = inspect.getsource(diagnostics_domain.run_diagnosis)
    assert "consume_free_question" in source
    assert "check_free_question_allowed" not in source


# --- turns are recorded, refusals included ---------------------------------


def test_an_answered_turn_is_recorded(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    _retrieving(monkeypatch, [_passage()])
    db = _FakeSession()
    diagnostics_domain.run_diagnosis(session=cast(Session, db), user=_user(), request=_request())
    assert any(getattr(row, "question", None) == _request().symptom for row in db.added)


def test_a_refused_turn_is_recorded_too(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Record refusals too.

    A history showing only answered turns reads as though nothing was ever
    declined — exactly the history an engineer needs when asking why the
    assistant would not help.
    """
    _retrieving(monkeypatch, [])
    db = _FakeSession()
    diagnostics_domain.run_diagnosis(session=cast(Session, db), user=_user(), request=_request())
    recorded = [row for row in db.added if hasattr(row, "question")]
    assert recorded, "the refusal was not recorded"
    assert recorded[0].answer


# --- tenant scoping ---------------------------------------------------------


def test_an_unknown_session_is_not_found(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    _retrieving(monkeypatch, [_passage()])
    with pytest.raises(NotFoundError):
        diagnostics_domain.run_diagnosis(
            session=cast(Session, _FakeSession(existing=None)),
            user=_user(),
            request=_request(session_id="22222222-2222-2222-2222-222222222222"),
        )


def test_another_tenants_session_is_not_found_rather_than_forbidden(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Distinguishing "forbidden" from "missing" is a membership oracle.

    It tells a caller that a session id they cannot read does exist.
    """
    _retrieving(monkeypatch, [_passage()])
    with pytest.raises(NotFoundError):
        diagnostics_domain.run_diagnosis(
            session=cast(Session, _FakeSession(existing=None)),
            user=_user(),
            request=_request(session_id="33333333-3333-3333-3333-333333333333"),
        )


def test_a_malformed_session_id_is_not_found(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Not a 500 — a bad id from a client is a client error, not a crash."""
    _retrieving(monkeypatch, [_passage()])
    with pytest.raises(NotFoundError):
        diagnostics_domain.run_diagnosis(
            session=cast(Session, _FakeSession()),
            user=_user(),
            request=_request(session_id="not-a-uuid"),
        )


def test_loading_a_session_is_scoped_by_tenant() -> None:
    """An id alone would let one tenant read another's conversation."""
    import inspect

    source = inspect.getsource(diagnostics_domain._load_session)
    assert "tenant_id" in source


# --- retrieval is production-only -------------------------------------------


def test_the_chat_path_cannot_reach_staging() -> None:
    """The whole ADR 0001 isolation claim, at the one place it matters."""
    import inspect

    source = inspect.getsource(diagnostics_domain)
    assert "search_staging" not in source
    assert "IndexTarget" not in source


# --- against a real database ------------------------------------------------
#
# The unit tests above use a fake session, which cannot type-check a column,
# cannot enforce a constraint, and cannot lock a row. A review found a crash
# those tests could never have caught: `CurrentUser.tenant_id` is a string and
# every tenant-scoped column is a UUID, so creating a conversation raised on
# the first real request while every fake-backed test passed. These run against
# the migrated Postgres CI provides.


def _database_available() -> bool:
    try:
        from app.core.config import get_settings

        engine = create_engine(get_settings().database_url.get_secret_value())
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") or not _database_available(),
    reason="needs a migrated Postgres; CI provides one as a service container",
)

_DB_SLUG_PREFIX = "diagtest-"


@pytest.fixture
def db() -> Iterator[Session]:
    """A real session, cleaned of anything this module created."""
    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            text(
                "DELETE FROM diagnostic_turns WHERE session_id IN (SELECT id FROM "
                "diagnostic_sessions WHERE tenant_id IN "
                "(SELECT id FROM tenants WHERE slug LIKE :p))"
            ),
            {"p": f"{_DB_SLUG_PREFIX}%"},
        )
        session.execute(
            text(
                "DELETE FROM diagnostic_sessions WHERE tenant_id IN "
                "(SELECT id FROM tenants WHERE slug LIKE :p)"
            ),
            {"p": f"{_DB_SLUG_PREFIX}%"},
        )
        session.execute(
            text("DELETE FROM tenants WHERE slug LIKE :p"), {"p": f"{_DB_SLUG_PREFIX}%"}
        )
        session.commit()
        session.close()


@pytest.fixture
def db_user(db: Session) -> CurrentUser:
    """A caller whose tenant actually exists."""
    tenant = TenantRow(name="Diag Test", slug=f"{_DB_SLUG_PREFIX}{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return CurrentUser(
        id=str(uuid.uuid4()),
        email="e@example.com",
        tenant_id=str(tenant.id),
        roles=frozenset({Role.ENGINEER}),
    )


@requires_db
def test_a_new_conversation_persists(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The crash a fake session could never have caught.

    `CurrentUser.tenant_id` is a string; the column is a UUID. Creating a
    conversation without converting raised on every first message, which is
    the acceptance path.
    """
    _retrieving(monkeypatch, [_passage()])
    response = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    assert response.diagnosis is not None

    stored = db.scalars(
        select(DiagnosticTurnRow).where(
            DiagnosticTurnRow.session_id == uuid.UUID(response.session_id)
        )
    ).all()
    assert len(stored) == 1
    assert stored[0].position == 1
    assert stored[0].refused is False


@requires_db
def test_an_answered_turn_replays_as_an_answer(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Not as a refusal carrying the answer text.

    Getting this backwards makes a past successful diagnosis reload as "the
    assistant declined to help" — both wrong and alarming.
    """
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    assert len(history.turns) == 1
    replayed = history.turns[0].response
    assert replayed.refusal_message is None, "an answered turn replayed as a refusal"
    assert replayed.answer is not None
    assert replayed.answer.text


@requires_db
def test_a_refused_turn_replays_as_a_refusal(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    _retrieving(monkeypatch, [])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    replayed = history.turns[0].response
    assert replayed.refusal_message
    assert replayed.diagnosis is None


@requires_db
def test_turns_replay_in_the_order_they_happened(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """A history that reorders question and answer is worse than none."""
    _retrieving(monkeypatch, [_passage()])
    first = diagnostics_domain.run_diagnosis(
        session=db, user=db_user, request=_request(symptom="first question")
    )
    diagnostics_domain.run_diagnosis(
        session=db,
        user=db_user,
        request=_request(symptom="second question", session_id=first.session_id),
    )

    history = diagnostics_domain.get_session(session=db, user=db_user, session_id=first.session_id)
    assert [turn.request.symptom for turn in history.turns] == [
        "first question",
        "second question",
    ]


@requires_db
def test_two_turns_cannot_share_a_position(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The constraint backing the row lock.

    The lock stops the race; this refuses the result if a future caller ever
    bypasses it, rather than leaving the history to order two turns arbitrarily.
    """
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    db.flush()

    db.add(
        DiagnosticTurnRow(
            session_id=uuid.UUID(created.session_id),
            position=1,
            question="a colliding turn",
            answer="text",
            refused=False,
            confidence=0.5,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


@requires_db
def test_another_tenants_conversation_is_invisible(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Tenant scoping against a real filter.

    The fake session returned None regardless, so this passed for the wrong
    reason even while the UUID/str mismatch made the filter match nothing.
    """
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    db.flush()

    other = TenantRow(name="Other", slug=f"{_DB_SLUG_PREFIX}{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    intruder = CurrentUser(
        id=str(uuid.uuid4()),
        email="other@example.com",
        tenant_id=str(other.id),
        roles=frozenset({Role.ENGINEER}),
    )

    with pytest.raises(NotFoundError):
        diagnostics_domain.get_session(session=db, user=intruder, session_id=created.session_id)


@requires_db
def test_a_replayed_refusal_shows_the_original_message(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Not a placeholder. The engineer needs to see why it declined."""
    _retrieving(monkeypatch, [])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    original = created.refusal_message

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    assert history.turns[0].response.refusal_message == original


@requires_db
def test_a_replayed_refusal_is_marked_low_confidence(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """A refusal shown without the uncertainty banner reads as an answer."""
    _retrieving(monkeypatch, [])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    assert history.turns[0].response.low_confidence


@requires_db
def test_a_confident_answer_replays_without_the_banner(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The banner is about how much to trust the answer.

    Showing it on every replayed turn would train engineers to ignore it,
    which is the same failure as never showing it at all.
    """
    _retrieving(monkeypatch, [_passage(score=0.95)])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    assert created.confidence.overall >= _REPLAY_CONFIDENT_FLOOR

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    replayed = history.turns[0].response
    assert not replayed.low_confidence, "a confident answer replayed with an uncertainty banner"


@requires_db
def test_a_weak_answer_replays_with_the_banner(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The other half: the stored score is what decides, not a constant.

    Ten passages retrieved, one cited. The evidence cleared the guardrail, but
    the answer rests on a tenth of it — which is exactly the case the banner
    exists for, and is not visible from the retrieval score alone.
    """
    _retrieving(
        monkeypatch,
        [_passage(), *(_other_passage(f"other-{n}") for n in range(9))],
    )
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    assert created.confidence.overall < _REPLAY_CONFIDENT_FLOOR

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    assert history.turns[0].response.low_confidence


@requires_db
def test_a_tenant_claim_that_is_not_a_uuid_is_refused(
    db: Session, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The claim is a string; every tenant column is a UUID.

    Passing the string straight through raised on every new conversation —
    a crash no fake session could catch, because a fake never type-checks a
    column. Converting is what fixed it; this is what keeps it fixed.
    """
    _retrieving(monkeypatch, [_passage()])
    malformed = CurrentUser(
        id=str(uuid.uuid4()),
        email="e@example.com",
        tenant_id="tenant-1",
        roles=frozenset({Role.ENGINEER}),
    )
    with pytest.raises(NotFoundError):
        diagnostics_domain.run_diagnosis(session=db, user=malformed, request=_request())


@requires_db
def test_a_second_turn_takes_the_next_position(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Against the real unique constraint.

    A position that does not advance collides on the second turn, so this
    fails loudly rather than silently overwriting the conversation's order.
    """
    _retrieving(monkeypatch, [_passage()])
    first = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    diagnostics_domain.run_diagnosis(
        session=db, user=db_user, request=_request(session_id=first.session_id)
    )
    db.flush()

    positions = db.scalars(
        select(DiagnosticTurnRow.position)
        .where(DiagnosticTurnRow.session_id == uuid.UUID(first.session_id))
        .order_by(DiagnosticTurnRow.position)
    ).all()
    assert list(positions) == [1, 2]


@requires_db
def test_concurrent_turns_do_not_collide(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Two turns racing on the same conversation must not share a position.

    Single-threaded tests cannot show this: the read-modify-write on
    ``position`` only collides when two transactions interleave. Without the
    row lock both read the same maximum and both write N+1, and the unique
    constraint then rejects one — so the failure mode without the lock is a
    lost turn, and without the constraint it is a history that orders two
    turns arbitrarily.

    Threads rather than a mocked lock, because a mock would assert the call
    happened rather than that it worked.
    """
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())
    db.commit()

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url.get_secret_value())
    maker = sessionmaker(bind=engine)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def ask(label: str) -> None:
        own = maker()
        try:
            # Both threads arrive together, so the read-modify-write genuinely
            # interleaves rather than happening to serialise.
            barrier.wait(timeout=10)
            diagnostics_domain.run_diagnosis(
                session=own,
                user=db_user,
                request=_request(symptom=label, session_id=created.session_id),
            )
            own.commit()
        except BaseException as exc:
            errors.append(exc)
            own.rollback()
        finally:
            own.close()

    threads = [threading.Thread(target=ask, args=(f"concurrent {n}",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"a concurrent turn failed: {errors}"

    db.commit()
    positions = db.scalars(
        select(DiagnosticTurnRow.position)
        .where(DiagnosticTurnRow.session_id == uuid.UUID(created.session_id))
        .order_by(DiagnosticTurnRow.position)
    ).all()
    # Three turns: the first, plus both concurrent ones, each at its own
    # position. A duplicate here means the lock did not hold.
    assert list(positions) == [1, 2, 3], f"positions collided: {list(positions)}"


# --- the stream -------------------------------------------------------------


def test_the_stream_ends_with_the_complete_response(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The acceptance criterion: the card arrives, streamed.

    A client that ignores every progress event and reads only the last one
    must lose nothing — the stages are a progress indicator, not a protocol
    the frontend reassembles an answer from.
    """
    _retrieving(monkeypatch, [_passage()])
    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert events[-1].event == "result"
    payload = events[-1].data
    assert payload["diagnosis"] is not None
    assert payload["diagnosis"]["steps"]
    assert payload["answer"]["citations"]


def test_no_partial_answer_is_ever_streamed(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Every event before `result` carries no answer text.

    Streaming a partially-built answer would put text in front of an engineer
    before the guardrail had ruled on whether it may be shown at all — and a
    refusal arriving after three paragraphs of confident-sounding draft is not
    a refusal.
    """
    _retrieving(monkeypatch, [_passage()])
    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    summary = _valid_tool_payload()["summary"]
    for event in events[:-1]:
        assert summary not in json.dumps(event.data), f"{event.event} leaked the answer"


def test_a_refusal_streams_as_a_refusal(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    _retrieving(monkeypatch, [])
    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert [event.event for event in events] == ["retrieving", "refused", "result"]
    assert events[-1].data["diagnosis"] is None
    assert events[-1].data["refusal_message"]


def test_the_stream_reports_progress_before_the_result(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """An engineer watching a blank panel cannot tell slow from hung."""
    _retrieving(monkeypatch, [_passage()])
    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )
    assert [event.event for event in events] == ["retrieving", "generated", "result"]


def test_a_streamed_refusal_still_makes_no_generation_call(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The streaming path must not become a way around the guardrail."""
    _retrieving(monkeypatch, [])
    list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )
    assert wired.calls == 0


def test_every_event_renders_as_a_valid_frame(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """What the route actually writes to the socket."""
    _retrieving(monkeypatch, [_passage()])
    for event in diagnostics_domain.stream_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    ):
        frame = event.render()
        assert frame.startswith("event: ")
        assert frame.endswith("\n\n")
        body = frame.split("data: ", 1)[1].rsplit("\n\n", 1)[0]
        json.loads(body)


def test_an_abandoned_stream_is_not_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """A client that disconnects mid-stream must not be billed.

    The charge sits after the final yield, so it only runs once the consumer
    has asked for the item beyond the result — which for a StreamingResponse
    means the result frame reached the transport. A generator abandoned part
    way is never resumed, so the charge never happens.

    This is the interaction a round-2 review found: moving the charge to
    "only on a delivered answer" and adding streaming were each correct, and
    together reintroduced the exact billing violation the first fix removed.
    """
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [_passage()])

    stream = diagnostics_domain.stream_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    # Consume up to and including the result frame, then walk away — the state
    # a client is in when it disconnects after the answer was generated but
    # before the response finished. This is the case that mattered: the work
    # is done and paid for upstream, and the question is whether we bill.
    seen = []
    for event in stream:
        seen.append(event.event)
        if event.event == "generated":
            break
    stream.close()

    assert seen == ["retrieving", "generated"], "the stream did not reach generation"
    assert charged == [], "an abandoned stream burned a question after generating"


def test_a_completed_stream_is_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The other half: a delivered answer must still be billed."""
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [_passage()])
    list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )
    assert charged == ["x"]


def test_a_streamed_refusal_is_not_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [])
    list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )
    assert charged == []


# --- a turn that fails after streaming has begun ----------------------------
#
# The bug these pin: a real query produced `event: retrieving` and then
# nothing. OpenSearch rejected the query with `Pipeline
# panelpilot-hybrid-symptom_description is not defined`, the exception escaped
# the generator, and because a `StreamingResponse` has already sent 200 and the
# SSE headers by the time the body runs, there was no status code left to
# return. The client saw a body that stopped mid-stream — identical to a
# dropped connection, and impossible to tell from a hung backend.


def _failing_retrieval(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Make retrieval raise, as an unreachable OpenSearch does."""

    def boom(*_a: object, **_kw: object) -> list[RetrievedPassage]:
        raise exc

    monkeypatch.setattr(diagnostics_domain, "search", boom)


def test_a_failure_mid_stream_still_ends_with_a_terminal_event(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The core regression: the stream must never just stop.

    Without this, the generator died after `retrieving` and the frontend could
    only report "connection lost" — which sends the engineer to check their
    network for a server-side fault.
    """
    _failing_retrieval(monkeypatch, RuntimeError("Pipeline ... is not defined"))

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert [event.event for event in events] == ["retrieving", "refused", "result"]


def test_the_terminal_event_is_a_well_formed_refusal(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """So the frontend renders it through the path it already has.

    A `result` that failed `DiagnosticResponse` validation would raise inside
    the generator and reintroduce the silent stop by another door.
    """
    _failing_retrieval(monkeypatch, RuntimeError("boom"))

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    payload = events[-1].data
    assert payload["diagnosis"] is None
    assert payload["answer"] is None
    assert payload["refusal_message"]
    assert payload["low_confidence"] is True


def test_a_failed_turn_reports_zero_confidence(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """Nothing was retrieved or scored, so every signal is zero.

    A placeholder above zero would be a fabricated measurement on a turn that
    performed no measurement.
    """
    _failing_retrieval(monkeypatch, RuntimeError("boom"))

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert events[-1].data["confidence"]["overall"] == 0.0
    assert events[-1].data["confidence"]["retrieval_score"] == 0.0


def test_a_failed_turn_is_not_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """A failed turn must not consume the tenant's quota.

    Billing for an answer that was never produced is the failure the
    charge-after-delivery ordering exists to prevent, and a mid-stream fault
    must not route around it.
    """
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _failing_retrieval(monkeypatch, RuntimeError("boom"))

    list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert charged == []


def test_the_failure_message_does_not_leak_the_exception(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The refusal text must not carry the underlying exception.

    An exception string on a user-facing surface is a disclosure risk, and
    the engineer reading it cannot act on an OpenSearch error anyway.
    """
    _failing_retrieval(
        monkeypatch, RuntimeError("connection to opensearch:9200 refused; index secret-idx")
    )

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    rendered = json.dumps([event.data for event in events])
    assert "opensearch" not in rendered.lower()
    assert "secret-idx" not in rendered


def test_the_failure_says_nothing_was_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The one fact the reader can act on: retry without cost."""
    _failing_retrieval(monkeypatch, RuntimeError("boom"))

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert "charged" in events[-1].data["refusal_message"].lower()


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("boom"), ValueError("bad"), TimeoutError(), KeyError("k")],
    ids=["RuntimeError", "ValueError", "TimeoutError", "KeyError"],
)
def test_any_downstream_failure_is_converted(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient, failure: Exception
) -> None:
    """Any exception type terminates the stream cleanly.

    Deliberately broad. Retrieval reaches OpenSearch, generation reaches
    Anthropic and embedding reaches Voyage; this layer cannot enumerate their
    failure modes, and every one of them must still terminate the stream.
    """
    _failing_retrieval(monkeypatch, failure)

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert events[-1].event == "result"


def test_the_first_event_is_still_emitted_before_the_failure(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The opening event survives a later failure.

    `retrieving` must survive: it is what proves to the client that the
    request was accepted, and it is already on the wire when the fault hits.
    """
    _failing_retrieval(monkeypatch, RuntimeError("boom"))

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert events[0].event == "retrieving"


def test_a_successful_turn_is_unaffected(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The guard must not have swallowed the normal path."""
    _retrieving(monkeypatch, [_passage()])

    events = list(
        diagnostics_domain.stream_diagnosis(
            session=cast(Session, _FakeSession()), user=_user(), request=_request()
        )
    )

    assert [event.event for event in events] == ["retrieving", "generated", "result"]
    assert events[-1].data["diagnosis"] is not None


# --- the conversation history list (FE-011 / GET /sessions) ------------------
#
# This reads one tenant's rows out of a shared table, which puts it in the same
# class as every other tenant-scoped read: the failure that matters is not a
# wrong sort order but one tenant seeing another's conversations. Most of what
# follows is about isolation and about the page boundary, because a keyset
# cursor that is subtly wrong silently drops rows in the middle of a list
# rather than erroring.


def _seed_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    questions: list[str],
    started: datetime | None = None,
    spacing: timedelta = timedelta(minutes=1),
    equipment_models: list[str | None] | None = None,
) -> uuid.UUID:
    """Create a conversation with turns at controlled timestamps.

    Args:
        db: Open database session.
        tenant_id: Owning tenant.
        questions: One question per turn, in conversation order.
        started: When the first turn happened; defaults to a fixed past time.
        spacing: Gap between consecutive turns.
        equipment_models: Per-turn recorded equipment, positionally matched to
            ``questions``; ``None`` throughout when omitted.

    Returns:
        The new session id.

    Timestamps are set explicitly rather than left to `server_default`, because
    ordering is the property under test and rows created in the same
    transaction otherwise share a timestamp to the microsecond.
    """
    base = started or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    conversation = DiagnosticSessionRow(tenant_id=tenant_id, created_at=base, updated_at=base)
    db.add(conversation)
    db.flush()

    for index, question in enumerate(questions):
        stamp = base + spacing * index
        db.add(
            DiagnosticTurnRow(
                session_id=conversation.id,
                position=index + 1,
                question=question,
                answer=f"answer to {question}",
                refused=False,
                confidence=0.8,
                equipment_model=(equipment_models[index] if equipment_models is not None else None),
                created_at=stamp,
                updated_at=stamp,
            )
        )
    db.flush()
    return conversation.id


def _other_tenant(db: Session) -> uuid.UUID:
    """Create a second tenant, to prove rows do not leak across the boundary."""
    tenant = TenantRow(name="Other", slug=f"{_DB_SLUG_PREFIX}{uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant.id


# --- tenant isolation, which is the security property ------------------------


@requires_db
def test_another_tenants_sessions_are_never_listed(db: Session, db_user: CurrentUser) -> None:
    """The failure this endpoint must not have.

    One tenant reading another's conversation list would expose the equipment
    they run and the faults they are having, which is commercially sensitive
    on its own even before anyone opens a session.
    """
    mine = _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["my question"])
    _seed_session(db, tenant_id=_other_tenant(db), questions=["their secret question"])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert [row.id for row in page.sessions] == [str(mine)]


@requires_db
def test_another_tenants_titles_do_not_leak(db: Session, db_user: CurrentUser) -> None:
    """Not even as a title.

    The title is the engineer's own words about their fault; leaking it is a
    disclosure whether or not the session id comes with it.
    """
    _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["mine"])
    _seed_session(db, tenant_id=_other_tenant(db), questions=["their secret question"])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert all("secret" not in row.title for row in page.sessions)


@requires_db
def test_a_cursor_cannot_be_used_to_page_into_another_tenant(
    db: Session, db_user: CurrentUser
) -> None:
    """A cursor carries no authority of its own.

    A cursor is client-supplied input, so it is worth proving it carries no
    authority of its own: replaying one against a different caller must still
    only ever return that caller's rows.
    """
    other_tenant = _other_tenant(db)
    for index in range(3):
        _seed_session(
            db,
            tenant_id=other_tenant,
            questions=[f"their question {index}"],
            started=datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(hours=index),
        )
    _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["mine"])

    other_user = CurrentUser(
        id=str(uuid.uuid4()),
        email="other@example.com",
        tenant_id=str(other_tenant),
        roles=frozenset({Role.ENGINEER}),
    )
    their_page = diagnostics_domain.list_sessions(session=db, user=other_user, limit=1)
    assert their_page.next_cursor is not None

    # The other tenant's own cursor, replayed by our caller.
    mine = diagnostics_domain.list_sessions(session=db, user=db_user, cursor=their_page.next_cursor)

    assert all("their" not in row.title for row in mine.sessions)


# --- ordering ----------------------------------------------------------------


@requires_db
def test_sessions_are_ordered_by_most_recent_activity(db: Session, db_user: CurrentUser) -> None:
    """Most recent first, which is what the spec asks for."""
    tenant = uuid.UUID(db_user.tenant_id)
    old = _seed_session(
        db, tenant_id=tenant, questions=["old"], started=datetime(2026, 1, 1, tzinfo=UTC)
    )
    new = _seed_session(
        db, tenant_id=tenant, questions=["new"], started=datetime(2026, 3, 1, tzinfo=UTC)
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert [row.id for row in page.sessions] == [str(new), str(old)]


@requires_db
def test_a_session_sorts_by_its_newest_turn_not_its_creation(
    db: Session, db_user: CurrentUser
) -> None:
    """The bug this ordering exists to avoid.

    Appending a turn does not write to `diagnostic_sessions`, so that row's
    `updated_at` still holds the moment the conversation was opened. Sorting by
    it would push a session worked on all afternoon below one opened yesterday
    and abandoned — exactly the session the engineer is looking for.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    # Opened first, but still being worked on.
    busy = _seed_session(
        db,
        tenant_id=tenant,
        questions=["q1", "q2", "q3"],
        started=datetime(2026, 1, 1, tzinfo=UTC),
        spacing=timedelta(days=30),
    )
    # Opened later, then abandoned.
    abandoned = _seed_session(
        db, tenant_id=tenant, questions=["only"], started=datetime(2026, 1, 15, tzinfo=UTC)
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert [row.id for row in page.sessions] == [str(busy), str(abandoned)]


@requires_db
def test_last_activity_is_reported_as_updated_at(db: Session, db_user: CurrentUser) -> None:
    """So the sidebar can show when the conversation was last worked on."""
    _seed_session(
        db,
        tenant_id=uuid.UUID(db_user.tenant_id),
        questions=["a", "b"],
        started=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
        spacing=timedelta(hours=3),
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].updated_at == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


# --- what each row carries ---------------------------------------------------


@requires_db
def test_the_title_is_the_first_question(db: Session, db_user: CurrentUser) -> None:
    """By conversation position, not by whichever row came back first.

    The first question is what makes a session recognisable a day later; the
    last one is often a follow-up that means nothing out of context.
    """
    _seed_session(
        db,
        tenant_id=uuid.UUID(db_user.tenant_id),
        questions=["ACS880 undervoltage trip", "and what about the fan?"],
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].title == "ACS880 undervoltage trip"


@requires_db
def test_a_long_question_is_truncated(db: Session, db_user: CurrentUser) -> None:
    """A pasted fault log is a legitimate question and an illegitimate list row."""
    _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["x" * 5000])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert len(page.sessions[0].title) < 200
    assert page.sessions[0].title.endswith("\u2026")


@requires_db
def test_a_session_with_no_turns_still_appears(db: Session, db_user: CurrentUser) -> None:
    """A conversation exists from the moment a question starts.

    Dropping it would make the sidebar lose the session the engineer is looking
    at while the first answer is still streaming.
    """
    empty = _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=[])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert [row.id for row in page.sessions] == [str(empty)]
    assert page.sessions[0].turn_count == 0
    assert page.sessions[0].title


@requires_db
def test_the_turn_count_is_reported(db: Session, db_user: CurrentUser) -> None:
    _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["a", "b", "c"])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].turn_count == 3


@requires_db
def test_the_turn_count_is_not_multiplied_by_the_join(db: Session, db_user: CurrentUser) -> None:
    """The classic aggregate-over-join bug.

    Counting the joined rows rather than the turns would report a plausible
    but wrong number, and nothing else in the response would look odd.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    _seed_session(db, tenant_id=tenant, questions=["a", "b", "c", "d"])
    _seed_session(db, tenant_id=tenant, questions=["e"])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert sorted(row.turn_count for row in page.sessions) == [1, 4]


# --- pagination --------------------------------------------------------------


@requires_db
def test_a_page_is_limited(db: Session, db_user: CurrentUser) -> None:
    tenant = uuid.UUID(db_user.tenant_id)
    for index in range(5):
        _seed_session(
            db,
            tenant_id=tenant,
            questions=[f"q{index}"],
            started=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
        )

    page = diagnostics_domain.list_sessions(session=db, user=db_user, limit=2)

    assert len(page.sessions) == 2
    assert page.next_cursor is not None


@requires_db
def test_paging_visits_every_session_exactly_once(db: Session, db_user: CurrentUser) -> None:
    """The property that makes a cursor correct.

    A keyset cursor that is subtly wrong does not raise — it silently repeats
    or skips rows at the page boundary, which a sidebar renders as a history
    that is missing yesterday's session or shows it twice.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    expected = {
        str(
            _seed_session(
                db,
                tenant_id=tenant,
                questions=[f"q{index}"],
                started=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
            )
        )
        for index in range(7)
    }

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # bounded, so a cursor that never advances fails here
        page = diagnostics_domain.list_sessions(session=db, user=db_user, limit=2, cursor=cursor)
        seen.extend(row.id for row in page.sessions)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert len(seen) == len(set(seen)), "a session was returned on two pages"
    assert set(seen) == expected


@requires_db
def test_sessions_sharing_a_timestamp_are_not_lost_across_pages(
    db: Session, db_user: CurrentUser
) -> None:
    """Why the cursor carries the id as well as the timestamp.

    Two sessions can share a last-activity timestamp to the microsecond. A
    cursor comparing the timestamp alone either re-serves the whole tied group
    forever or steps past it, dropping rows in the middle of the list.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    tied = datetime(2026, 2, 2, 10, 0, tzinfo=UTC)
    expected = {
        str(_seed_session(db, tenant_id=tenant, questions=[f"tied {i}"], started=tied))
        for i in range(4)
    }

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(8):
        page = diagnostics_domain.list_sessions(session=db, user=db_user, limit=2, cursor=cursor)
        seen.extend(row.id for row in page.sessions)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate over tied timestamps"
    assert set(seen) == expected
    assert len(seen) == len(set(seen))


@requires_db
def test_the_last_page_offers_no_cursor(db: Session, db_user: CurrentUser) -> None:
    """The final page offers no cursor.

    A cursor on the final page would hand the sidebar a fetch that returns
    nothing, which reads as a loading row that never resolves.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    for index in range(2):
        _seed_session(
            db,
            tenant_id=tenant,
            questions=[f"q{index}"],
            started=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
        )

    page = diagnostics_domain.list_sessions(session=db, user=db_user, limit=2)

    assert len(page.sessions) == 2
    assert page.next_cursor is None


@requires_db
def test_an_empty_history_is_an_empty_page(db: Session, db_user: CurrentUser) -> None:
    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions == []
    assert page.next_cursor is None


@requires_db
def test_the_limit_is_capped(db: Session, db_user: CurrentUser) -> None:
    """The page size is capped server-side.

    An unbounded limit lets one request read an entire tenant's history into
    memory, which is the usual shape of an accidental denial of service.

    Asserted on the SQL rather than by seeding 101 conversations: a row-count
    assertion over a handful of rows holds whether or not the cap exists, and a
    mutation removing `min(...)` survived exactly that test. This reads the
    LIMIT the query actually carries.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    _seed_session(db, tenant_id=tenant, questions=["a"])

    executed: list[tuple[str, Any]] = []

    @sa_event.listens_for(db.get_bind(), "before_cursor_execute")
    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        del conn, cursor, context, executemany
        executed.append((statement, parameters))

    try:
        diagnostics_domain.list_sessions(session=db, user=db_user, limit=10_000)
    finally:
        sa_event.remove(db.get_bind(), "before_cursor_execute", record)

    # The LIMIT is a bound parameter, so it lives in the parameters rather than
    # in the SQL text.
    listing = [
        parameters
        for statement, parameters in executed
        if "diagnostic_sessions" in statement and "LIMIT" in statement
    ]
    assert listing, "no listing query was issued"
    first = listing[0]
    bound = list(first.values()) if isinstance(first, dict) else list(first)
    # `_SESSIONS_PAGE_MAX + 1`: one extra row is fetched to detect a next page,
    # so the cap reaches SQL as 101 rather than 100.
    assert 10_000 not in bound
    assert 101 in bound


@requires_db
def test_a_non_positive_limit_is_refused(db: Session, db_user: CurrentUser) -> None:
    with pytest.raises(ValidationError):
        diagnostics_domain.list_sessions(session=db, user=db_user, limit=0)


# --- cursor validation, since it is client-supplied input --------------------


@pytest.mark.parametrize(
    "cursor",
    ["not-base64!!", "", "abc", base64.urlsafe_b64encode(b"no-separator").decode()],
    ids=["not-base64", "empty", "short", "no-separator"],
)
def test_a_malformed_cursor_is_refused(cursor: str) -> None:
    """A malformed cursor is refused.

    It is decoded straight into a SQL comparison, so it is validated like
    any other external input rather than trusted because we issued one once.
    """
    with pytest.raises(ValidationError, match="cursor"):
        diagnostics_domain._decode_session_cursor(cursor)


def test_a_cursor_round_trips() -> None:
    stamp = datetime(2026, 4, 4, 16, 30, tzinfo=UTC)
    identifier = uuid.uuid4()

    decoded = diagnostics_domain._decode_session_cursor(
        diagnostics_domain._session_cursor(stamp, identifier)
    )

    assert decoded == (stamp, identifier)


def test_a_cursor_with_a_bad_uuid_is_refused() -> None:
    cursor = base64.urlsafe_b64encode(b"2026-01-01T00:00:00+00:00|not-a-uuid").decode()

    with pytest.raises(ValidationError):
        diagnostics_domain._decode_session_cursor(cursor)


def test_a_cursor_with_a_bad_timestamp_is_refused() -> None:
    cursor = base64.urlsafe_b64encode(f"never|{uuid.uuid4()}".encode()).decode()

    with pytest.raises(ValidationError):
        diagnostics_domain._decode_session_cursor(cursor)


# --- the recorded equipment context ------------------------------------------
#
# FE-011's acceptance criterion is that selecting a past session restores "its
# context indicator and message history correctly" -- the indicator, not just
# the messages. That indicator is driven by `StructuredDiagnosis.equipment_model`,
# which used to live only on the live response, so a replayed turn came back
# with it unset and the chip reloaded blank. These pin the record-and-replay
# path, because the alternative -- re-deriving a model number from stored prose
# -- is the guess the indicator exists to prevent.


@requires_db
def test_the_equipment_model_is_recorded_on_the_turn(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """What was shown is written down, rather than recomputed later."""
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())

    stored = db.scalars(
        select(DiagnosticTurnRow).where(
            DiagnosticTurnRow.session_id == uuid.UUID(created.session_id)
        )
    ).all()

    assert stored[0].equipment_model == "ACS880"


@requires_db
def test_a_replayed_turn_restores_the_context_indicator(
    db: Session, db_user: CurrentUser, monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """The acceptance criterion itself.

    `contextFromResponse` in the web client reads exactly this field. With it
    unset the chip comes back empty, and the engineer has to restate the
    equipment they already told it about -- which is the friction the chip was
    built to remove.
    """
    _retrieving(monkeypatch, [_passage()])
    created = diagnostics_domain.run_diagnosis(session=db, user=db_user, request=_request())

    history = diagnostics_domain.get_session(
        session=db, user=db_user, session_id=created.session_id
    )
    replayed = history.turns[0].response

    assert replayed.diagnosis is not None
    assert replayed.diagnosis.equipment_model == "ACS880"


@requires_db
def test_a_turn_with_no_equipment_replays_without_one(db: Session, db_user: CurrentUser) -> None:
    """No guess, and no crash.

    Turns written before the column existed carry NULL, and a conversation that
    never identified a unit is a normal thing. Either way the sidebar shows no
    context rather than inventing one.
    """
    session_id = _seed_session(
        db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["something vague"]
    )

    history = diagnostics_domain.get_session(session=db, user=db_user, session_id=str(session_id))
    replayed = history.turns[0].response

    assert replayed.diagnosis is not None
    assert replayed.diagnosis.equipment_model is None


@requires_db
def test_the_summary_reports_the_conversations_equipment(db: Session, db_user: CurrentUser) -> None:
    """So the sidebar can show brand/model per entry, as the spec asks."""
    session_id = _seed_session(
        db,
        tenant_id=uuid.UUID(db_user.tenant_id),
        questions=["undervoltage trip"],
        equipment_models=["ACS880"],
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].id == str(session_id)
    assert page.sessions[0].equipment_model == "ACS880"


@requires_db
def test_the_summary_reports_the_most_recent_equipment(db: Session, db_user: CurrentUser) -> None:
    """The most recently identified equipment wins.

    An engineer who moved to another unit mid-session is looking for the unit
    they moved to.

    Pinned because `MAX` over a text column would pass a single-value test and
    then return whichever model sorts highest alphabetically -- here `VLT`
    happens to sort after `ACS880`, so only a reversed case catches it.
    """
    _seed_session(
        db,
        tenant_id=uuid.UUID(db_user.tenant_id),
        questions=["first", "second"],
        equipment_models=["VLT2800", "ACS880"],
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].equipment_model == "ACS880"


@requires_db
def test_a_later_turn_without_equipment_does_not_erase_it(
    db: Session, db_user: CurrentUser
) -> None:
    """A later turn without equipment does not erase it.

    A follow-up like "and the fan?" identifies no unit, and must not blank
    the chip for a conversation that had already established one.
    """
    _seed_session(
        db,
        tenant_id=uuid.UUID(db_user.tenant_id),
        questions=["ACS880 trips", "and the fan?"],
        equipment_models=["ACS880", None],
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].equipment_model == "ACS880"


@requires_db
def test_a_session_that_never_identified_equipment_reports_none(
    db: Session, db_user: CurrentUser
) -> None:
    _seed_session(db, tenant_id=uuid.UUID(db_user.tenant_id), questions=["vague"])

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    assert page.sessions[0].equipment_model is None


@requires_db
def test_equipment_does_not_leak_between_sessions(db: Session, db_user: CurrentUser) -> None:
    """The correlated subquery must be correlated.

    An uncorrelated one would return the same model for every row, putting one
    conversation's equipment on all of them.
    """
    tenant = uuid.UUID(db_user.tenant_id)
    _seed_session(
        db,
        tenant_id=tenant,
        questions=["a"],
        equipment_models=["ACS880"],
        started=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _seed_session(
        db,
        tenant_id=tenant,
        questions=["b"],
        equipment_models=[None],
        started=datetime(2026, 2, 1, tzinfo=UTC),
    )

    page = diagnostics_domain.list_sessions(session=db, user=db_user)

    by_title = {row.title: row.equipment_model for row in page.sessions}
    assert by_title == {"a": "ACS880", "b": None}
