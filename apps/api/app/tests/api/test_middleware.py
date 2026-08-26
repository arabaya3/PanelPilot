"""Tests for `app/api/middleware.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware import correlation_id_middleware
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
