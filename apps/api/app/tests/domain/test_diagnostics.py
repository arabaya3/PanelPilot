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

import json
import os
import threading
import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFoundError
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.diagnostics import DiagnosticRequest, EquipmentContext
from app.models.schemas.search import Citation, RetrievedPassage
from app.models.tables import calculations, escalation, ingestion  # noqa: F401
from app.models.tables.diagnostics import DiagnosticTurnRow
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
