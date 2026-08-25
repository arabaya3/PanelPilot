"""Structured logging setup.

One configuration call at startup; every other module uses ``get_logger``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import Processor


def configure_logging(*, log_level: str, json_output: bool) -> None:
    """Install the process-wide structlog configuration.

    Both runtimes call this once at startup. Logs go to stdout so the container
    runtime owns collection and rotation; nothing here writes to a file.

    Args:
        log_level: Minimum level to emit, e.g. ``"INFO"``.
        json_output: Emit JSON lines (deployed) instead of console output (local).
    """
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)

    # Route stdlib logging (uvicorn, sqlalchemy) through the same stream so a
    # deployed environment gets one consistent format, not two interleaved ones.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a bound logger for a module.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)
