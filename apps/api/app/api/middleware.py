"""Request-scoped middleware: tracing, body limits, and response headers.

Each is installed once, in ``app.main``, so every request gets it without any
route or domain function knowing it exists.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Awaitable, Callable, Sequence

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.observability import (
    CORRELATION_HEADER,
    record_latency,
    with_correlation_id,
)

_logger = get_logger(__name__)


def _unhandled_error_response(exc: Exception, correlation_id: str) -> Response:
    """Log an unhandled exception and return a generic 500.

    Args:
        exc: The exception nothing else handled.
        correlation_id: The id bound to this request.

    Returns:
        A JSON 500 with a fixed message and the correlation id, which is all
        a client can usefully quote and all support needs to find the logs.

    The exception's text never reaches the response: it can name tables,
    columns, file paths, or echo a query's parameters. Nor does it reach the
    log: a database error's message quotes the statement's bound values,
    which on this API can be a fault description or a password hash. The log
    gets the type and the stack's shape (file, line, function), which is what
    finds the bug, and the correlation id, which finds the request.
    """
    _logger.error(
        "request.unhandled_exception",
        error_type=type(exc).__name__,
        # Not `stack`: structlog renderers reserve that key for a string.
        frames=[
            f"{frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        ],
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "detail": "Internal server error",
            "correlation_id": correlation_id,
        },
        # This response is built outside `SecurityHeadersMiddleware` (the
        # correlation middleware is outermost), so it carries them itself.
        headers={
            CORRELATION_HEADER: correlation_id,
            **{name.decode(): value.decode() for name, value in _SECURITY_HEADERS},
        },
    )


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

    This is also the catch-all for unhandled exceptions. It lives here rather
    than in an ``Exception`` handler on the app because Starlette runs that
    handler outside every middleware — after this one has exited and unbound
    the correlation id — so the 500 it produced could name no request.
    """
    supplied = request.headers.get(CORRELATION_HEADER)
    with with_correlation_id(supplied) as correlation_id:
        started = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception as exc:  # the last line of defence
                response = _unhandled_error_response(exc, correlation_id)
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


class RequestBodyTooLargeError(Exception):
    """Raised from ``receive`` once a streamed body passes its limit.

    Internal to ``BodySizeLimitMiddleware``: whatever the application turns
    it into, the middleware discards that response and sends its own 413.
    """


class BodySizeLimitMiddleware:
    """Refuse request bodies over a size limit, before anything parses them.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the limit has to sit in the
    ``receive`` channel itself, because FastAPI reads and parses the body —
    and spools a multipart upload to a temp file — before any dependency, so
    a check in a dependency or route runs after the bytes are already held.

    Two checks. A declared ``Content-Length`` over the limit is refused
    without reading a byte. A body without one (chunked) is counted as it
    arrives and refused the moment it passes the limit, since a missing
    header must not be a way around the ceiling.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        limits_by_prefix: Sequence[tuple[str, int]] = (),
    ) -> None:
        """Wrap an application.

        Args:
            app: The next ASGI application.
            default_limit: Bytes allowed on any path not listed below.
            limits_by_prefix: ``(path_prefix, bytes)`` pairs for the few
                endpoints that legitimately take more, such as image upload.
                The longest matching prefix wins.
        """
        self.app = app
        self.default_limit = default_limit
        self.limits_by_prefix = sorted(
            limits_by_prefix, key=lambda pair: len(pair[0]), reverse=True
        )

    def limit_for(self, path: str) -> int:
        """Return the byte limit for a request path.

        Args:
            path: The request path.

        Returns:
            The limit of the longest matching prefix, else the default.
        """
        for prefix, limit in self.limits_by_prefix:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply the limit to one HTTP request.

        Args:
            scope: The ASGI connection scope.
            receive: The channel the request body arrives on.
            send: The channel the response leaves on.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope["path"])
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_too_large(send, limit)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise RequestBodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            # Once the limit is passed the application's own reply — a 400
            # for an unreadable body, typically — is the wrong answer, and
            # ours goes out instead.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not response_started:
            await _send_too_large(send, limit)


def _declared_length(scope: Scope) -> int | None:
    """Return the request's declared ``Content-Length``, if usable.

    Args:
        scope: The ASGI connection scope.

    Returns:
        The declared length, or ``None`` when absent or malformed. A
        malformed header is left to the server to reject; counting the body
        as it arrives still bounds it either way.
    """
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_too_large(send: Send, limit: int) -> None:
    """Send a 413 in the API's error shape.

    Args:
        send: The response channel.
        limit: The limit that was exceeded, stated so a client knows the
            ceiling rather than guessing at it.
    """
    response = JSONResponse(
        status_code=413,
        content={
            "error": "RequestBodyTooLarge",
            "detail": f"request body exceeds the {limit}-byte limit for this endpoint",
        },
        # The rest of the body is unread; closing is the only safe reuse.
        headers={"Connection": "close"},
    )
    await response({"type": "http"}, _no_receive, send)


async def _no_receive() -> Message:
    """Stand in for ``receive`` when sending a response that reads nothing.

    Returns:
        A disconnect, which a plain response never asks for anyway.
    """
    return {"type": "http.disconnect"}


# Sent on every response. Each closes a class of browser-side attack that has
# nothing to do with what the endpoint returns, so none is per-route.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    # Stops a browser sniffing a JSON error into HTML and executing it.
    (b"x-content-type-options", b"nosniff"),
    # No API response is meant to be framed; this makes that true.
    (b"x-frame-options", b"DENY"),
    # URLs here can carry session ids; never hand them to another origin.
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeadersMiddleware:
    """Add defensive headers to every response, and ``no-store`` to auth.

    Auth responses carry access and refresh tokens and a trial's claim
    secret. ``Cache-Control: no-store`` keeps them out of every browser and
    intermediary cache, where they would outlive the session that fetched
    them.
    """

    def __init__(self, app: ASGIApp, *, no_store_prefixes: Sequence[str] = ()) -> None:
        """Wrap an application.

        Args:
            app: The next ASGI application.
            no_store_prefixes: Path prefixes whose responses must never be
                cached.
        """
        self.app = app
        self.no_store_prefixes = tuple(no_store_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Decorate one HTTP response.

        Args:
            scope: The ASGI connection scope.
            receive: The request channel.
            send: The response channel.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        no_store = any(scope["path"].startswith(p) for p in self.no_store_prefixes)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [pair for pair in _SECURITY_HEADERS if pair[0] not in present]
                if no_store:
                    # Replaced, not appended: two Cache-Control headers are
                    # combined by a cache, and "public, no-store" is a mess.
                    headers = [(n, v) for n, v in headers if n.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
