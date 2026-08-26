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

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.diagnostics import DiagnosticRequest, EquipmentContext
from app.models.schemas.search import Citation, RetrievedPassage

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
        self._existing = existing
        self._positions = positions or []

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1

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
        self.tenant_id = "tenant-1"


def _user() -> CurrentUser:
    return CurrentUser(
        id="user-1",
        email="e@example.com",
        tenant_id="tenant-1",
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


def test_the_question_is_charged_before_retrieval(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    """A question asked is a question spent.

    Billing after the fact would let a caller mine the corpus for free by
    asking things that refuse.
    """
    order: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: order.append("charged")
    )

    def _searching(*_args: Any, **_kwargs: Any) -> list[RetrievedPassage]:
        order.append("searched")
        return [_passage()]

    monkeypatch.setattr(diagnostics_domain, "search", _searching)

    diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert order == ["charged", "searched"]


def test_a_refusal_is_still_charged(
    monkeypatch: pytest.MonkeyPatch, wired: _CountingClient
) -> None:
    charged: list[str] = []
    monkeypatch.setattr(
        diagnostics_domain, "consume_free_question", lambda **_kw: charged.append("x")
    )
    _retrieving(monkeypatch, [])
    diagnostics_domain.run_diagnosis(
        session=cast(Session, _FakeSession()), user=_user(), request=_request()
    )
    assert charged == ["x"]


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
