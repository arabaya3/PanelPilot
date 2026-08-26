"""Tests for `app/api/v1/routes/diagnostics.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

A round-2 review found the streaming endpoint had no route-level tests at all:
every streaming test lived in the domain layer, used a fake session, and
`list()`-ed the generator eagerly. By construction none of them could see
buffering, headers, status, or what happens when a client disconnects — and a
real billing defect hid in exactly that blind spot. These exercise the HTTP
layer.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import diagnostics as diagnostics_route
from app.core.config import Settings
from app.domain import diagnostics as diagnostics_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.diagnostics import DiagnosticRequest
from app.models.schemas.streaming import DiagnosisEvent

_TENANT_ID = "55555555-5555-5555-5555-555555555555"


def _user() -> CurrentUser:
    return CurrentUser(
        id="user-1",
        email="e@example.com",
        tenant_id=_TENANT_ID,
        roles=frozenset({Role.ENGINEER}),
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client bound to just this router, with auth and the DB stubbed out.

    The domain is exercised elsewhere; what is under test here is the HTTP
    contract — status, media type, headers, and the shape of the body.
    """
    from app.api import deps
    from app.core.db import get_session

    app = FastAPI()
    app.include_router(diagnostics_route.router, prefix="/diagnostics")
    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_session] = lambda: None

    with TestClient(app) as test_client:
        yield test_client


def _events(*names: str) -> list[DiagnosisEvent]:
    built = []
    for name in names:
        data: dict[str, Any] = {"session_id": "s1"} if name == "result" else {}
        built.append(DiagnosisEvent(event=name, data=data))
    return built


def test_the_stream_is_served_as_server_sent_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The media type a browser's EventSource requires.

    Anything else and the client parses it as a single JSON body, which for a
    multi-event stream is a syntax error rather than an answer.
    """
    monkeypatch.setattr(
        diagnostics_domain,
        "stream_diagnosis",
        lambda **_kw: iter(_events("retrieving", "generated", "result")),
    )
    response = client.post("/diagnostics/stream", json={"symptom": "F0001"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_the_stream_disables_proxy_buffering(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without these a proxy delivers every event at once, at the end.

    That is indistinguishable from not streaming, which defeats the point.
    """
    monkeypatch.setattr(
        diagnostics_domain,
        "stream_diagnosis",
        lambda **_kw: iter(_events("result")),
    )
    response = client.post("/diagnostics/stream", json={"symptom": "F0001"})
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_every_event_reaches_the_wire_in_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        diagnostics_domain,
        "stream_diagnosis",
        lambda **_kw: iter(_events("retrieving", "generated", "result")),
    )
    body = client.post("/diagnostics/stream", json={"symptom": "F0001"}).text
    names = [
        line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
    ]
    assert names == ["retrieving", "generated", "result"]


def test_the_route_passes_the_request_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A route that dropped the question would stream an answer to nothing."""
    seen: list[DiagnosticRequest] = []

    def _capture(**kwargs: Any) -> Iterator[DiagnosisEvent]:
        seen.append(kwargs["request"])
        return iter(_events("result"))

    monkeypatch.setattr(diagnostics_domain, "stream_diagnosis", _capture)
    client.post("/diagnostics/stream", json={"symptom": "F0001 overcurrent"})
    assert seen
    assert seen[0].symptom == "F0001 overcurrent"


def test_the_route_acts_for_the_authenticated_caller(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tenant comes from the credential, never from the payload.

    A caller-supplied tenant would be a caller-chosen isolation boundary.
    """
    seen: list[CurrentUser] = []

    def _capture(**kwargs: Any) -> Iterator[DiagnosisEvent]:
        seen.append(kwargs["user"])
        return iter(_events("result"))

    monkeypatch.setattr(diagnostics_domain, "stream_diagnosis", _capture)
    client.post("/diagnostics/stream", json={"symptom": "q", "tenant_id": "other"})
    assert seen
    assert seen[0].tenant_id == _TENANT_ID


def test_a_malformed_request_is_rejected_before_the_domain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation is the route's job; the domain should never see this."""
    called: list[str] = []

    def _record(**_kwargs: Any) -> Iterator[DiagnosisEvent]:
        called.append("x")
        return iter(())

    monkeypatch.setattr(diagnostics_domain, "stream_diagnosis", _record)
    response = client.post("/diagnostics/stream", json={})
    assert response.status_code == 422
    assert called == []


def test_the_stream_endpoint_is_documented_as_event_stream(settings: Settings) -> None:
    """The generated frontend type comes from this schema.

    Left undeclared, OpenAPI records an empty JSON schema for an endpoint that
    emits `text/event-stream`, and the type for the payload this whole feature
    delivers generates as `unknown`.

    Settings are injected rather than read from the environment: the `api
    (test)` CI job runs without any, and a test that needs real configuration
    to check a static schema would fail there for an unrelated reason.
    """
    from app.main import create_app

    schema = create_app(settings).openapi()
    operation = schema["paths"]["/api/v1/diagnostics/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]


# --- time to first token, at the wire ---------------------------------------


def test_the_stream_records_time_to_first_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion: it is visible as its own metric.

    Measured where the frame reaches the transport, not where the domain
    decided to send it — the number that matters is when the engineer stops
    looking at nothing.
    """
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
        monkeypatch.setattr(
            diagnostics_domain,
            "stream_diagnosis",
            lambda **_kw: iter(_events("retrieving", "generated", "result")),
        )
        client.post("/diagnostics/stream", json={"symptom": "F0001"})
    finally:
        structlog.configure(**original)

    metrics = [e for e in entries if e.get("event") == "stream_latency"]
    assert metrics, "the stream emitted no latency metric"
    metric = metrics[-1]
    assert metric["first_token_ms"] is not None
    assert metric["events"] == 3
    # Separate numbers, not one total: perceived speed is the first.
    assert "total_ms" in metric
