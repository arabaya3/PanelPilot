"""Request-scoped tracing.

One middleware, installed once, so every request is traceable without any
route or domain function knowing about it.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger
from app.core.observability import (
    CORRELATION_HEADER,
    record_latency,
    with_correlation_id,
)

_logger = get_logger(__name__)


def _route_template(request: Request) -> str:
    """Return the templated path for a request.

    Args:
        request: The incoming request.

    Returns:
        The route's template, e.g. ``/diagnostics/{session_id}``, falling back
        to the concrete path when no route matched — a 404 still deserves a
        latency line. The template groups by endpoint; a concrete path embeds
        ids that have no business being aggregation keys.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else request.url.path


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind a correlation id to the request and echo it back.

    Args:
        request: The incoming request.
        call_next: The rest of the stack.

    Returns:
        The response, carrying the correlation id in a header so a client can
        quote it in a bug report and a support engineer can find the exact
        request in the logs.

    Note that nothing here logs the request body, the query string, or any
    header other than the correlation id. The path and method are shape; a
    query string is content, and on this API it can contain a fault
    description.
    """
    supplied = request.headers.get(CORRELATION_HEADER)
    with with_correlation_id(supplied) as correlation_id:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[CORRELATION_HEADER] = correlation_id
            return response
        finally:
            # Recorded even when the handler raised: a request that 500s is
            # the one whose latency is most worth having.
            record_latency(
                "request",
                (time.perf_counter() - started) * 1000,
                method=request.method,
                # `route.path` rather than the concrete URL: the templated form
                # groups by endpoint, and a concrete path can embed a session
                # id that has no business being aggregated on.
                path=_route_template(request),
                status=status,
            )
