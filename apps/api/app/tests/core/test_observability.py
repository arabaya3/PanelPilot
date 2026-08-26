"""Tests for `app/core/observability.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The acceptance criterion is that one request's whole lifecycle is traceable by
its correlation id, and that time-to-first-token is visible as its own metric.
Both are asserted against captured log output rather than by reading the code.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog

from app.core import observability
from app.core.logging import get_logger


@pytest.fixture
def captured() -> Iterator[list[dict[str, Any]]]:
    """Capture structlog output for the duration of a test."""
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


# --- one id, threaded through everything ------------------------------------


def test_a_correlation_id_reaches_every_log_line(captured: list[dict[str, Any]]) -> None:
    """The acceptance criterion.

    A line emitted deep inside retrieval must carry the same id as one from
    the edge, or debugging a slow request means correlating timestamps across
    interleaved requests by hand.
    """
    deep_logger = get_logger("some.deep.module")

    with observability.with_correlation_id("abc123") as correlation_id:
        deep_logger.info("retrieval_started")
        observability.record_latency("retrieval", 12.5)
        deep_logger.info("generation_finished")

    assert correlation_id == "abc123"
    assert len(captured) == 3
    assert {entry["correlation_id"] for entry in captured} == {"abc123"}


def test_lines_outside_the_block_carry_no_id(captured: list[dict[str, Any]]) -> None:
    """A leaked id is worse than none: it attributes work to the wrong request."""
    with observability.with_correlation_id("inside"):
        get_logger("m").info("during")
    get_logger("m").info("after")

    assert captured[0]["correlation_id"] == "inside"
    assert "correlation_id" not in captured[1]


def test_nested_blocks_restore_the_outer_id(captured: list[dict[str, Any]]) -> None:
    """Reset, not clear.

    A worker thread reused across requests would otherwise inherit the
    previous request's id, which is worse than no id because it looks right.
    """
    with observability.with_correlation_id("outer"):
        with observability.with_correlation_id("inner"):
            get_logger("m").info("nested")
        get_logger("m").info("back_outside")

    assert captured[0]["correlation_id"] == "inner"
    assert captured[1]["correlation_id"] == "outer"


def test_an_id_is_generated_when_none_is_supplied() -> None:
    with observability.with_correlation_id() as generated:
        assert generated
        assert observability.current_correlation_id() == generated


def test_two_requests_get_different_ids() -> None:
    with observability.with_correlation_id() as first:
        pass
    with observability.with_correlation_id() as second:
        pass
    assert first != second


def test_the_current_id_is_none_outside_a_block() -> None:
    assert observability.current_correlation_id() is None


# --- a supplied id is not trusted -------------------------------------------


def test_a_clean_supplied_id_is_kept() -> None:
    """So one user action traces end to end across services."""
    assert observability.sanitise_correlation_id("req-123_abc") == "req-123_abc"


@pytest.mark.parametrize(
    "hostile",
    [
        "id with spaces",
        'id"with"quotes',
        "id\nwith\nnewlines",
        "id\x00with\x00nulls",
        '{"injected": "json"}',
        "../../etc/passwd",
    ],
)
def test_a_hostile_id_is_replaced_not_used(hostile: str) -> None:
    """It lands in every log line for the request.

    A newline in a log line forges a second entry; an unbounded string is a
    way to fill log storage.
    """
    result = observability.sanitise_correlation_id(hostile)
    assert result != hostile
    assert set(result) <= observability._SAFE_CORRELATION_ID


def test_an_overlong_id_is_truncated_or_replaced() -> None:
    result = observability.sanitise_correlation_id("a" * 500)
    assert len(result) <= observability._MAX_CORRELATION_ID


def test_a_hostile_id_does_not_reject_the_request() -> None:
    """Refusing would turn a malformed header into an outage.

    The request is still perfectly traceable under an id we chose.
    """
    assert observability.sanitise_correlation_id("!!! bad !!!")


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_absent_id_yields_a_fresh_one(empty: str | None) -> None:
    assert observability.sanitise_correlation_id(empty)


# --- latency -----------------------------------------------------------------


def test_a_stage_records_its_duration(captured: list[dict[str, Any]]) -> None:
    with observability.timed("retrieval", passages=3):
        time.sleep(0.01)

    entry = captured[-1]
    assert entry["stage"] == "retrieval"
    assert entry["duration_ms"] >= 10
    assert entry["passages"] == 3
    assert entry["failed"] is False


def test_a_failing_stage_still_records(captured: list[dict[str, Any]]) -> None:
    """The most interesting latency measurement there is.

    A timer that only fires on success loses exactly the timeouts.
    """
    with pytest.raises(RuntimeError), observability.timed("generation"):
        raise RuntimeError("upstream is down")

    entry = captured[-1]
    assert entry["stage"] == "generation"
    assert entry["failed"] is True


def test_a_failing_stage_does_not_swallow_the_error(captured: list[dict[str, Any]]) -> None:
    """Observability must never change control flow."""
    with pytest.raises(ValueError, match="boom"), observability.timed("s"):
        raise ValueError("boom")


# --- time to first token -----------------------------------------------------


def test_first_token_is_measured_separately_from_total(captured: list[dict[str, Any]]) -> None:
    """Perceived speed is driven by the first, and one total hides it."""
    timer = observability.StreamTimer("diagnosis")
    time.sleep(0.01)
    timer.mark_event()
    time.sleep(0.02)
    timer.mark_event()
    timer.finish()

    entry = captured[-1]
    assert entry["first_token_ms"] >= 10
    assert entry["total_ms"] > entry["first_token_ms"]
    assert entry["events"] == 2


def test_only_the_first_event_fixes_the_metric() -> None:
    """A second "first" would overwrite the number this class exists to capture."""
    timer = observability.StreamTimer()
    timer.mark_event()
    first = timer.first_token_ms
    time.sleep(0.01)
    timer.mark_event()
    assert timer.first_token_ms == first


def test_a_stream_that_sent_nothing_reports_no_first_token(captured: list[dict[str, Any]]) -> None:
    """Not its total duration.

    Reporting the total there would make a completely failed request look
    like a merely slow one, which is the opposite of what a reader needs.
    """
    timer = observability.StreamTimer()
    time.sleep(0.01)
    timer.finish(failed=True)

    entry = captured[-1]
    assert entry["first_token_ms"] is None
    assert entry["total_ms"] >= 10
    assert entry["failed"] is True


def test_the_stream_metric_is_its_own_event(captured: list[dict[str, Any]]) -> None:
    """So it can be aggregated without picking it out of general latency."""
    observability.StreamTimer().finish()
    assert captured[-1]["event"] == "stream_latency"


# --- content stays out of the logs -------------------------------------------


def test_recording_latency_takes_only_metadata(captured: list[dict[str, Any]]) -> None:
    """Shape, not content.

    Logs are shipped, retained and read by people with no business reading
    someone's fault description.
    """
    observability.record_latency("retrieval", 5.0, passages=3, model="claude-sonnet-5")
    entry = captured[-1]
    assert set(entry) >= {"stage", "duration_ms", "passages", "model"}
    assert all(not isinstance(v, bytes) for v in entry.values())
