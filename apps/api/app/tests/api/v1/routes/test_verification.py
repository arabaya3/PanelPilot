"""Tests for `app/api/v1/routes/verification.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The queue's behaviour is exercised against a real database in
``app/tests/domain/test_verification_queue.py``. What is under test here is the
HTTP contract: status codes, the shape of the body, and the authorisation
gate on the lead-only view.

The domain is stubbed rather than run, deliberately. A route test that also
exercises the database would fail for two unrelated reasons and tell you
neither; and the one behaviour that genuinely belongs to this layer — mapping a
single ``QueueError`` onto three different status codes — is invisible if the
domain never raises.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import verification as verification_route
from app.core.errors import NotFoundError, ValidationError, install_exception_handlers
from app.domain import corpus_maintenance as maintenance_domain
from app.domain import promotion as promotion_domain
from app.domain import verification_queue as queue_domain
from app.domain.verification_queue import QueueError
from app.models.schemas.auth import CurrentUser, Role
from app.models.tables.ingestion import StaleDocumentRow

_TENANT_ID = str(uuid.UUID(int=7))
_USER_ID = str(uuid.UUID(int=1))
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class _Row:
    """The subset of a queue row the projection reads."""

    def __init__(
        self,
        *,
        row_id: uuid.UUID,
        chunk_id: str | None = "c1",
        status: str = "pending",
        label: str | None = None,
        assigned_at: datetime | None = NOW,
    ) -> None:
        """Build a stand-in row.

        Args:
            row_id: The item id.
            chunk_id: Which chunk it covers.
            status: Its queue status.
            label: The label applied, if any.
            assigned_at: When it was assigned.
        """
        self.id = row_id
        self.chunk_id = chunk_id
        self.status = status
        self.label = label
        self.assigned_at = assigned_at


def _engineer() -> CurrentUser:
    """Return a caller holding only the engineer role."""
    return CurrentUser(
        id=_USER_ID,
        email="verifier@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )


def _lead() -> CurrentUser:
    """Return a caller holding the reviewer role."""
    return CurrentUser(
        id=_USER_ID,
        email="lead@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset({Role.ENGINEER, Role.REVIEWER}),
    )


class _Session:
    """A session that records whether the route committed."""

    def __init__(self) -> None:
        """Start with nothing committed."""
        self.committed = False

    def commit(self) -> None:
        """Record the commit."""
        self.committed = True


def _client(user_factory: Callable[[], CurrentUser]) -> Iterator[TestClient]:
    """Build a client bound to just this router.

    Args:
        user_factory: Callable returning the authenticated caller.

    Yields:
        A configured test client.
    """
    from app.api import deps
    from app.core.db import get_session

    app = FastAPI()
    app.include_router(verification_route.router, prefix="/verification")
    app.dependency_overrides[deps.get_current_user] = user_factory
    app.dependency_overrides[get_session] = _Session
    # The role check that labelling now carries is decided by the domain's
    # error type and mapped centrally, so the handlers are part of the test.
    install_exception_handlers(app)

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def published(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record promotions instead of reaching OpenSearch.

    A correct label publishes its chunk (ADR 0001); these are route tests, so
    the publication itself is the promotion module's tests' concern.
    """
    published: list[str] = []
    monkeypatch.setattr(
        promotion_domain,
        "promote_chunk",
        lambda **kw: published.append(kw["chunk_id"]),
    )
    return published


@pytest.fixture(name="client")
def _verifier_client() -> Iterator[TestClient]:
    """A client authenticated as an ordinary verifier."""
    yield from _client(_engineer)


@pytest.fixture(name="lead_client")
def _lead_client() -> Iterator[TestClient]:
    """A client authenticated as a lead."""
    yield from _client(_lead)


# --- the verifier's own queue -------------------------------------------------


def test_the_queue_returns_the_callers_items(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=42)
    monkeypatch.setattr(
        queue_domain,
        "queue_for",
        lambda **_: [_Row(row_id=item_id)],
    )

    response = client.get("/verification/queue/me")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(item_id)
    assert body["items"][0]["chunk_id"] == "c1"


def test_each_item_carries_the_text_and_source_under_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verifier was shown a chunk id and asked to judge text never shown."""
    monkeypatch.setattr(queue_domain, "queue_for", lambda **_: [_Row(row_id=uuid.UUID(int=1))])
    asked: list[list[str]] = []

    def staged(chunk_ids: list[str]) -> dict[str, dict[str, object]]:
        asked.append(list(chunk_ids))
        return {
            "c1": {
                "content": "F0001 OVERCURRENT: check the motor cable.",
                "source_url": "https://library.abb.com/acs880.pdf",
                "page": 88,
                "section": "Fault tracing",
            }
        }

    monkeypatch.setattr(queue_domain, "staged_chunks", staged)

    item = client.get("/verification/queue/me").json()["items"][0]

    assert item["content"] == "F0001 OVERCURRENT: check the motor cable."
    assert item["source_url"] == "https://library.abb.com/acs880.pdf"
    assert item["page"] == 88
    assert item["section"] == "Fault tracing"
    assert asked == [["c1"]]  # one read for the batch


def test_an_item_whose_chunk_cannot_be_read_says_so_by_omission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_domain, "queue_for", lambda **_: [_Row(row_id=uuid.UUID(int=1))])
    monkeypatch.setattr(queue_domain, "staged_chunks", lambda _ids: {})

    item = client.get("/verification/queue/me").json()["items"][0]

    assert item["content"] is None
    assert item["source_url"] is None


def test_the_queue_asks_only_for_the_callers_own_items(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The route must pass the authenticated caller's id, not one from the
    # request. Otherwise any verifier could read another's queue by asking.
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> list[_Row]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(queue_domain, "queue_for", _capture)

    client.get("/verification/queue/me")

    assert seen["verifier_id"] == uuid.UUID(_USER_ID)


def test_an_empty_queue_is_an_empty_list_not_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A verifier who has finished their batch is the normal end state, not a
    # 404. Returning an error would make "done" indistinguishable from "broken".
    monkeypatch.setattr(queue_domain, "queue_for", lambda **_: [])

    response = client.get("/verification/queue/me")

    assert response.status_code == 200
    assert response.json() == {"items": []}


# --- labelling ----------------------------------------------------------------


def test_a_label_is_recorded_and_committed(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=42)
    monkeypatch.setattr(
        queue_domain,
        "record_label",
        lambda **_: _Row(row_id=item_id, status="labeled", label="correct"),
    )

    response = lead_client.post(
        f"/verification/items/{item_id}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(item_id),
        "status": "labeled",
        "label": "correct",
    }


def test_an_escalating_label_reports_the_escalated_status(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=42)
    monkeypatch.setattr(
        queue_domain,
        "record_label",
        lambda **_: _Row(row_id=item_id, status="escalated", label="uncertain"),
    )

    response = lead_client.post(
        f"/verification/items/{item_id}/label",
        json={"label": "uncertain", "note": "two passages conflict"},
    )

    assert response.json()["status"] == "escalated"


def test_an_unknown_label_is_rejected_by_the_schema(lead_client: TestClient) -> None:
    # The vocabulary is closed. A client sending "mostly-correct" gets a 422
    # rather than having it stored as a fourth label nobody defined.
    response = lead_client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "mostly-correct", "note": "x"},
    )

    assert response.status_code == 422


def test_a_missing_item_is_a_404(lead_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_: object) -> None:
        raise QueueError("no verification item 123")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = lead_client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 404


def test_labelling_someone_elses_item_is_a_403(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Distinct from 404 on purpose: "not yours" and "does not exist" are
    # different problems for whoever is debugging the client.
    def _raise(**_: object) -> None:
        raise QueueError("item 123 is not assigned to 456")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = lead_client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 403


def test_an_escalating_label_without_a_note_is_a_422(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_: object) -> None:
        raise QueueError("a incorrect label requires a note")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = lead_client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "incorrect", "note": ""},
    )

    assert response.status_code == 422


def test_a_failed_label_is_not_committed(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The commit must sit after the domain call, not before it. A commit on the
    # error path would persist whatever partial state the domain had written
    # before raising.
    def _raise(**_: object) -> None:
        raise QueueError("no verification item 123")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = lead_client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 404


# --- the lead-only escalation view --------------------------------------------


def test_a_lead_can_read_the_escalation_queue(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=99)
    monkeypatch.setattr(
        queue_domain,
        "escalations",
        lambda **_: [_Row(row_id=item_id, status="escalated", label="incorrect")],
    )

    response = lead_client.get("/verification/escalations")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == str(item_id)


def test_an_ordinary_verifier_cannot_read_the_escalation_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AI-012's rule is that escalations are resolved by a lead rather than by
    # whoever raised them, which only holds if the view is restricted. The
    # queue also spans every verifier's work, so it is not the caller's to read.
    called = False

    def _escalations(**_: object) -> list[_Row]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(queue_domain, "escalations", _escalations)

    response = client.get("/verification/escalations")

    assert response.status_code == 403
    # Refused before the query runs, not filtered afterwards.
    assert not called


# --- labelling is clearance ----------------------------------------------------


def test_only_a_reviewer_may_label(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The label is the verdict promotion publishes on, so it takes that role."""
    labelled: list[object] = []
    monkeypatch.setattr(queue_domain, "record_label", lambda **kw: labelled.append(kw))

    response = client.post(
        f"/verification/items/{uuid.UUID(int=7)}/label", json={"label": "correct", "note": ""}
    )

    assert response.status_code == 403
    assert labelled == [], "a non-reviewer's label was recorded"


def test_a_correct_label_publishes_the_chunk(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch, published: list[str]
) -> None:
    item_id = uuid.UUID(int=8)
    monkeypatch.setattr(
        queue_domain,
        "record_label",
        lambda **_: _Row(
            row_id=item_id, chunk_id="doc#0001-abc", status="labeled", label="correct"
        ),
    )

    response = lead_client.post(
        f"/verification/items/{item_id}/label", json={"label": "correct", "note": ""}
    )

    assert response.status_code == 200
    assert published == ["doc#0001-abc"]


# --- stale documents ----------------------------------------------------------

_FLAG_ID = uuid.UUID(int=42)


def _stale_row(**overrides: object) -> StaleDocumentRow:
    """A stale-document flag as the domain returns it."""
    fields: dict[str, object] = {
        "id": _FLAG_ID,
        "source_url": "https://library.abb.com/acs880.pdf",
        "source_id": "abb",
        "reason": "superseded",
        "status": "open",
        "published_hashes": "h1,h2",
        "upstream_hash": "h3",
        "first_flagged_at": NOW,
        "last_checked_at": NOW,
    }
    fields.update(overrides)
    return StaleDocumentRow(**fields)


def test_a_reviewer_lists_open_flags_by_default(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def _list(**kwargs: object) -> list[StaleDocumentRow]:
        asked.append(str(kwargs["status"]))
        return [_stale_row()]

    monkeypatch.setattr(maintenance_domain, "list_stale_documents", _list)

    response = lead_client.get("/verification/stale-documents")

    assert response.status_code == 200
    assert asked == ["open"]
    item = response.json()["items"][0]
    assert item["published_hashes"] == ["h1", "h2"]
    assert (item["reason"], item["upstream_hash"], item["review_note"]) == (
        "superseded",
        "h3",
        None,
    )


def test_the_list_filters_by_status(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def _list(**kwargs: object) -> list[StaleDocumentRow]:
        asked.append(str(kwargs["status"]))
        return []

    monkeypatch.setattr(maintenance_domain, "list_stale_documents", _list)
    assert lead_client.get("/verification/stale-documents?status=dismissed").status_code == 200
    assert lead_client.get("/verification/stale-documents?status=bogus").status_code == 422
    assert asked == ["dismissed"]


def test_an_engineer_cannot_read_the_stale_list(client: TestClient) -> None:
    # The real domain function: the role check is its first act, before it
    # touches the (stub) session.
    response = client.get("/verification/stale-documents")
    assert response.status_code == 403
    assert "reviewer role" in response.json()["detail"]


def test_a_dismissal_is_committed_and_returned(
    lead_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions: list[object] = []

    def _dismiss(**kwargs: object) -> StaleDocumentRow:
        sessions.append(kwargs["session"])
        assert (kwargs["document_id"], kwargs["note"]) == (_FLAG_ID, "cover page only")
        return _stale_row(status="dismissed", reviewed_at=NOW, review_note="cover page only")

    monkeypatch.setattr(maintenance_domain, "dismiss_stale_document", _dismiss)

    response = lead_client.post(
        f"/verification/stale-documents/{_FLAG_ID}/dismiss", json={"note": "cover page only"}
    )

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["review_note"]) == (
        "dismissed",
        "cover page only",
    )
    assert getattr(sessions[0], "committed", False) is True


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NotFoundError("no stale-document flag"), 404),
        (ValidationError("a dismissal needs a note"), 422),
    ],
)
def test_a_refused_dismissal_maps_to_its_status_and_is_not_committed(
    lead_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: int,
) -> None:
    sessions: list[object] = []

    def _dismiss(**kwargs: object) -> StaleDocumentRow:
        sessions.append(kwargs["session"])
        raise error

    monkeypatch.setattr(maintenance_domain, "dismiss_stale_document", _dismiss)

    response = lead_client.post(f"/verification/stale-documents/{_FLAG_ID}/dismiss", json={})

    assert response.status_code == code
    assert getattr(sessions[0], "committed", False) is False
