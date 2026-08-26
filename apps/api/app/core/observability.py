"""Correlation IDs and latency, so "faster than OhmX" is measurable.

**One id, threaded through everything.** A request gets a correlation id at
the edge and every log line for the rest of that request carries it, including
lines emitted deep inside retrieval or generation. Without it, debugging a slow
request in production means correlating timestamps across interleaved requests
by hand.

Context variables rather than a parameter passed down the call stack: the id is
ambient to a request, and threading it through every signature would put an
observability concern in the type of every domain function.

**Time-to-first-token is its own metric.** Perceived speed is driven by how
long an engineer stares at nothing, not by total response time. A response that
takes four seconds but starts in three hundred milliseconds feels fast; one
that takes two seconds and shows nothing until the end feels broken. Averaging
them into one number hides exactly the difference that matters.

**Content never reaches the logs.** Not the engineer's question, not the
answer, not image bytes. Logs are shipped, retained, and read by people who
have no business reading someone's fault description — and a photograph of a
panel can carry a site name or a face. What is logged is shape: lengths, ids,
counts, durations. There is a debug-level escape hatch and it is off by
default, because "temporarily" enabled content logging is how content ends up
in a log retained for a year.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from app.core.logging import get_logger

_logger = get_logger(__name__)

# The header a caller may supply to continue a trace that started upstream —
# a load balancer, a frontend, another service. Accepted rather than always
# generated so one user action traces end to end.
CORRELATION_HEADER = "X-Correlation-ID"

# A supplied id is bounded and sanitised before it is used: it lands in every
# log line for the request, and an unbounded caller-controlled string in a log
# is both an injection vector and a way to blow up log storage.
_MAX_CORRELATION_ID = 64
_SAFE_CORRELATION_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def sanitise_correlation_id(supplied: str | None) -> str:
    """Return a usable correlation id, generating one if needed.

    Args:
        supplied: Whatever arrived in the header, if anything.

    Returns:
        The supplied id when it is safe to use, otherwise a fresh one. An
        unusable id is replaced rather than rejected: refusing the request
        would turn a malformed header into an outage, and the request is
        still perfectly traceable under an id we chose.
    """
    if not supplied:
        return uuid.uuid4().hex
    trimmed = supplied.strip()[:_MAX_CORRELATION_ID]
    if not trimmed or not set(trimmed) <= _SAFE_CORRELATION_ID:
        return uuid.uuid4().hex
    return trimmed


@contextmanager
def with_correlation_id(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation id for the duration of a block.

    Args:
        correlation_id: An id to adopt, or ``None`` to generate one.

    Yields:
        The id in force. Every ``get_logger`` line emitted inside the block
        carries it, including from code that knows nothing about tracing.
    """
    resolved = sanitise_correlation_id(correlation_id)
    tokens = structlog.contextvars.bind_contextvars(correlation_id=resolved)
    try:
        yield resolved
    finally:
        # Reset rather than clear: a worker thread reused across requests would
        # otherwise inherit the previous request's id, which is worse than no
        # id at all because it looks correct.
        structlog.contextvars.reset_contextvars(**tokens)


def current_correlation_id() -> str | None:
    """Return the id bound to this context, if any.

    Returns:
        The current correlation id, or ``None`` outside a traced block.
    """
    bound = structlog.contextvars.get_contextvars()
    value = bound.get("correlation_id")
    return value if isinstance(value, str) else None


def record_latency(stage: str, ms: float, **fields: Any) -> None:
    """Record how long one pipeline stage took.

    Args:
        stage: Which stage, e.g. ``"retrieval"`` or ``"generation"``.
        ms: Duration in milliseconds.
        **fields: Extra dimensions — passage counts, model id, and so on.
            Metadata only; passing content here would defeat the point of
            keeping it out of the logs.
    """
    _logger.info("latency", stage=stage, duration_ms=round(ms, 2), **fields)


@contextmanager
def timed(stage: str, **fields: Any) -> Iterator[None]:
    """Time a block and record it, whether or not it succeeds.

    Args:
        stage: Which stage is being timed.
        **fields: Extra dimensions to record.

    Yields:
        Nothing; the duration is recorded on exit.

        Recorded on failure too, and marked as such. A stage that times out
        is the most interesting latency measurement there is, and a `try`
        that only records success loses exactly those.
    """
    started = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        record_latency(stage, elapsed_ms, failed=failed, **fields)


class StreamTimer:
    """Measures time-to-first-token separately from total time.

    Perceived speed is driven by the first, and a single total hides it. Both
    are recorded on completion so one log line carries the comparison.
    """

    def __init__(self, stage: str = "stream", **fields: Any) -> None:
        """Start timing.

        Args:
            stage: What is being streamed.
            **fields: Extra dimensions to record.
        """
        self._stage = stage
        self._fields = fields
        self._started = time.perf_counter()
        self._first_token_ms: float | None = None
        self._events = 0

    def mark_event(self) -> None:
        """Record that an event reached the client.

        The first call fixes time-to-first-token; later calls only count. A
        second "first" would silently overwrite the number this class exists
        to capture.
        """
        self._events += 1
        if self._first_token_ms is None:
            self._first_token_ms = (time.perf_counter() - self._started) * 1000

    @property
    def first_token_ms(self) -> float | None:
        """Milliseconds until the first event, or ``None`` if none was sent."""
        return self._first_token_ms

    def finish(self, *, failed: bool = False) -> None:
        """Record both timings.

        Args:
            failed: Whether the stream ended badly.

        A stream that produced nothing records ``first_token_ms`` as ``None``
        rather than as its total duration. Reporting the total there would
        make a completely failed request look like a merely slow one.
        """
        total_ms = (time.perf_counter() - self._started) * 1000
        _logger.info(
            "stream_latency",
            stage=self._stage,
            first_token_ms=(
                round(self._first_token_ms, 2) if self._first_token_ms is not None else None
            ),
            total_ms=round(total_ms, 2),
            events=self._events,
            failed=failed,
            **self._fields,
        )
