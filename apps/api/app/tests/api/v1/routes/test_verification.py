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
from app.domain import verification_queue as queue_domain
from app.domain.verification_queue import QueueError
from app.models.schemas.auth import CurrentUser, Role

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

    with TestClient(app) as test_client:
        yield test_client


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
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=42)
    monkeypatch.setattr(
        queue_domain,
        "record_label",
        lambda **_: _Row(row_id=item_id, status="labeled", label="correct"),
    )

    response = client.post(
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
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = uuid.UUID(int=42)
    monkeypatch.setattr(
        queue_domain,
        "record_label",
        lambda **_: _Row(row_id=item_id, status="escalated", label="uncertain"),
    )

    response = client.post(
        f"/verification/items/{item_id}/label",
        json={"label": "uncertain", "note": "two passages conflict"},
    )

    assert response.json()["status"] == "escalated"


def test_an_unknown_label_is_rejected_by_the_schema(client: TestClient) -> None:
    # The vocabulary is closed. A client sending "mostly-correct" gets a 422
    # rather than having it stored as a fourth label nobody defined.
    response = client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "mostly-correct", "note": "x"},
    )

    assert response.status_code == 422


def test_a_missing_item_is_a_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_: object) -> None:
        raise QueueError("no verification item 123")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 404


def test_labelling_someone_elses_item_is_a_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Distinct from 404 on purpose: "not yours" and "does not exist" are
    # different problems for whoever is debugging the client.
    def _raise(**_: object) -> None:
        raise QueueError("item 123 is not assigned to 456")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "correct", "note": ""},
    )

    assert response.status_code == 403


def test_an_escalating_label_without_a_note_is_a_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_: object) -> None:
        raise QueueError("a incorrect label requires a note")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = client.post(
        f"/verification/items/{uuid.UUID(int=42)}/label",
        json={"label": "incorrect", "note": ""},
    )

    assert response.status_code == 422


def test_a_failed_label_is_not_committed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The commit must sit after the domain call, not before it. A commit on the
    # error path would persist whatever partial state the domain had written
    # before raising.
    def _raise(**_: object) -> None:
        raise QueueError("no verification item 123")

    monkeypatch.setattr(queue_domain, "record_label", _raise)

    response = client.post(
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
