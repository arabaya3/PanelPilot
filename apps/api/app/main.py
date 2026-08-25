"""FastAPI application factory.

Composition root: this is the only module that wires framework, config, and
routers together. Keep it boring — behaviour goes in ``app.domain``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, load_settings_or_exit
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging


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

    app = FastAPI(title="PanelPilot API", version="0.1.0", debug=settings.debug)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
