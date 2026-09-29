"""FastAPI application factory.

Composition root: this is the only module that wires framework, config, and
routers together. Keep it boring — behaviour goes in ``app.domain``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    correlation_id_middleware,
)
from app.api.v1.router import api_router
from app.core.config import Settings, load_settings_or_exit
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.observability import CORRELATION_HEADER


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application instance.

    Args:
        settings: Optional settings override; tests pass a constructed object
            instead of relying on the environment.

    Returns:
        The configured FastAPI application.
    """
    # Shared with app.worker.main so both composition roots fail identically.
    settings = settings or load_settings_or_exit()
    configure_logging(log_level=settings.log_level, json_output=not settings.debug)

    # The interactive docs and the schema are a map of every endpoint and
    # payload. Useful in dev and staging; in prod they only help someone
    # probing the API. `app.openapi()` still works with the routes off, which
    # is what the shared-types generator calls.
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="PanelPilot API",
        version="0.1.0",
        debug=settings.debug,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Starlette inserts each middleware at the FRONT of the stack, so the one
    # added last runs first. Added innermost-first, the order on the wire is:
    #
    #   correlation id -> security headers -> CORS -> body limit -> routes
    #
    # The body limit sits inside CORS so a browser can read its 413; the
    # security headers sit outside CORS so a preflight carries them too.
    prefix = settings.api_v1_prefix
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_request_body_bytes,
        limits_by_prefix=[
            (f"{prefix}/images", settings.max_image_request_body_bytes),
            (f"{prefix}/plc", settings.max_plc_request_body_bytes),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # Auth is a bearer header, never a cookie, so a cross-origin request
        # has no ambient credential to carry. Off, a misconfigured origin
        # list cannot turn into a CSRF against a logged-in browser.
        allow_credentials=False,
        # Everything the API serves and everything the frontend sends; see
        # apps/web/src/lib. `Accept` is CORS-safelisted and needs no entry.
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", CORRELATION_HEADER],
    )
    app.add_middleware(SecurityHeadersMiddleware, no_store_prefixes=[f"{prefix}/auth"])
    # Outermost, so a request is traceable even when CORS, the body limit, or
    # a handler rejects it — the failures worth tracing are exactly the ones
    # that do not reach a route. It is also the catch-all for unhandled
    # exceptions; see `correlation_id_middleware`.
    app.middleware("http")(correlation_id_middleware)

    install_exception_handlers(app)
    app.include_router(api_router, prefix=prefix)
    return app
