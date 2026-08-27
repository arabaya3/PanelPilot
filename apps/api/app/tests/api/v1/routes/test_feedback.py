"""Tests for `app/api/v1/routes/feedback.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The domain is exercised in `app/tests/domain/test_feedback.py`. What is under
test here is the HTTP contract, and one property that belongs to this layer
alone: a missing turn and another tenant's turn must be indistinguishable from
outside, or the endpoint becomes a probe for which turn ids exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import feedback as feedback_route
from app.domain import feedback as feedback_domain
from app.domain.feedback import FeedbackError
from app.models.schemas.auth import CurrentUser, Role

_TENANT_ID = str(uuid.UUID(int=7))
_USER_ID = str(uuid.UUID(int=1))


class _Flag:
    """The subset of a flag row the route reads."""

    def __init__(self, flag_id: uuid.UUID) -> None:
        """Record the id.

        Args:
            flag_id: The stored flag's id.
        """
        self.id = flag_id


class _Session:
    """A session that records whether the route committed."""

    def __init__(self) -> None:
        """Start uncommitted."""
        self.committed = False

    def commit(self) -> None:
        """Record the commit."""
        self.committed = True


def _user() -> CurrentUser:
    """Return the authenticated caller."""
    return CurrentUser(
        id=_USER_ID,
        email="engineer@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    """A client bound to just this router."""
    from app.api import deps
    from app.core.db import get_session

    app = FastAPI()
    app.include_router(feedback_route.router, prefix="/feedback")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = _Session

    with TestClient(app) as test_client:
        yield test_client


def _payload(**overrides: object) -> dict[str, object]:
    """Build a flag request body.

    Args:
        **overrides: Fields to replace.

    Returns:
        The body.
    """
    body: dict[str, object] = {
        "message_id": str(uuid.UUID(int=42)),
        "reason": "the rating is for 30 C",
        "retrieved": [
            {
                "id": "c1",
                "text": "Rated 16 A at 40 C.",
                "score": 0.9,
                "citation": {
                    "document_id": "doc-1",
                    "document_title": "ABB S200",
                    "manufacturer": "abb",
                    "page": 27,
                    "section": "4.2.1",
                },
            }
        ],
    }
    body.update(overrides)
    return body


def test_a_flag_is_created(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    flag_id = uuid.UUID(int=99)
    monkeypatch.setattr(feedback_domain, "flag_answer", lambda **_: _Flag(flag_id))

    response = client.post("/feedback/flag", json=_payload())

    assert response.status_code == 201
    assert response.json() == {"flag_id": str(flag_id), "queued": True}


def test_the_retrieved_context_is_passed_through_untouched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The point of the whole feature: what the client says the user saw is what
    # gets stored. The route must not re-run retrieval or drop the passages.
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> _Flag:
        seen.update(kwargs)
        return _Flag(uuid.UUID(int=99))

    monkeypatch.setattr(feedback_domain, "flag_answer", _capture)

    client.post("/feedback/flag", json=_payload())

    retrieved = seen["retrieved"]
    assert isinstance(retrieved, list)
    assert len(retrieved) == 1
    assert retrieved[0].text == "Rated 16 A at 40 C."


def test_the_flag_is_attributed_to_the_authenticated_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Taken from the token, never from the body — otherwise a client could
    # flag as somebody else, or on behalf of another tenant.
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> _Flag:
        seen.update(kwargs)
        return _Flag(uuid.UUID(int=99))

    monkeypatch.setattr(feedback_domain, "flag_answer", _capture)

    client.post("/feedback/flag", json=_payload())

    assert seen["tenant_id"] == uuid.UUID(_TENANT_ID)
    assert seen["flagged_by_id"] == uuid.UUID(_USER_ID)


def test_a_flag_without_a_reason_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feedback_domain, "flag_answer", lambda **_: _Flag(uuid.UUID(int=99)))

    response = client.post("/feedback/flag", json=_payload(reason=None))

    assert response.status_code == 201


def test_a_flag_on_a_refusal_carries_no_passages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refusal has nothing retrieved and is exactly the sort of answer worth
    # flagging.
    monkeypatch.setattr(feedback_domain, "flag_answer", lambda **_: _Flag(uuid.UUID(int=99)))

    response = client.post("/feedback/flag", json=_payload(retrieved=[]))

    assert response.status_code == 201


def test_a_missing_turn_is_a_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**_: object) -> None:
        raise FeedbackError("no diagnostic turn 42")

    monkeypatch.setattr(feedback_domain, "flag_answer", _raise)

    response = client.post("/feedback/flag", json=_payload())

    assert response.status_code == 404


def test_another_tenants_turn_is_also_a_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deliberately the same code as a missing turn. A distinct 403 would let a
    # caller enumerate which turn ids exist in other tenants by watching which
    # status came back.
    def _raise(**_: object) -> None:
        raise FeedbackError("turn 42 does not belong to this tenant")

    monkeypatch.setattr(feedback_domain, "flag_answer", _raise)

    response = client.post("/feedback/flag", json=_payload())

    assert response.status_code == 404


def test_an_unbounded_context_is_rejected(client: TestClient) -> None:
    # Client-supplied and written to the database, so it is bounded. Without a
    # cap one request could store an arbitrary amount.
    one = _payload()["retrieved"]
    assert isinstance(one, list)

    response = client.post("/feedback/flag", json=_payload(retrieved=one * 60))

    assert response.status_code == 422


def test_a_missing_message_id_is_rejected(client: TestClient) -> None:
    body = _payload()
    del body["message_id"]

    response = client.post("/feedback/flag", json=body)

    assert response.status_code == 422
