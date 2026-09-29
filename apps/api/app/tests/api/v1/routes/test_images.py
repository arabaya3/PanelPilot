"""Tests for `app/api/v1/routes/images.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The route is thin, so what is worth testing here is the HTTP contract and,
specifically, that a lying client gets nowhere: the declared content type and
the filename are both attacker-controlled and neither may influence the
outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import images as images_route
from app.domain import images as images_domain
from app.domain import recognition as recognition_domain
from app.domain.storage import FilesystemObjectStore
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.auth_flows import QuotaStatus
from app.models.schemas.recognition import FaultRecognitionResult

_TENANT = "33333333-3333-3333-3333-333333333333"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


def _user() -> CurrentUser:
    return CurrentUser(
        id="user-1",
        email="e@example.com",
        tenant_id=_TENANT,
        roles=frozenset({Role.ENGINEER}),
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "images")


_REPORT = FaultRecognitionResult.model_validate(
    {"verdict": "fault_display", "fault_code": {"value": "E-17", "confidence": 0.91}}
)


class _Settings:
    llm_model = "test-model"


class _Session:
    """Accepts the charge's commit; the transaction itself is the domain's."""

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


_ROOM = QuotaStatus(questions_used=0, question_limit=5, questions_remaining=5)


@pytest.fixture
def recognised(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """Stub the recogniser so no test here reaches the network.

    Returns the bytes each call saw, so a test can assert the model was or
    was not asked.
    """
    seen: list[bytes] = []

    def _recognise(*_args: Any, data: bytes, **_kwargs: Any) -> FaultRecognitionResult:
        seen.append(data)
        return _REPORT

    monkeypatch.setattr(recognition_domain, "recognise_fault_display", _recognise)
    monkeypatch.setattr(recognition_domain, "_anthropic_client", lambda: object())
    monkeypatch.setattr(recognition_domain, "get_settings", _Settings)
    monkeypatch.setattr(recognition_domain, "get_quota", lambda **_: _ROOM)
    monkeypatch.setattr(recognition_domain, "consume_free_question", lambda **_: _ROOM)
    return seen


@pytest.fixture
def client(store: FilesystemObjectStore, recognised: list[bytes]) -> Iterator[TestClient]:
    from app.api import deps

    app = FastAPI()
    app.include_router(images_route.router, prefix="/images")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[deps.get_object_store] = lambda: store
    # The quota charge is exercised in the domain tests; here it only needs a
    # session to be handed through, and the allowance to have room.
    from app.core.db import get_session

    app.dependency_overrides[get_session] = _Session

    from app.core.errors import install_exception_handlers

    install_exception_handlers(app)

    with TestClient(app) as test_client:
        yield test_client


def test_a_real_photo_is_accepted(client: TestClient) -> None:
    response = client.post("/images", files={"file": ("photo.jpg", JPEG, "image/jpeg")})
    assert response.status_code == 200
    assert response.json()["image_id"]


def test_the_declared_content_type_does_not_decide(client: TestClient) -> None:
    """A real PNG announcing itself as a JPEG is still accepted, as a PNG.

    The declaration is ignored entirely — which is the point. Accepting on the
    header would let a non-image through by relabelling it.
    """
    response = client.post("/images", files={"file": ("x.jpg", PNG, "image/jpeg")})
    assert response.status_code == 200


def test_a_non_image_is_rejected_however_it_is_labelled(client: TestClient) -> None:
    """The failure mode the spec names.

    A shell script called `photo.jpg`, announcing `image/jpeg`. Everything the
    client controls says image; the bytes say otherwise, and the bytes win.
    """
    script = b"#!/bin/sh\nrm -rf /\n" + b"\x00" * 40
    response = client.post("/images", files={"file": ("photo.jpg", script, "image/jpeg")})
    assert response.status_code == 422
    assert "not a JPEG" in response.text


def test_an_oversized_upload_is_rejected(client: TestClient) -> None:
    oversized = JPEG + b"\x00" * images_domain.MAX_IMAGE_BYTES
    response = client.post("/images", files={"file": ("big.jpg", oversized, "image/jpeg")})
    assert response.status_code == 422
    assert "limit" in response.text


def test_an_empty_upload_is_rejected(client: TestClient) -> None:
    response = client.post("/images", files={"file": ("empty.jpg", b"", "image/jpeg")})
    assert response.status_code == 422


def test_a_missing_file_is_a_validation_error(client: TestClient) -> None:
    assert client.post("/images").status_code == 422


def test_the_upload_is_stored_under_the_callers_tenant(
    client: TestClient, store: FilesystemObjectStore, tmp_path: Path
) -> None:
    """The tenant comes from the credential, never from the request.

    A caller-supplied tenant would be a caller-chosen isolation boundary.
    """
    response = client.post("/images", files={"file": ("photo.jpg", JPEG, "image/jpeg")})
    image_id = response.json()["image_id"]
    assert (tmp_path / "images" / _TENANT / f"{image_id}.jpg").is_file()


def test_the_stored_bytes_are_what_was_uploaded(
    client: TestClient, store: FilesystemObjectStore
) -> None:
    response = client.post("/images", files={"file": ("photo.jpg", JPEG, "image/jpeg")})
    _, data = images_domain.get_image(
        store=store, image_id=response.json()["image_id"], tenant_id=_TENANT
    )
    assert data == JPEG


def test_the_response_carries_what_was_recognised(
    client: TestClient, recognised: list[bytes]
) -> None:
    """The shape `apps/web/src/lib/recognition.ts` reads: `recognition` beside `image_id`."""
    response = client.post("/images", files={"file": ("photo.jpg", JPEG, "image/jpeg")})

    assert response.status_code == 200
    body = response.json()
    assert body["recognition"]["verdict"] == "fault_display"
    assert body["recognition"]["fault_code"] == {"value": "E-17", "confidence": 0.91}
    assert recognised == [JPEG]


def test_a_rejected_upload_is_never_shown_to_the_model(
    client: TestClient, recognised: list[bytes]
) -> None:
    client.post("/images", files={"file": ("photo.jpg", b"not an image", "image/jpeg")})
    assert recognised == []


def test_a_recogniser_failure_still_returns_the_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _down(*_args: Any, **_kwargs: Any) -> FaultRecognitionResult:
        raise ConnectionError("upstream down")

    monkeypatch.setattr(recognition_domain, "recognise_fault_display", _down)

    response = client.post("/images", files={"file": ("photo.jpg", JPEG, "image/jpeg")})

    assert response.status_code == 200
    assert response.json()["image_id"]
    assert response.json()["recognition"] is None
