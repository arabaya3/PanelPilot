"""Tests for `app/domain/recognition.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

What matters here is the composition, not the recogniser (tested in
`app/tests/ai/test_recognition.py`): a bad upload never reaches the model, a
good one is stored before the model is asked, and a model failure costs the
engineer their recognition but never their upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai import recognition as recognition_ai
from app.core.errors import ValidationError
from app.domain import images as images_domain
from app.domain import recognition as recognition_domain
from app.domain.storage import FilesystemObjectStore
from app.models.schemas.images import ImageFormat
from app.models.schemas.recognition import DisplayVerdict

_TENANT = "44444444-4444-4444-4444-444444444444"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60

_REPORT: dict[str, Any] = {
    "verdict": "fault_display",
    "fault_code": {"value": "F0002", "confidence": 0.93},
    "brand": {"value": None, "confidence": 0.0},
    "model": {"value": None, "confidence": 0.0},
    "note": None,
}


class _Block:
    def __init__(self, payload: Any) -> None:
        self.type = "tool_use"
        self.name = recognition_ai.RECOGNITION_TOOL_NAME
        self.input = payload


class _Message:
    def __init__(self, payload: Any) -> None:
        self.content = [_Block(payload)]


class _FakeClient:
    """Records each request and returns a canned report, or raises."""

    def __init__(self, payload: Any = None, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._payload = payload
        self._error = error
        self.messages = self

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _Message(self._payload)


class _Settings:
    llm_model = "test-model"
    generation_model = "test-model"


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "images")


def _wire(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(recognition_domain, "_anthropic_client", lambda: client)
    monkeypatch.setattr(recognition_domain, "get_settings", _Settings)
    return client


# --- the happy path ----------------------------------------------------------


def test_a_readable_display_comes_back_with_its_report(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    _wire(monkeypatch, _FakeClient(_REPORT))

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    assert response.recognition is not None
    assert response.recognition.verdict is DisplayVerdict.FAULT_DISPLAY
    assert response.recognition.fault_code.value == "F0002"


def test_the_image_is_stored_under_the_callers_tenant(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    _wire(monkeypatch, _FakeClient(_REPORT))

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    _, data = images_domain.get_image(store=store, image_id=response.image_id, tenant_id=_TENANT)
    assert data == JPEG


def test_the_model_sees_the_sniffed_format_and_the_configured_model(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    client = _wire(monkeypatch, _FakeClient(_REPORT))

    recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=PNG)

    (call,) = client.calls
    assert call["model"] == "test-model"
    image = call["messages"][0]["content"][0]
    assert image["source"]["media_type"] == ImageFormat.PNG.media_type


def test_an_off_topic_photo_is_reported_as_such_not_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    """A wiring diagram is a successful recognition with a negative verdict."""
    _wire(
        monkeypatch,
        _FakeClient({"verdict": "not_a_fault_display", "note": "a terminal strip"}),
    )

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    assert response.recognition is not None
    assert response.recognition.verdict is DisplayVerdict.NOT_A_FAULT_DISPLAY
    assert response.recognition.fault_code.value is None


# --- a bad upload never reaches the model -----------------------------------


@pytest.mark.parametrize(
    "data",
    [b"", b"#!/bin/sh\necho hi\n" + b"\x00" * 40, JPEG + b"\x00" * images_domain.MAX_IMAGE_BYTES],
    ids=["empty", "not-an-image", "oversized"],
)
def test_an_invalid_upload_raises_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore, data: bytes
) -> None:
    client = _wire(monkeypatch, _FakeClient(_REPORT))

    with pytest.raises(ValidationError):
        recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=data)

    assert client.calls == []


# --- a model failure costs the recognition, never the upload ----------------


def test_an_unreachable_model_still_returns_the_stored_image(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    _wire(monkeypatch, _FakeClient(error=ConnectionError("upstream down")))

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    assert response.recognition is None
    _, data = images_domain.get_image(store=store, image_id=response.image_id, tenant_id=_TENANT)
    assert data == JPEG


def test_a_report_that_does_not_validate_is_dropped_not_half_read(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    """A code reported beside a not-a-display verdict is invented; none is used."""
    _wire(
        monkeypatch,
        _FakeClient(
            {
                "verdict": "not_a_fault_display",
                "fault_code": {"value": "F0001", "confidence": 0.99},
            }
        ),
    )

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    assert response.recognition is None
    assert response.image_id


def test_a_failure_is_logged_with_ids_and_not_the_image(
    monkeypatch: pytest.MonkeyPatch, store: FilesystemObjectStore
) -> None:
    logged: list[tuple[str, dict[str, Any]]] = []

    class _Logger:
        def exception(self, event: str, **fields: Any) -> None:
            logged.append((event, fields))

    monkeypatch.setattr(recognition_domain, "logger", _Logger())
    _wire(monkeypatch, _FakeClient(error=ConnectionError("upstream down")))

    response = recognition_domain.upload_and_recognise(store=store, tenant_id=_TENANT, data=JPEG)

    assert logged == [("recognition.failed", {"tenant_id": _TENANT, "image_id": response.image_id})]
