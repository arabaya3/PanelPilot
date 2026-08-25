"""Worker entrypoint.

The second composition root, alongside ``app.main``. Same package, same
config, same domain layer — different runtime, because batch work and HTTP
requests have nothing in common operationally. See
docs/adr/0002-one-package-two-runtimes.md.

Usage::

    python -m app.worker crawl abb-drives
    python -m app.worker --list
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Run one background job to completion.

    Deliberately runs a single job per process rather than holding a scheduler
    loop: retries, concurrency, and timeouts belong to the platform's scheduler
    (cron, ECS task, k8s Job), which already does them better than we would.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code — ``0`` on success, non-zero on failure.
    """
    _args = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    configure_logging(log_level=settings.log_level, json_output=not settings.debug)
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
