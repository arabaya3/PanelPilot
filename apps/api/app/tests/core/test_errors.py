"""Tests for `app/core/errors.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    NotFoundError,
    NotImplementedYetError,
    PanelPilotError,
    TooManyRequestsError,
    ValidationError,
    install_exception_handlers,
    status_for,
)


class _UnmappedError(PanelPilotError):
    """A deliberate error nobody mapped."""


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/throttled")
    def throttled() -> None:
        raise TooManyRequestsError("slow down — wait 2 minutes", retry_after_seconds=90)

    @app.get("/throttled-without-a-number")
    def throttled_bare() -> None:
        raise TooManyRequestsError("slow down")

    @app.get("/stub")
    def stub() -> None:
        raise NotImplementedYetError("cable sizing is not available yet")

    with TestClient(app) as test_client:
        yield test_client


def test_too_many_requests_is_a_429_with_retry_after(client: TestClient) -> None:
    response = client.get("/throttled")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "90"
    assert response.json() == {
        "error": "TooManyRequestsError",
        "detail": "slow down — wait 2 minutes",
    }


def test_retry_after_is_omitted_when_unknown(client: TestClient) -> None:
    response = client.get("/throttled-without-a-number")
    assert response.status_code == 429
    assert "Retry-After" not in response.headers


def test_a_stub_is_a_501_with_its_reason(client: TestClient) -> None:
    """Not an anonymous 500 that reads as the server breaking."""
    response = client.get("/stub")
    assert response.status_code == 501
    assert response.json()["detail"] == "cable sizing is not available yet"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("x"), HTTPStatus.NOT_FOUND),
        (ValidationError("x"), HTTPStatus.UNPROCESSABLE_ENTITY),
        (TooManyRequestsError("x"), HTTPStatus.TOO_MANY_REQUESTS),
        (NotImplementedYetError("x"), HTTPStatus.NOT_IMPLEMENTED),
        (_UnmappedError("x"), HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
def test_status_for_walks_the_mapping(error: PanelPilotError, status: HTTPStatus) -> None:
    assert status_for(error) is status


def test_too_many_requests_is_not_a_validation_error() -> None:
    """A subclass of ValidationError would inherit its 422 by MRO."""
    assert not issubclass(TooManyRequestsError, ValidationError)
