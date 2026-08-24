"""Structured logging setup.

One configuration call at startup; every other module uses ``get_logger``.
"""

from __future__ import annotations

from typing import Any

import structlog


def configure_logging(*, log_level: str, json_output: bool) -> None:
    """Install the process-wide structlog configuration.

    Args:
        log_level: Minimum level to emit, e.g. ``"INFO"``.
        json_output: Emit JSON lines (deployed) instead of console output (local).
    """
    raise NotImplementedError


def get_logger(name: str) -> Any:
    """Return a bound logger for a module.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)
