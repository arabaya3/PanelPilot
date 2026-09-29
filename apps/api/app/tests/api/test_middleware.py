"""Tests for `app/api/middleware.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    correlation_id_middleware,
)
from app.core.logging import get_logger
from app.core.observability import CORRELATION_HEADER, current_correlation_id


@pytest.fixture
def captured() -> Iterator[list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []

    def _capture(
        _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        entries.append(dict(event_dict))
        raise structlog.DropEvent

    original = structlog.get_config()
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, _capture],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )
    try:
        yield entries
    finally:
        structlog.configure(**original)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.middleware("http")(correlation_id_middleware)

    @app.get("/ok")
    def ok() -> dict[str, str | None]:
        get_logger("handler").info("handling")
        return {"seen": current_correlation_id()}

    @app.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("handler exploded")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_a_request_gets_an_id_the_handler_can_see(client: TestClient) -> None:
    """The id is ambient, so a handler need not accept it as a parameter."""
    body = client.get("/ok").json()
    assert body["seen"]


def test_the_id_is_echoed_back(client: TestClient) -> None:
    """Echo the id back to the caller.

    So a client can quote it in a bug report and support can find the
    exact request in the logs.
    """
    response = client.get("/ok")
    assert response.headers[CORRELATION_HEADER] == response.json()["seen"]


def test_a_supplied_id_is_adopted(client: TestClient) -> None:
    """So one user action traces end to end across services."""
    response = client.get("/ok", headers={CORRELATION_HEADER: "upstream-42"})
    assert response.json()["seen"] == "upstream-42"
    assert response.headers[CORRELATION_HEADER] == "upstream-42"


def test_a_hostile_supplied_id_is_replaced(client: TestClient) -> None:
    """It lands in every log line for the request."""
    response = client.get("/ok", headers={CORRELATION_HEADER: "bad id with spaces"})
    assert response.json()["seen"] != "bad id with spaces"
    assert response.status_code == 200


def test_two_requests_get_different_ids(client: TestClient) -> None:
    first = client.get("/ok").json()["seen"]
    second = client.get("/ok").json()["seen"]
    assert first != second


def test_handler_log_lines_carry_the_id(client: TestClient, captured: list[dict[str, Any]]) -> None:
    """The acceptance criterion at the HTTP boundary."""
    response = client.get("/ok", headers={CORRELATION_HEADER: "trace-me"})
    assert response.status_code == 200
    handler_lines = [e for e in captured if e.get("event") == "handling"]
    assert handler_lines
    assert all(e["correlation_id"] == "trace-me" for e in handler_lines)


def test_request_latency_is_recorded(client: TestClient, captured: list[dict[str, Any]]) -> None:
    client.get("/ok")
    latency = [e for e in captured if e.get("stage") == "request"]
    assert latency
    assert latency[-1]["status"] == 200
    assert latency[-1]["duration_ms"] >= 0


def test_the_templated_path_is_recorded_not_the_concrete_one(
    client: TestClient, captured: list[dict[str, Any]]
) -> None:
    """The template groups by endpoint.

    A concrete path embeds ids that have no business being aggregation keys,
    and on this API can carry a session id.
    """
    client.get("/items/abc-123")
    latency = [e for e in captured if e.get("stage") == "request"][-1]
    assert latency["path"] == "/items/{item_id}"
    assert "abc-123" not in latency["path"]


def test_a_failing_request_still_records_its_latency(
    client: TestClient, captured: list[dict[str, Any]]
) -> None:
    """The request whose latency is most worth having."""
    client.get("/boom")
    latency = [e for e in captured if e.get("stage") == "request"][-1]
    assert latency["status"] == 500


def test_an_unmatched_path_still_records(
    client: TestClient, captured: list[dict[str, Any]]
) -> None:
    """A 404 deserves a latency line too — a flood of them is a signal."""
    client.get("/no-such-route")
    latency = [e for e in captured if e.get("stage") == "request"]
    assert latency


def test_no_query_string_or_body_is_logged(
    client: TestClient, captured: list[dict[str, Any]]
) -> None:
    """The path and method are shape; a query string is content.

    On this API it can contain a fault description, which has no business in
    a log that is shipped and retained.
    """
    client.get("/ok?symptom=the+drive+keeps+tripping")
    for entry in captured:
        rendered = repr(entry)
        assert "tripping" not in rendered
        assert "symptom" not in rendered


# --- the unhandled-exception catch-all -------------------------------------------


def test_an_unhandled_exception_is_a_generic_500(client: TestClient) -> None:
    """The exception's text never reaches the caller."""
    response = client.get("/boom", headers={CORRELATION_HEADER: "trace-500"})
    assert response.status_code == 500
    assert response.json() == {
        "error": "InternalServerError",
        "detail": "Internal server error",
        "correlation_id": "trace-500",
    }
    assert "exploded" not in response.text
    assert response.headers[CORRELATION_HEADER] == "trace-500"


def test_an_unhandled_exception_is_logged_with_its_id_but_not_its_text(
    client: TestClient, captured: list[dict[str, Any]]
) -> None:
    """Exception text can quote SQL parameters — content, in a shipped log."""
    client.get("/boom", headers={CORRELATION_HEADER: "trace-log"})
    errors = [e for e in captured if e.get("event") == "request.unhandled_exception"]
    assert len(errors) == 1
    assert errors[0]["correlation_id"] == "trace-log"
    assert errors[0]["error_type"] == "RuntimeError"
    assert errors[0]["frames"], "the stack's shape is what finds the bug"
    assert "exploded" not in repr(errors[0])


# --- the body size limit ---------------------------------------------------------


class _Echo(BaseModel):
    text: str


@pytest.fixture
def limited() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=100,
        limits_by_prefix=[("/upload", 1000)],
    )

    @app.post("/echo")
    def echo(payload: _Echo) -> dict[str, int]:
        return {"length": len(payload.text)}

    @app.post("/upload")
    async def upload(file: UploadFile = File()) -> dict[str, int]:  # noqa: B008
        return {"size": len(await file.read())}

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as test_client:
        yield test_client


def test_a_normal_request_is_unaffected(limited: TestClient) -> None:
    response = limited.post("/echo", json={"text": "short"})
    assert response.status_code == 200
    assert response.json() == {"length": 5}
    assert limited.get("/ping").status_code == 200


def test_a_declared_oversized_body_is_refused_with_413(limited: TestClient) -> None:
    response = limited.post("/echo", json={"text": "x" * 500})
    assert response.status_code == 413
    assert response.json()["error"] == "RequestBodyTooLarge"
    assert "100-byte" in response.json()["detail"]


def test_a_chunked_oversized_body_is_refused_with_413(limited: TestClient) -> None:
    """No Content-Length must not be a way around the ceiling."""

    def _chunks() -> Iterator[bytes]:
        yield b'{"text": "'
        for _ in range(20):
            yield b"x" * 20
        yield b'"}'

    response = limited.post(
        "/echo", content=_chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"] == "RequestBodyTooLarge"


def test_a_chunked_body_under_the_limit_is_read_normally(limited: TestClient) -> None:
    def _chunks() -> Iterator[bytes]:
        yield b'{"text": '
        yield b'"abc"}'

    response = limited.post(
        "/echo", content=_chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == {"length": 3}


def test_a_listed_prefix_gets_its_own_limit(limited: TestClient) -> None:
    response = limited.post("/upload", files={"file": ("a.bin", b"x" * 500)})
    assert response.status_code == 200
    assert response.json() == {"size": 500}

    too_big = limited.post("/upload", files={"file": ("a.bin", b"x" * 1500)})
    assert too_big.status_code == 413


def test_the_longest_matching_prefix_wins() -> None:
    middleware = BodySizeLimitMiddleware(
        FastAPI(),
        default_limit=1,
        limits_by_prefix=[("/api", 10), ("/api/images", 100)],
    )
    assert middleware.limit_for("/api/images") == 100
    assert middleware.limit_for("/api/images/x") == 100
    assert middleware.limit_for("/api/other") == 10
    # A prefix matches whole path segments, not string starts.
    assert middleware.limit_for("/api/imagesque") == 10
    assert middleware.limit_for("/elsewhere") == 1


# --- security headers --------------------------------------------------------------


@pytest.fixture
def headed() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, no_store_prefixes=["/auth"])

    @app.get("/auth/token")
    def token() -> dict[str, str]:
        return {"token": "t"}

    @app.get("/public")
    def public() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as test_client:
        yield test_client


def test_every_response_carries_the_security_headers(headed: TestClient) -> None:
    for path in ("/public", "/auth/token", "/missing"):
        response = headed.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"


def test_auth_responses_are_never_cached(headed: TestClient) -> None:
    """They carry tokens, which must not outlive the session in a cache."""
    assert headed.get("/auth/token").headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in headed.get("/public").headers
