"""Structured logging setup.

One configuration call at startup; every other module uses ``get_logger``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import Processor

#: Libraries whose DEBUG output carries request or response bodies.
QUIET_LOGGERS = ("anthropic", "openai", "httpx", "httpcore")


def quiet_sdk_loggers(level: int | None = None) -> None:
    """Hold the model SDKs' and httpx's loggers (and their children) at INFO or above.

    Called at configure time and again right after each SDK is imported: an
    SDK's import reads ``ANTHROPIC_LOG`` / ``OPENAI_LOG`` and may set its
    logger (and httpx's) to DEBUG, which would undo a level set earlier.

    Args:
        level: The app's level; the root logger's when omitted.
    """
    floor = max(logging.getLogger().level if level is None else level, logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(floor)
    for name, child in list(logging.root.manager.loggerDict.items()):
        if (
            isinstance(child, logging.Logger)
            and name.split(".", 1)[0] in QUIET_LOGGERS
            and logging.NOTSET < child.level < floor
        ):
            child.setLevel(floor)


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
    # The model SDKs and their HTTP client log each request's options, body
    # included, at DEBUG: with LOG_LEVEL=DEBUG that would print every AI
    # intake description, board name and model answer to the log stream.
    # They stay at INFO or above whatever the app's level.
    quiet_sdk_loggers(level)

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
